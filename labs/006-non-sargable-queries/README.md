# 006. 인덱스가 있는데도 못 타는 쿼리

> 인덱스는 하나도 안 바꾸고 WHERE 절만 고쳤더니 41.3배(날짜 함수), 876.4배(`LIKE '%-1234'`), 6,993.9배(컬럼 형변환) 빨라졌다. 각 100회 조회 합계 기준이다.

## 문제

`created_at`, `phone`, `order_no`에 모두 인덱스가 있다. 그런데 아래 쿼리는 셋 다 테이블을 처음부터 끝까지 읽는다.

```sql
SELECT id, amount FROM orders WHERE date(created_at) = '2026-03-15';     -- 컬럼에 함수
SELECT id, amount FROM orders WHERE phone LIKE '%-1234';                -- 앞에 %
SELECT id, amount FROM orders WHERE CAST(order_no AS INTEGER) = 12345678; -- 컬럼 쪽 형변환
```

인덱스는 **컬럼 값 그대로** 정렬해 둔 구조라서, WHERE에서 컬럼을 가공하거나 앞부분을 모르면 어디서부터 찾아야 할지 알 수 없다. 얼마나 느려지고, 어떻게 고치면 같은 결과를 인덱스로 가져올 수 있을까?

## 실험

- 주문 30만 건, 1년치 `created_at`(`YYYY-MM-DD HH:MM:SS` 문자열), 전화번호, 문자열 주문번호 (SQLite in-memory, 시드 고정)
- 인덱스: `created_at`, `phone`, `phone_last4`(전화번호 뒷자리 4자리를 따로 저장한 컬럼), `order_no`
- 세 경우 모두 "그대로 쓴 쿼리"와 "인덱스를 탈 수 있게 고친 쿼리"를 서로 다른 값 100개로 조회하고, 결과가 같은지 `assert`로 확인
  - 함수: `date(created_at) = ?` → `created_at >= ? AND created_at < ?`
  - LIKE: `phone LIKE '%-1234'` → `phone_last4 = '1234'`
  - 형변환: `CAST(order_no AS INTEGER) = ?` → `order_no = '...'`
- 고친 방법이 안 통하는 경우도 확인: 식 인덱스 `date(created_at)`를 만든 뒤 같은 식과 다른 식(`strftime('%Y-%m', ...)`)으로 조회
- `CAST` 없이 문자열 컬럼에 숫자 파라미터만 넘겼을 때 실행 계획

```bash
python3 main.py
```

## 결과

![날짜 함수 1,337.37ms → 범위 조건 32.36ms(41.3배), LIKE '%-1234' 906.04ms → 뒷자리 컬럼 1.03ms(876.4배), CAST(order_no) 663.54ms → 문자열 비교 0.095ms(6,993.9배). 식 인덱스는 같은 식에만 34.2배, 월 단위 strftime 조회는 1.0배인 비교 그래프](result.png)

같은 인덱스, WHERE만 다르게 (서로 다른 값 100번 조회 합계)

| 케이스 | 반환 행 | 그대로 (ms) | 고친 쿼리 (ms) | 배율 |
|---|---:|---:|---:|---:|
| `date(created_at) = ?` → 범위 조건 | 82,141 | 1,337.37 | 32.36 | 41.3x |
| `phone LIKE '%-1234'` → `phone_last4 = ?` | 3,009 | 906.04 | 1.03 | 876.4x |
| `CAST(order_no AS INTEGER) = ?` → `order_no = '...'` | 100 | 663.54 | 0.095 | 6,993.9x |

실행 계획

```text
그대로   : SCAN orders                                            (세 경우 모두)
고친 쿼리: SEARCH orders USING INDEX idx_orders_created (created_at>? AND created_at<?)
           SEARCH orders USING INDEX idx_orders_last4 (phone_last4=?)
           SEARCH orders USING INDEX idx_orders_order_no (order_no=?)
```

식 인덱스 `CREATE INDEX idx_orders_day ON orders(date(created_at))`를 만든 뒤

| 쿼리 | 식 인덱스 전 (ms) | 식 인덱스 후 (ms) | 배율 | 실행 계획 |
|---|---:|---:|---:|---|
| `date(created_at) = ?` x100 | 1,340.69 | 39.22 | 34.2x | `SEARCH ... USING INDEX idx_orders_day (<expr>=?)` |
| `strftime('%Y-%m', created_at) = ?` x1 | 35.42 | 34.89 | 1.0x | `SCAN orders` |
| 같은 달을 범위 조건으로 x1 (23,386행) | - | 8.87 | - | `SEARCH ... USING INDEX idx_orders_created` |

`CAST` 없이 `order_no = 12345678`(숫자 파라미터)로 조회하면 SQLite에서는 `SEARCH ... USING INDEX idx_orders_order_no`로 인덱스를 탔다(100회 합계 0.089ms, 문자열 파라미터는 0.087ms).

<sub>Python 3.13 / SQLite 3.50, 3회 중 최소값. 환경에 따라 수치는 달라질 수 있음.</sub>

## 배운 점

- 인덱스가 있어도 WHERE에서 **컬럼 쪽을 가공하면** 실행 계획이 `SCAN`이 된다. 함수는 컬럼 대신 값 쪽을 바꾸는 방향(`date(col) = 날짜` → `col >= 날짜 AND col < 다음 날`)으로 고치면 같은 인덱스로 41.3배 빨라졌다.
- `LIKE '%1234'`는 앞부분을 모르니 인덱스 순서를 쓸 수 없다. 뒷자리 검색이 자주 필요하면 뒷자리를 별도 컬럼으로 저장하고 인덱스를 거는 식으로 풀었다(876.4배). 대신 컬럼과 인덱스가 하나 늘고, 쓰기 때마다 값을 맞춰야 한다. 단어 중간 검색이 필요하면 전문 검색(full-text)을 검토한다.
- 형변환은 DB마다 다르다. MySQL은 문자열 컬럼을 숫자와 비교하면 컬럼 쪽을 숫자로 바꿔서 인덱스를 쓰지 못한다고 문서에 적혀 있다. SQLite는 상수 쪽을 바꿔서 인덱스를 탔기 때문에, 이 실험에서는 컬럼 쪽 변환을 `CAST`로 직접 써서 재현했다. 어느 DB든 파라미터 타입을 컬럼 타입에 맞추는 게 안전하다.
- 식 인덱스는 **그 식과 똑같이 쓴 쿼리에만** 듣는다. `date(created_at)` 식 인덱스로 일 단위 조회는 34.2배 빨라졌지만, 월 단위 `strftime` 조회는 1.0배로 그대로였다. 조회 패턴이 여러 개면 컬럼을 그대로 두고 범위 조건으로 쓰는 쪽이 하나의 인덱스로 여러 쿼리를 받는다.
