# 002. 인덱스는 언제 빨라지고, 언제 소용없을까

> 같은 인덱스인데 한 쿼리는 887배 빨라지고, 다른 쿼리는 오히려 조금 느려졌다.

## 문제

`WHERE` 조건 컬럼에 인덱스가 없으면 DB는 테이블 전체를 읽는다(full scan).
그렇다고 인덱스를 걸면 항상 빨라질까?

```sql
SELECT id, amount FROM orders WHERE user_id = ?;   -- 결과가 몇 건
SELECT id, amount FROM orders WHERE status = 'PAID'; -- 결과가 90%
```

## 실험

- 주문 30만 건, 사용자 3만 명, 상태는 PAID 90% / CANCELLED 10% (SQLite in-memory, 시드 고정)
- 인덱스 없이 측정 → `user_id`, `status`에 인덱스 생성 + `ANALYZE` → 같은 쿼리 다시 측정
- 사용자 조회는 서로 다른 user_id로 100번, 상태 조회는 1번
- `EXPLAIN QUERY PLAN`으로 실행 계획이 바뀌는지 확인하고, 결과가 같은지 `assert`로 확인

```bash
python3 main.py
```

## 결과

| 케이스 | 반환 행 | 인덱스 없음 (ms) | 인덱스 있음 (ms) | 배율 |
|---|---:|---:|---:|---:|
| user_id 조회 x100 | 1,040 | 370.98 | 0.42 | 887.3x |
| status = 'PAID' x1 | 269,700 | 47.65 | 50.68 | 0.9x |

실행 계획

```text
user_id 조회   before: SCAN orders
               after : SEARCH orders USING INDEX idx_orders_user_id (user_id=?)
status 조회    before: SCAN orders
               after : SEARCH orders USING INDEX idx_orders_status (status=?)
```

<sub>Python 3.13 / SQLite 3.50, 3회 중 최소값. 환경에 따라 수치는 달라질 수 있음.</sub>

## 배운 점

- 인덱스가 빛나는 건 **결과가 전체의 아주 일부일 때**다. 30만 건 중 평균 10건을 찾는 조회는 SCAN → SEARCH로 바뀌며 수백 배 빨라졌다.
- 결과가 테이블의 90%면 인덱스를 타도 이득이 없다. 인덱스를 따라간 뒤 행마다 테이블을 다시 찾아가야 해서, 그냥 처음부터 끝까지 읽는 것과 비슷하거나 더 느리다.
- 그래서 "인덱스를 탔다"(`SEARCH ... USING INDEX`)와 "빨라졌다"는 다른 이야기다. 실행 계획과 실제 시간을 같이 봐야 한다.
- 선택도가 낮은 컬럼(상태값, 성별 등)은 단독 인덱스보다 다른 컬럼과 묶은 복합 인덱스로 쓰는 경우가 많다. 인덱스는 쓰기 비용과 저장 공간도 늘린다.
