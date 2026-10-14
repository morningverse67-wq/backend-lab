package com.example.blog;

import java.util.List;

public class Blog {
  private Long id;
  private String title;
  private List<Post> posts;
  public Long getId() { return id; }
  public void setId(Long id) { this.id = id; }
  public String getTitle() { return title; }
  public void setTitle(String title) { this.title = title; }
  public List<Post> getPosts() { return posts; }
  public void setPosts(List<Post> posts) { this.posts = posts; }
}
