package com.example.blog;

import static org.assertj.core.api.Assertions.*;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.example.blog.mapper.BlogMapper;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.Statement;
import java.util.*;
import java.util.function.Consumer;
import java.util.stream.Collectors;
import org.apache.ibatis.datasource.pooled.PooledDataSource;
import org.apache.ibatis.mapping.Environment;
import org.apache.ibatis.session.*;
import org.apache.ibatis.transaction.jdbc.JdbcTransactionFactory;
import org.junit.jupiter.api.*;
import org.slf4j.LoggerFactory;

/** Spring 없이 MyBatis 3.5.19 만으로 글의 주장을 확인한다. MySQL 은 README 의 docker 컨테이너(포트 33061). */
class NPlusOneTest {
  static final String URL = "jdbc:mysql://localhost:33061/blog";
  final Logger mapperLog = (Logger) LoggerFactory.getLogger("com.example.blog.mapper");
  final ListAppender<ILoggingEvent> logs = new ListAppender<>();

  @BeforeEach void attach() { mapperLog.setLevel(Level.DEBUG); logs.start(); mapperLog.addAppender(logs); }
  @AfterEach void detach() { mapperLog.detachAppender(logs); }

  // ---------- 도우미 ----------
  static SqlSessionFactory factory(Consumer<Configuration> custom) {
    var env = new Environment("t", new JdbcTransactionFactory(),
        new PooledDataSource("com.mysql.cj.jdbc.Driver", URL, "root", "pw"));
    var cfg = new Configuration(env);
    cfg.getTypeAliasRegistry().registerAliases("com.example.blog");
    custom.accept(cfg);
    cfg.addMapper(BlogMapper.class);
    return new SqlSessionFactoryBuilder().build(cfg);
  }
  static SqlSessionFactory factory() { return factory(c -> {}); }

  static void sql(String... stmts) throws Exception {
    try (Connection c = DriverManager.getConnection(URL, "root", "pw"); Statement s = c.createStatement()) {
      for (String q : stmts) s.execute(q);
    }
  }
  /** blogs 개, 블로그마다 posts 개. id 는 1부터. */
  static void seed(int blogs, int posts) throws Exception {
    sql("DROP TABLE IF EXISTS POST", "DROP TABLE IF EXISTS BLOG",
        "CREATE TABLE BLOG (id BIGINT PRIMARY KEY, title VARCHAR(100))",
        "CREATE TABLE POST (id BIGINT PRIMARY KEY, blog_id BIGINT, subject VARCHAR(100), INDEX (blog_id))");
    try (Connection c = DriverManager.getConnection(URL + "?rewriteBatchedStatements=true", "root", "pw")) {
      var b = c.prepareStatement("INSERT INTO BLOG VALUES (?, ?)");
      var p = c.prepareStatement("INSERT INTO POST VALUES (?, ?, ?)");
      long pid = 1;
      for (long i = 1; i <= blogs; i++) {
        b.setLong(1, i); b.setString(2, "blog " + i); b.addBatch();
        for (int j = 0; j < posts; j++) { p.setLong(1, pid); p.setLong(2, i); p.setString(3, "post " + pid++); p.addBatch(); }
      }
      b.executeBatch(); p.executeBatch();
    }
  }
  long sqlCount() { return logs.list.stream().filter(e -> e.getFormattedMessage().matches("=+>  Preparing:.*")).count(); }
  List<String> lines() { return logs.list.stream().map(e -> e.getLevel() + " " + e.getLoggerName() + " - " + e.getFormattedMessage()).toList(); }
  void dump(String title, int max) { System.out.println("### " + title); lines().stream().limit(max).forEach(System.out::println); }

