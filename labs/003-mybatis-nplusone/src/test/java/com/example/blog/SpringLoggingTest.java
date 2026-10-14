package com.example.blog;

import static org.assertj.core.api.Assertions.*;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.example.blog.mapper.BlogMapper;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/** 글의 application.yml(logging.level.com.example.blog.mapper: debug)만으로 SQL 이 찍히는지. */
@SpringBootTest
class SpringLoggingTest {
  @Autowired BlogMapper mapper;

  @Test void yml_debug_level_prints_sql() throws Exception {
    NPlusOneTest.seed(20, 3);
    Logger log = (Logger) LoggerFactory.getLogger("com.example.blog.mapper");
    assertThat(log.getLevel()).isEqualTo(Level.DEBUG);                       // yml 이 적용됨
    assertThat(((Logger) LoggerFactory.getLogger("ROOT")).getLevel()).isEqualTo(Level.INFO);
    var logs = new ListAppender<ILoggingEvent>(); logs.start(); log.addAppender(logs);
    mapper.selectBlogs();
    log.detachAppender(logs);
    long sql = logs.list.stream().filter(e -> e.getFormattedMessage().matches("=+>  Preparing:.*")).count();
    long rows = logs.list.stream().filter(e -> e.getFormattedMessage().contains("Row:")).count();
    System.out.println("spring) Preparing 줄 수 = " + sql + ", Row 줄 수 = " + rows);
    assertThat(sql).isEqualTo(21);
    assertThat(rows).isZero();
  }
}
