package com.example.blog;

public class Post {
  private Long id;
  private Long blogId;
  private String subject;
  public Long getId() { return id; }
  public void setId(Long id) { this.id = id; }
  public Long getBlogId() { return blogId; }
  public void setBlogId(Long blogId) { this.blogId = blogId; }
  public String getSubject() { return subject; }
  public void setSubject(String subject) { this.subject = subject; }
}
