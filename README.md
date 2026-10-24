# backend-lab

![백엔드 성능 문제를 재현하고 측정하는 backend-lab](assets/cover.png)

Small, runnable experiments that reproduce and measure backend performance problems: N+1, indexes, paging, MyBatis.

### 30초 실행

```bash
git clone https://github.com/morningverse67-wq/backend-lab.git && cd backend-lab
python3 labs/001-n-plus-one/main.py      # N+1: 쿼리 501번 vs 1번 (Python 3, 설치 없음)
python3 labs/002-index-scan/main.py      # 인덱스 vs 풀 스캔
```

MyBatis + MySQL 버전(Java 21, Maven, Docker)은 [labs/003-mybatis-nplusone](labs/003-mybatis-nplusone)의 한 줄 명령으로 실행한다.

백엔드에서 자주 만나는 데이터·성능 문제를 **작게 재현하고, 직접 측정해서, 기록**하는 실험실.

- 실험 하나 = 폴더 하나 (`labs/NNN-topic/`)
- 각 폴더에는 바로 실행되는 코드와 `README.md` (문제 → 실험 → 결과 → 배운 점)
- Python lab: 표준 라이브러리만 사용해서 `python3 main.py` 한 줄로 재현
- Java lab: Maven + Docker(MySQL)로 `mvn test` 재현. 실행 명령은 각 lab README에
- 수치는 직접 돌린 값만 적는다. 환경에 따라 달라질 수 있음을 함께 표시

## Labs

| # | 주제 | 한 줄 요약 | 실행 | Velog 글 |
|---|---|---|---|---|
| 001 | [N+1 쿼리](labs/001-n-plus-one) | 쿼리 501번 vs 1번. 비용의 대부분은 실행이 아니라 왕복 | Python | [MyBatis N+1 문제 해결, 로그 확인부터 일대다 페이징까지](https://velog.io/@morningverse/N1-쿼리는-왜-생길까) |
| 002 | [인덱스 vs 풀 스캔](labs/002-index-scan) | 선택적 조회는 887배 빨라지고, 90%를 읽는 조회는 0.9배 | Python | 후속 글 후보 |
| 003 | [MyBatis N+1 (MySQL)](labs/003-mybatis-nplusone) | 중첩 select 21번 → join resultMap 1번, 부모 페이징 + IN 2번. `<id>` 대신 `<result>`면 약 11~19% 느림 | Java | [MyBatis N+1 문제 해결, 로그 확인부터 일대다 페이징까지](https://velog.io/@morningverse/N1-쿼리는-왜-생길까) |
| 004 | [복합 인덱스 컬럼 순서](labs/004-composite-index-order) | 같은 두 컬럼도 순서에 따라 505배 차이. 등호 컬럼 먼저, 범위 컬럼 나중. 날짜만 조회하면 반대로 10.6배 | Python | 후속 글 후보 |
| 005 | [OFFSET vs 커서 페이징](labs/005-offset-vs-keyset-paging) | 10,000페이지 100회 조회 합계 OFFSET 187.96ms vs 커서 0.77ms(244.7배). 대신 특정 페이지로 바로 이동은 OFFSET이 52.3배 빠름 | Python | 후속 글 후보 |
| 006 | [인덱스를 못 타는 쿼리](labs/006-non-sargable-queries) | 인덱스는 그대로, WHERE만 고쳐서 날짜 함수 41.3배, `LIKE '%-1234'` 876.4배 (각 100회 조회 합계). 식 인덱스는 같은 식에만 효과 | Python | 후속 글 후보 |
| 007 | [커버링 인덱스](labs/007-covering-index) | 테이블을 안 읽으니 한 달 합계 10회 82.20ms → 4.95ms(16.6배). 인덱스 밖 컬럼을 하나라도 조회하면 1.0배, 인덱스 크기는 1.71배 | Python | 후속 글 후보 |
| 008 | [대량 INSERT](labs/008-bulk-insert) | 1만 건을 한 건마다 COMMIT 1,948.84ms → 트랜잭션 하나 8.08ms(241.2배). 1,000건 단위 커밋도 10.16ms. 대신 하나로 묶으면 1건 실패에 0건이 남음 | Python | 후속 글 후보 |
| 009 | [COUNT(*) 비용과 대안](labs/009-count-cost) | 100만 건 중 59.9만 건 세기: 인덱스 없음 21.16ms, 인덱스 6.12ms, 트리거 카운터 0.0005ms. 대신 카운터 때문에 INSERT 2.90배·DELETE 4.73배 느려짐. 있는지만 볼 땐 EXISTS, 단 결과가 없으면 1.0배 | Python | 후속 글 후보 |
| 010 | [Lost update와 낙관적 락](labs/010-lost-update-optimistic-lock) | 한 행에 +1 요청 1,600번: 락 없이 읽고-쓰기는 최종 값 200(1,400건 유실), `version` 낙관적 락은 1,600. 대신 재시도 5,995번에 시간 2.09배, 요청이 흩어지면 1.03배 | Python | 후속 글 후보 |
| 011 | [비관적 락과 트레이드오프](labs/011-pessimistic-lock) | 한 행에 +1 요청 1,600번: 행을 먼저 잠그면 재시도 0번, p99 174.32ms → 18.92ms. 전체 시간은 2,767.2ms vs 2,771.7ms로 같고 중앙값은 1.66ms → 13.62ms. 락 하나로 전부 묶으면 흩어진 요청도 2.49배 느려짐 | Python | 후속 글 후보 |
| 012 | [재고 차감 동시성](labs/012-stock-decrement-concurrency) | 재고 100개에 주문 400건: 읽고-쓰기는 400개를 팔고(300개 초과), `qty - 1`만 바꾸면 107개에 재고 -7. 원자적 UPDATE는 정확히 100개, 주문 800건은 락보다 2.17배 빠름(634.9ms vs 1,379.7ms). 대신 장바구니처럼 여러 행이면 트랜잭션 필요 | Python | 후속 글 후보 |
| 013 | [격리 수준 현상](labs/013-isolation-anomalies) | A를 읽고 0.2ms 뒤 B를 읽는 합계 보고서 1,000건 중 235건이 틀림(문장마다 다른 시점을 읽음). 읽기 트랜잭션으로 묶으면 0건, 시간은 같음(p50 0.315ms vs 0.314ms). 대신 스냅샷을 열어 두면 커밋 3,000번에 WAL 3.93MB → 11.79MB(3.0배) | Python | 후속 글 후보 |
| 014 | [멱등성 키로 중복 결제 막기](labs/014-idempotency-key) | 같은 결제 요청 3번씩 주문 200건: 키를 먼저 조회하면 DB 행은 200건인데 PG 결제는 599번, 결제 전에 키부터 저장하면 200번. 재시도는 같은 키여야 함(시도마다 새 키면 400번). 대가는 요청당 0.148ms, PG 2ms 포함 1.22배 | Python | [이벤트 중복 처리, SQLite DO NOTHING 뒤 집계가 늘어나는 함정](https://velog.io/@morningverse/sqlite-event-dedup-returning-count) |

다음 주제는 [ROADMAP.md](ROADMAP.md)에.

## Links

- Blog: [velog.io/@morningverse](https://velog.io/@morningverse)
- 면접 질문별 정리: [backend-interview-lab](https://github.com/morningverse67-wq/backend-interview-lab)