  // ---------- a) 중첩 select = 1 + N ----------
  @Test void a_nestedSelect_runs_1_plus_N() throws Exception {
    seed(20, 3);
    try (var s = factory().openSession()) {
      List<Blog> blogs = s.getMapper(BlogMapper.class).selectBlogs();
      dump("a) selectBlogs", 8);
      assertThat(blogs).hasSize(20);
      assertThat(blogs.get(0).getPosts()).hasSize(3);
      assertThat(sqlCount()).isEqualTo(21);
      // 부수 발견: column="id" 를 collection 이 쓰면 자동 매핑에서 빠져 Blog.id 가 null
      System.out.println("a) blogs[0].id = " + blogs.get(0).getId() + ", title = " + blogs.get(0).getTitle());
    }
  }

  @Test void a_fixed_blogResult_keeps_id() throws Exception {
    seed(20, 3);
    try (var s = factory().openSession()) {
      List<Blog> blogs = s.getMapper(BlogMapper.class).selectBlogsFixed();
      assertThat(sqlCount()).isEqualTo(21);
      assertThat(blogs).extracting(Blog::getId).doesNotContainNull();
      assertThat(blogs.get(0).getTitle()).isEqualTo("blog 1");
    }
  }

  // ---------- b) DEBUG = SQL, TRACE = 결과 행 ----------
  @Test void b_debug_shows_sql_trace_shows_rows() throws Exception {
    seed(2, 1);
    try (var s = factory().openSession()) {
      s.getMapper(BlogMapper.class).selectBlogPage(10, 0);
    }
    dump("b) DEBUG", 10);
    assertThat(lines()).anyMatch(l -> l.contains("==>  Preparing:"));
    assertThat(lines()).anyMatch(l -> l.contains("==> Parameters:"));
    assertThat(lines()).anyMatch(l -> l.contains("<==      Total: 2"));
    assertThat(lines()).noneMatch(l -> l.contains("Row:"));

    logs.list.clear();
    mapperLog.setLevel(Level.TRACE);
    try (var s = factory().openSession()) {
      s.getMapper(BlogMapper.class).selectBlogPage(10, 0);
    }
    dump("b) TRACE", 10);
    assertThat(logs.list).anyMatch(e -> e.getLevel() == Level.TRACE && e.getFormattedMessage().startsWith("<==        Row: 1, blog 1"));
    assertThat(logs.list).anyMatch(e -> e.getLevel() == Level.TRACE && e.getFormattedMessage().startsWith("<==    Columns:"));
    // 로거 이름 = namespace + "." + statement id
    assertThat(logs.list).allMatch(e -> e.getLoggerName().equals("com.example.blog.mapper.BlogMapper.selectBlogPage"));
  }

  // ---------- c) lazy: 처음 1번, 순회하면 결국 1+N ----------
  @Test void c_lazy_defers_but_iteration_still_N() throws Exception {
    seed(20, 3);
    var f = factory(c -> c.setLazyLoadingEnabled(true));
    assertThat(f.getConfiguration().isAggressiveLazyLoading()).as("aggressiveLazyLoading 기본값").isFalse();
    try (var s = f.openSession()) {
      List<Blog> blogs = s.getMapper(BlogMapper.class).selectBlogs();
      assertThat(sqlCount()).isEqualTo(1);
      blogs.forEach(Blog::getTitle);              // 다른 getter 는 로드 안 함 (aggressive=false)
      assertThat(sqlCount()).isEqualTo(1);
      blogs.forEach(b -> b.getPosts().size());     // 화면 그리듯 순회
      dump("c) lazy 순회", 6);
      assertThat(sqlCount()).isEqualTo(21);
    }
  }

  @Test void c_aggressive_true_loads_on_any_method() throws Exception {
    seed(20, 3);
    var f = factory(c -> { c.setLazyLoadingEnabled(true); c.setAggressiveLazyLoading(true); });
    try (var s = f.openSession()) {
      List<Blog> blogs = s.getMapper(BlogMapper.class).selectBlogs();
      assertThat(sqlCount()).isEqualTo(1);
      blogs.get(0).getTitle();                      // posts 와 무관한 getter
      assertThat(sqlCount()).isEqualTo(2);
    }
  }

