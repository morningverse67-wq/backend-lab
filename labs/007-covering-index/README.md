# 007. 커버링 인덱스: 테이블을 안 읽으면 얼마나 빨라질까

> 같은 데이터에 인덱스 컬럼만 늘렸더니 한 달 매출 합계가 16.6배(10회 조회 합계 82.20ms → 4.95ms) 빨라졌다. 대신 인덱스에 없는 컬럼을 하나라도 조회하면 1.0배였고, 인덱스 크기는 1.71배가 됐다.

## 문제

`user_id`에 인덱스가 있으면 아래 쿼리는 인덱스를 탄다.

```sql
CREATE INDEX idx_user ON orders(user_id);

SELECT created_at, amount FROM orders WHERE user_id = ?;
-- SEARCH orders USING INDEX idx_user (user_id=?)
```

그런데 인덱스에는 `user_id`와 행 위치(rowid)만 있다. `created_at`, `amount`를 가져오려면 찾은 행마다 테이블을 한 번 더 읽어야 한다. 조회에 필요한 컬럼을 전부 인덱스에 넣으면(커버링 인덱스) 이 단계가 없어진다.

```sql
CREATE INDEX idx_user_cov ON orders(user_id, created_at, amount);
-- SEARCH orders USING COVERING INDEX idx_user_cov (user_id=?)
```

얼마나 빨라지고, 무엇을 대가로 내야 할까?

## 실험

- 주문 30만 건, 사용자 3,000명(1명당 평균 100건), 행마다 200자짜리 `memo` 컬럼 (SQLite in-memory, 시드 고정)
- 같은 행을 넣은 DB 2개를 만들고 인덱스만 다르게 둠
  - 일반: `(user_id)`, `(created_at)`
  - 커버링: `(user_id, created_at, amount)`, `(created_at, amount)`
- 세 쿼리를 두 DB에서 똑같이 돌리고, 결과가 같은지 `assert`로 확인
  - 사용자별 주문 조회 `SELECT created_at, amount ... WHERE user_id = ?` (서로 다른 사용자 100명)
  - 한 달 매출 합계 `SELECT SUM(amount) ... WHERE created_at >= ? AND created_at < ?` (서로 다른 달 10개)
  - 인덱스에 없는 `memo`까지 조회 `SELECT created_at, amount, memo ... WHERE user_id = ?` (100명)
- 쓰기 쪽 비용: 인덱스 크기(`PRAGMA page_count` 차이), 3만 건 INSERT 시간(측정 후 롤백)

```bash
python3 main.py
```

## 결과

![한 달 매출 합계 10회가 일반 인덱스 82.20ms, 커버링 인덱스 4.95ms로 16.6배, 사용자별 주문 조회 100회는 3.94ms 대 1.72ms로 2.3배, memo까지 조회하면 5.16ms 대 5.28ms로 1.0배. 인덱스 크기 11.16MB에서 19.05MB, INSERT 3만 건 35.84ms에서 41.21ms인 비교 그래프](result.png)

같은 데이터, 인덱스만 다르게 (각 쿼리를 서로 다른 값으로 여러 번 조회한 합계)

| 쿼리 | 읽은 행 | 일반 인덱스 (ms) | 커버링 인덱스 (ms) | 배율 |
|---|---:|---:|---:|---:|
| 사용자별 `created_at, amount` x100 | 10,055 | 3.94 | 1.72 | 2.3x |
| 한 달 `SUM(amount)` x10 | 249,145 | 82.20 | 4.95 | 16.6x |
| `memo`까지 조회 x100 | 10,055 | 5.16 | 5.28 | 1.0x |

실행 계획

```text
사용자별 조회  일반  : SEARCH orders USING INDEX idx_user (user_id=?)
               커버링: SEARCH orders USING COVERING INDEX idx_user_cov (user_id=?)
한 달 합계     일반  : SEARCH orders USING INDEX idx_created (created_at>? AND created_at<?)
               커버링: SEARCH orders USING COVERING INDEX idx_created_cov (created_at>? AND created_at<?)
memo까지 조회  일반  : SEARCH orders USING INDEX idx_user (user_id=?)
               커버링: SEARCH orders USING INDEX idx_user_cov (user_id=?)   <- COVERING이 빠짐
```

쓰기 쪽 비용

| 항목 | 일반 인덱스 | 커버링 인덱스 | 배율 |
|---|---:|---:|---:|
| 인덱스 2개 크기 | 11.16 MB | 19.05 MB | 1.71x |
| 3만 건 INSERT | 35.84 ms | 41.21 ms | 1.15x |

<sub>Python 3.13 / SQLite 3.50, 3회 중 최소값. 환경에 따라 수치는 달라질 수 있음.</sub>

## 배운 점

- 일반 인덱스는 "인덱스에서 찾기 → 행마다 테이블 읽기" 두 단계다. 필요한 컬럼이 모두 인덱스에 있으면 두 번째 단계가 없어져서, 실행 계획에 `COVERING INDEX`가 붙고 사용자별 조회가 2.3배 빨라졌다.
- 이득은 **읽는 행이 많을수록** 커졌다. 사용자당 100건 남짓을 읽는 사용자별 조회는 2.3배였지만, 한 번에 약 2.5만 건을 읽는 한 달 합계는 16.6배였다. 테이블 읽기 횟수가 읽는 행 수만큼 늘기 때문이다.
- 인덱스에 없는 컬럼이 **하나라도** 들어가면 효과가 사라진다. `memo`를 같이 조회하자 `COVERING`이 빠지고 1.0배(5.16ms vs 5.28ms)였다. `SELECT *`를 습관처럼 쓰면 커버링 인덱스는 거의 쓰이지 않는다.
- 공짜가 아니다. 컬럼을 인덱스에 넣은 만큼 인덱스가 커지고(1.71배) INSERT도 느려졌다(1.15배). 자주 돌고 느린 쿼리 몇 개에만 쓰고, 컬럼을 계속 덧붙이는 건 피한다. 이 실험은 메모리 DB라 디스크 I/O가 없었다. 디스크에서 캐시에 없는 페이지를 읽어야 하면 차이가 달라질 수 있다(여기서는 재지 않음).
