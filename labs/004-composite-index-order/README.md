# 004. 복합 인덱스, 컬럼 순서가 왜 중요할까

> 같은 두 컬럼으로 만든 인덱스인데, 순서만 바꿨더니 한 쿼리는 505배 느려지고 다른 쿼리는 10.6배 빨라졌다.

## 문제

`user_id`와 `created_at`으로 자주 조회하니 두 컬럼을 묶어 인덱스를 만들기로 했다.
그런데 `(user_id, created_at)`과 `(created_at, user_id)` 중 무엇으로 만들어야 할까?

```sql
CREATE INDEX idx ON orders(user_id, created_at);  -- A
CREATE INDEX idx ON orders(created_at, user_id);  -- B

SELECT id, amount FROM orders WHERE user_id = ? AND created_at >= ?;  -- 사용자 + 최근 30일
SELECT id, amount FROM orders WHERE user_id = ?;                      -- 사용자만
SELECT id, amount FROM orders WHERE created_at >= ?;                  -- 최근 1일만
```

## 실험

- 주문 30만 건, 사용자 3만 명, 1년치 `created_at` (SQLite in-memory, 시드 고정)
- 인덱스 없음 / `(user_id, created_at)` / `(created_at, user_id)` 세 상태에서 같은 쿼리 3개를 측정
- 사용자 조건이 있는 쿼리는 서로 다른 user_id로 100번, 최근 1일 조회는 1번
- `EXPLAIN QUERY PLAN`으로 인덱스를 어떻게 쓰는지 확인하고, 세 상태의 결과가 같은지 `assert`로 확인

```bash
python3 main.py
```

## 결과

![user_id와 최근 30일 조회는 (user_id, created_at)이 505배, user_id만 조회는 929배 빠르고, 최근 1일 조회는 (created_at, user_id)가 10.6배 빠른 비교 그래프](result.png)

| 쿼리 | 반환 행 | 인덱스 없음 (ms) | (user_id, created_at) (ms) | (created_at, user_id) (ms) | 더 빠른 쪽 |
|---|---:|---:|---:|---:|---|
| user_id = ? AND 최근 30일 x100 | 76 | 364.98 | 0.08 | 42.07 | (user_id, created_at) 505.0x |
| user_id = ? x100 | 1,010 | 367.01 | 0.40 | 369.30 | (user_id, created_at) 929.0x |
| 최근 1일 x1 | 835 | 3.31 | 3.25 | 0.31 | (created_at, user_id) 10.6x |

실행 계획

```text
user_id + 최근 30일   (user_id, created_at): SEARCH orders USING INDEX idx (user_id=? AND created_at>?)
                      (created_at, user_id): SEARCH orders USING INDEX idx (created_at>?)
user_id만             (user_id, created_at): SEARCH orders USING INDEX idx (user_id=?)
                      (created_at, user_id): SCAN orders
최근 1일만            (user_id, created_at): SCAN orders
                      (created_at, user_id): SEARCH orders USING INDEX idx (created_at>?)
```

<sub>Python 3.13 / SQLite 3.50, 3회 중 최소값. 환경에 따라 수치는 달라질 수 있음.</sub>

## 배운 점

- 복합 인덱스는 **왼쪽 컬럼부터 차례로** 정렬된 목록이다. 첫 컬럼 조건이 없으면 인덱스로 범위를 좁힐 수 없다. `(created_at, user_id)`에 `user_id`만 주면 SCAN이 됐고, 인덱스 없는 상태와 시간이 같았다.
- 등호(`=`) 조건 컬럼을 앞에, 범위(`>=`, `BETWEEN`) 조건 컬럼을 뒤에 두는 게 기본이다. 범위 컬럼이 앞에 오면 실행 계획에 `created_at>?`만 남는다. 최근 30일치 인덱스 항목을 다 훑으면서 `user_id`를 하나씩 걸러내서, 인덱스를 탔는데도 505배 느렸다.
- 반대로 `created_at`만으로 조회하는 화면(최근 주문 목록 같은)이 중요하면 `(created_at, user_id)`가 10.6배 빨랐다. 정답 순서는 컬럼이 아니라 **실제로 자주 나가는 쿼리**가 정한다.
- 두 패턴이 다 중요하면 인덱스를 두 개 두는 방법도 있지만, 인덱스마다 INSERT/UPDATE 비용과 저장 공간이 늘어난다. "인덱스를 탔다"는 실행 계획만 보지 말고 어떤 조건으로 SEARCH 했는지까지 같이 봐야 한다.
