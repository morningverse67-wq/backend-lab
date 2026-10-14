package com.example.blog.mapper;

import com.example.blog.Blog;
import com.example.blog.Post;
import java.util.List;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface BlogMapper {
  // 글: N+1 예제
  List<Blog> selectBlogs();
  // 글: 해결 1 (join + resultMap)
  List<Blog> selectBlogsWithPosts();
  // 개정본
  List<Blog> selectBlogsFixed();
  List<Post> selectPostsByBlogIdsFixed(@Param("blogIds") List<Long> blogIds);
  // 실험용: fetchType="lazy"
  List<Blog> selectBlogsFetchLazy();
  // 실험용: 해결 1 에 <id> 대신 <result>
  List<Blog> selectBlogsWithPostsNoId();
  // 실험용: 해결 1 에서 <id> 줄을 지움
  List<Blog> selectBlogsWithPostsIdDeleted();
  // 실험용: 해결 1 에 ORDER BY + LIMIT
  List<Blog> selectBlogsWithPostsLimit(@Param("limit") int limit);
  // 글: 해결 2
  List<Blog> selectBlogPage(@Param("size") int size, @Param("offset") int offset);
  List<Post> selectPostsByBlogIds(@Param("blogIds") List<Long> blogIds);
}
