# 001. N+1 쿼리

> 목록 1번 + 항목마다 1번씩 = N+1번. 쿼리 하나하나는 빠른데 왜 느려질까?

## 문제

게시글 목록을 가져온 뒤, 게시글마다 댓글을 다시 조회하면 쿼리가 `1 + N`번 나간다.
ORM의 지연 로딩이나 MyBatis의 중첩 select에서 흔히 생기는 패턴이다.

```python
for post_id, title in conn.execute("SELECT id, title FROM post"):
    conn.execute("SELECT body FROM comment WHERE post_id = ?", (post_id,))
```

## 실험

- 게시글 500개, 게시글당 댓글 5개 (SQLite in-memory, 표준 라이브러리만 사용)
- 세 가지 방식 비교: `n_plus_one` / `join_once` (JOIN 1번) / `batch_in` (IN 절로 2번)
- `sqlite3.set_trace_callback`으로 실제 실행된 SELECT 수를 센다
- 세 방식의 결과가 같은지 `assert`로 확인
- 네트워크 왕복을 흉내 내려고 쿼리마다 0.5ms sleep을 넣은 경우도 측정

```bash
python3 main.py
```

## 결과

![N+1 501번 321.82ms, JOIN 1번 1.68ms, IN 일괄 조회 2번 2.08ms 비교 막대 그래프](result.png)

| 방식 | 쿼리 수 | in-memory (ms) | 왕복 0.5ms 가정 (ms) |
|---|---:|---:|---:|
| n_plus_one | 501 | 0.83 | 321.82 |
| join_once | 1 | 0.66 | 1.68 |
| batch_in | 2 | 0.63 | 2.08 |

<sub>Python 3.13 / SQLite 3.50, 3회 중 최소값. 환경에 따라 수치는 달라질 수 있음.</sub>

## 배운 점

- 같은 프로세스 안의 in-memory DB에서는 N+1도 거의 차이가 없다. **비용의 대부분은 쿼리 실행이 아니라 왕복(round trip)** 이다.
- 실제 DB는 네트워크 너머에 있으니, 쿼리 수가 그대로 응답 시간에 곱해진다. 0.5ms만 잡아도 500건에서 약 190배 차이.
- 해결은 "쿼리 수를 N에 비례하지 않게" 만드는 것. JOIN은 1번이지만 부모 행이 중복되어 전송량이 늘고, IN 배치는 2번이지만 행 중복이 없다. 자식이 많거나 페이징이 필요하면 IN 배치(JPA의 `batch_size`, MyBatis의 `collection` + IN)가 다루기 쉽다.
- IN 절 파라미터 수에는 DB별 상한이 있어서, 실제로는 일정 크기로 나눠 보내야 한다.

## 관련 글

- Velog: [N+1 쿼리는 왜 생길까?](https://velog.io/@morningverse/N1-쿼리는-왜-생길까)