  @Test void c_fetchType_lazy_attribute_same() throws Exception {
    // 전역 lazyLoadingEnabled 없이 collection 에 fetchType="lazy" 만
    seed(20, 3);
    try (var s = factory().openSession()) {
      List<Blog> blogs = s.getMapper(BlogMapper.class).selectBlogsFetchLazy();
      assertThat(sqlCount()).isEqualTo(1);
      blogs.forEach(b -> b.getPosts().size());
      assertThat(sqlCount()).isEqualTo(21);
    }
  }

  // ---------- d) join + resultMap = 1번 ----------
  @Test void d_join_resultMap_runs_once() throws Exception {
    seed(20, 3);
    sql("INSERT INTO BLOG VALUES (21, 'empty blog')");
    try (var s = factory().openSession()) {
      List<Blog> blogs = s.getMapper(BlogMapper.class).selectBlogsWithPosts();
      dump("d) join", 4);
      assertThat(sqlCount()).isEqualTo(1);
      assertThat(blogs).hasSize(21);
      assertThat(blogs).filteredOn(b -> b.getId() <= 20).allMatch(b -> b.getPosts().size() == 3);
      Blog empty = blogs.stream().filter(b -> b.getId() == 21).findFirst().orElseThrow();
      assertThat(empty.getPosts()).isEmpty();      // LEFT JOIN 의 null 행으로 빈 Post 를 만들지 않음
      Post p = blogs.get(0).getPosts().get(0);
      assertThat(p.getId()).isNotNull();
      assertThat(p.getSubject()).startsWith("post ");   // columnPrefix 로 post_subject 연결
    }
  }

  @Test void d_without_id_same_result_and_timing() throws Exception {
    seed(2000, 10);  // 20,000 행
    var f = factory();
    try (var s = f.openSession()) {
      var m = s.getMapper(BlogMapper.class);
      mapperLog.setLevel(Level.INFO);
      var a = m.selectBlogsWithPosts(); s.clearCache();
      var b = m.selectBlogsWithPostsNoId(); s.clearCache();
      assertThat(b).hasSize(a.size());
      assertThat(b.stream().mapToInt(x -> x.getPosts().size()).sum()).isEqualTo(20000);
      for (int i = 0; i < 5; i++) { m.selectBlogsWithPosts(); s.clearCache(); m.selectBlogsWithPostsNoId(); s.clearCache(); }
      long withId = 0, noId = 0; int n = 20;
      for (int i = 0; i < n; i++) {
        long t = System.nanoTime(); m.selectBlogsWithPosts(); withId += System.nanoTime() - t; s.clearCache();
        t = System.nanoTime(); m.selectBlogsWithPostsNoId(); noId += System.nanoTime() - t; s.clearCache();
      }
      System.out.printf("d) 20,000행 평균: <id> 있음 %.1f ms, <id> 없음(<result>) %.1f ms%n", withId / 1e6 / n, noId / 1e6 / n);
    }
  }

  @Test void d_id_line_deleted_loses_id_and_merges_same_title() throws Exception {
    seed(0, 0);
    sql("INSERT INTO BLOG VALUES (1,'same'),(2,'same'),(3,'other')",
        "INSERT INTO POST VALUES (1,1,'p1'),(2,2,'p2'),(3,3,'p3')");
    try (var s = factory().openSession()) {
      var m = s.getMapper(BlogMapper.class);
      List<Blog> withId = m.selectBlogsWithPosts();
      List<Blog> deleted = m.selectBlogsWithPostsIdDeleted();
      System.out.println("d) <id> 있음: " + withId.stream().map(b -> b.getId() + ":" + b.getTitle() + b.getPosts().size()).toList());
      System.out.println("d) <id> 줄 삭제: " + deleted.stream().map(b -> b.getId() + ":" + b.getTitle() + b.getPosts().size()).toList());
      assertThat(withId).hasSize(3);
      assertThat(deleted).extracting(Blog::getId).containsOnlyNulls();    // 자동 매핑도 안 됨
      assertThat(deleted).hasSize(2);                                       // 제목 같은 블로그가 하나로 합쳐짐
    }
  }

