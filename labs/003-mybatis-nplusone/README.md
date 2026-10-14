# 003. MyBatis N+1 (MySQL)

> 001을 실제 MyBatis + MySQL로 다시 잰다. 중첩 select는 21번, join resultMap은 1번, 부모 페이징 + IN은 2번.

## 문제

`<collection select="...">`로 블로그 목록과 글을 매핑하면 블로그 목록 1번 + 블로그마다 1번 = `1 + N`번 쿼리가 나간다.
lazy loading을 켜도 화면에서 목록을 순회하는 순간 결국 N번이 더 나간다.

```xml
<resultMap id="blogResult" type="Blog">
  <collection property="posts" column="id" ofType="Post" select="selectPostsForBlog"/>
</resultMap>
```

## 실험

- 블로그 20개, 블로그당 글 3개 (`<id>` 비교만 2,000 × 10 = 20,000행)
- MyBatis 로그(`==>  Preparing:`) 줄 수로 실제 실행된 SQL 수를 센다 (logback `ListAppender`)
- 비교: 중첩 select / lazy loading / join + resultMap / `<id>` 유무 / 일대다 join + LIMIT / 부모 먼저 페이징 + `foreach` IN
- 테스트 14개: `NPlusOneTest`(Spring 없이 MyBatis만) 13개, `SpringLoggingTest`(`application.yml` 로깅 설정) 1개
- 매퍼는 [`BlogMapper.xml`](src/main/resources/com/example/blog/mapper/BlogMapper.xml)

JDK 21, Maven, Docker가 필요하다. 이 폴더에서:

```bash
docker run -d --name lab-n1-mysql -e MYSQL_ROOT_PASSWORD=pw -e MYSQL_DATABASE=blog -p 33061:3306 mysql:8.4 && until docker exec lab-n1-mysql mysql -uroot -ppw -h127.0.0.1 -e 'SELECT 1' >/dev/null 2>&1; do sleep 1; done && mvn test
docker rm -f lab-n1-mysql   # 끝나면 정리
```

비밀번호 `pw`는 로컬 테스트 컨테이너 전용 기본값이다.

## 결과

| 방식 | 쿼리 수 | 비고 |
|---|---:|---|
| 중첩 select (`collection select`) | 21 | 1 + 20 |
| lazy loading (`lazyLoadingEnabled`, `fetchType="lazy"`) | 1 → 21 | 처음 1번, 목록을 순회하면 21번 |
| `aggressiveLazyLoading=true` | 1 → 2 | posts와 무관한 `getTitle()` 한 번에도 로드 |
| join + resultMap | 1 | 61행 → Blog 21개, 글 없는 블로그는 빈 리스트 |
| 부모 먼저 페이징 + `foreach` IN | 2 | A2·B2·C1 |

그 밖에 확인한 것:

- **`<id>` 유무**: `<id>`를 `<result>`로 바꾸면 결과는 같고 20,000행 기준 평균 21~22ms → 25~26ms (약 15% 느림, 3회 측정). `<id>` 줄을 **지우면** Blog.id가 null이 되고, 제목이 같은 블로그가 한 객체로 합쳐진다(3개 → 2개).
- **일대다 join + `LIMIT 3`**: 행 단위로 잘려서 `[A[글1, 글2], B[글3]]`. 블로그 C가 사라지고 B의 글이 하나 빠진다.
- **중첩 select의 id가 null**: `column="id"`를 collection이 쓰면 그 컬럼은 자동 매핑에서 빠진다. `<id property="id" column="id"/>`를 추가해서 해결.
- **IN으로 받은 `Post.blogId`가 null**: `SELECT *` + `resultType="Post"`는 `mapUnderscoreToCamelCase` 기본값(false) 때문에 `blog_id`를 못 받는다. `blog_id AS blogId`로 해결.
- **빈 IN**: 부모가 없으면 SQL이 `WHERE blog_id IN`으로 끝나서 `SQLSyntaxErrorException`.
- **로그 레벨**: DEBUG에 SQL(Preparing/Parameters/Total), TRACE에 결과 행(Columns/Row). Spring Boot에서는 `logging.level.com.example.blog.mapper: debug`만으로 SQL이 보인다(Preparing 21줄, Row 0줄).

<sub>JDK 21 / MyBatis 3.5.19 / mybatis-spring-boot-starter 3.0.5 / Spring Boot 3.5.0 / MySQL 8.4.11 (Docker) / mysql-connector-j 9.2.0 / Apple M5, 10코어, 16GB, macOS 26.6. 2026-10-14 측정. 시간은 매퍼 호출 시간만 쟀고, 2026-10-14 재실행에서는 26.4ms → 29.3ms(약 11%)로 나왔다. 환경에 따라 달라질 수 있음.</sub>

## 배운 점

- lazy loading은 N+1을 없애지 않고 미룬다. 목록을 그리는 순간 같은 21번이 나간다.
- join + resultMap은 1번이지만 `<id>`가 사실상 필수다. 빼면 느려지고, 지우면 객체가 잘못 합쳐진다.
- 일대다 join에 LIMIT을 걸면 부모가 아니라 행이 잘린다. 페이징이 필요하면 부모를 먼저 페이징하고 자식은 IN으로 2번에 가져온다. 이때 빈 목록 처리를 빼먹으면 SQL 오류가 난다.
- 쿼리 수는 로그 줄 수로 테스트에서 고정할 수 있다. 회귀를 막는 가장 싼 방법이다.

## 확인하지 않은 것

- PostgreSQL에서 MyBatis로 돌리지 않았다.
- API 단위 응답 시간은 재지 않았다. 왕복 비용은 [001](../001-n-plus-one)에서 따로 쟀다.

## 관련 글

- Velog: [MyBatis N+1 문제 해결, 로그 확인부터 일대다 페이징까지](https://velog.io/@morningverse/N1-쿼리는-왜-생길까)