  // ---------- e) 일대다 join + LIMIT ----------
  /** 그림과 같은 데이터: A(글1,2) B(글3,4) C(글5) */
  static void seedPaging() throws Exception {
    seed(0, 0);
    sql("INSERT INTO BLOG VALUES (1,'A'),(2,'B'),(3,'C')",
        "INSERT INTO POST VALUES (1,1,'글1'),(2,1,'글2'),(3,2,'글3'),(4,2,'글4'),(5,3,'글5')");
  }

  @Test void e_join_limit_breaks_parent_paging() throws Exception {
    seedPaging();
    try (var s = factory().openSession()) {
      List<Blog> blogs = s.getMapper(BlogMapper.class).selectBlogsWithPostsLimit(3);
      System.out.println("e) join LIMIT 3 → " + blogs.stream().map(b -> b.getTitle() + b.getPosts().stream().map(Post::getSubject).toList()).toList());
      assertThat(blogs).extracting(Blog::getTitle).containsExactly("A", "B");   // C 사라짐
      assertThat(blogs.get(1).getPosts()).hasSize(1);                             // B 는 글 하나 빠짐
    }
  }

  @Test void e_page_parent_then_IN() throws Exception {
    seedPaging();
    for (boolean camel : new boolean[] {false, true}) {
      logs.list.clear();
      try (var s = factory(c -> c.setMapUnderscoreToCamelCase(camel)).openSession()) {
        var m = s.getMapper(BlogMapper.class);
        List<Blog> blogs = m.selectBlogPage(3, 0);
        List<Post> posts = blogs.isEmpty() ? List.of()
            : m.selectPostsByBlogIds(blogs.stream().map(Blog::getId).toList());
        System.out.println("e) camel=" + camel + " post.blogId = " + posts.stream().map(Post::getBlogId).toList());
        if (!camel) { assertThat(posts).allMatch(p -> p.getBlogId() == null); continue; }  // blog_id 가 blogId 로 안 들어감
        Map<Long, List<Post>> byBlog = posts.stream().collect(Collectors.groupingBy(Post::getBlogId));
        blogs.forEach(b -> b.setPosts(byBlog.getOrDefault(b.getId(), List.of())));
        dump("e) 부모 페이징 + IN", 8);
        assertThat(sqlCount()).isEqualTo(2);
        assertThat(blogs).extracting(Blog::getTitle).containsExactly("A", "B", "C");
        assertThat(blogs).extracting(b -> b.getPosts().size()).containsExactly(2, 2, 1);
      }
    }
  }

  @Test void e_fixed_IN_groups_by_blogId_without_setting() throws Exception {
    seedPaging();
    try (var s = factory().openSession()) {   // mapUnderscoreToCamelCase 기본값(false)
      var m = s.getMapper(BlogMapper.class);
      // 개정본 본문의 자바 코드 그대로
      List<Blog> blogs = m.selectBlogPage(3, 0);
      if (!blogs.isEmpty()) {
        List<Long> ids = blogs.stream().map(Blog::getId).toList();
        Map<Long, List<Post>> postsByBlog = m.selectPostsByBlogIdsFixed(ids).stream()
            .collect(Collectors.groupingBy(Post::getBlogId));
        blogs.forEach(b -> b.setPosts(postsByBlog.getOrDefault(b.getId(), List.of())));
      }
      assertThat(sqlCount()).isEqualTo(2);
      assertThat(blogs).extracting(b -> b.getTitle() + b.getPosts().size()).containsExactly("A2", "B2", "C1");
    }
  }

  @Test void e_empty_IN_is_sql_error() throws Exception {
    seedPaging();
    try (var s = factory().openSession()) {
      var m = s.getMapper(BlogMapper.class);
      Throwable t = catchThrowable(() -> m.selectPostsByBlogIds(List.of()));
      dump("e) 빈 IN", 3);
      System.out.println("e) 빈 IN 예외: " + t.getClass().getSimpleName() + " / " + t.getCause().getClass().getSimpleName());
      assertThat(t).hasRootCauseInstanceOf(java.sql.SQLSyntaxErrorException.class);
    }
  }
}
