# backend-proof

### 백엔드 면접 단골 질문에, 직접 잰 숫자로 답한 근거집

"N+1이 뭔가요?", "낙관적 락과 비관적 락은 뭐가 다른가요?" 같은 질문마다 **30초 답 → 직접 재현한 코드 → 측정 숫자 → 꼬리 질문 → 이 방법의 대가**를 한 폴더에 담았어요. 외운 답 대신, 아래 한 줄로 근거를 다시 돌려 볼 수 있어요.

```bash
git clone https://github.com/morningverse67-wq/backend-proof.git && cd backend-proof
python3 labs/001-n-plus-one/main.py              # 쿼리 501번 vs 1번 (설치 없음, Python 3)
python3 labs/010-lost-update-optimistic-lock/main.py   # +1 요청 1,600번 중 사라진 1,400건
```

### 질문별로 찾기

| 면접 질문 | 30초 답 | 직접 잰 숫자 | 대가 | 폴더 |
|---|---|---|---|---|
| 인덱스는 왜 빠른가요? | 필요한 행만 읽어서 빨라요. 많이 읽는 조회에는 이득이 없어요 | 선택적 조회 887배, 90%를 읽는 조회 0.9배 | 쓰기와 저장 공간 | [002](labs/002-index-scan) |
| OFFSET 페이징의 문제는요? | 뒤로 갈수록 건너뛰는 행을 다 읽어요 | 10,000페이지 조회 커서 방식이 244.7배 빠름 | 특정 페이지로 바로 이동은 OFFSET이 52.3배 빠름 | [005](labs/005-offset-vs-keyset-paging) |
| 낙관적 락은 언제 좋은가요? | 충돌이 드물 때요 | 사라진 요청 1,400건 → 0건 | 재시도 5,995번, 시간 2.09배 | [010](labs/010-lost-update-optimistic-lock) |
| 재고 차감은 어떻게 하나요? | 읽고-쓰기 대신 DB가 한 문장에서 계산해요 | 재고 100개에 주문 400건, 정확히 100개에서 멈춤 | 여러 행이면 트랜잭션 필요 | [012](labs/012-stock-decrement-concurrency) |
| 캐시 만료가 몰리면요? | 만료 시각에 무작위를 더해 흩어요 | 가장 바쁜 1초의 DB 조회 592번 → 96번 | 만료 시각이 조금 흩어짐 | [018](labs/018-ttl-jitter) |
| 재시도는 어떻게 하나요? | 지수 백오프에 jitter를 더해요 | 복구 후 거절 450번 → 9번 | 마지막 성공까지 4,048ms | [021](labs/021-retry-backoff-jitter) |

전체 46개는 다섯 묶음이에요.

- **데이터베이스·쿼리 (15)**: 001~009, 034~038, 041
- **트랜잭션·동시성 (8)**: 010~015, 039, 040
- **캐시 (9)**: 016~019, 042~046
- **장애 대응·API (6)**: 020~025
- **데이터 처리·메시징 (8)**: 026~033

각 폴더의 `README.md`는 문제 → 실험 → 결과 → 배운 점 순서이고, 수치는 직접 돌린 값만 적어요. 환경에 따라 달라질 수 있다는 점도 함께 적어 둬요.

<details>
<summary>전체 46개 목록</summary>

| # | 주제 | 결과 한 줄 |
|---|---|---|
| 001 | [N+1 쿼리](labs/001-n-plus-one) | 쿼리 501번 vs 1번. 비용의 대부분은 실행이 아니라 왕복 |
| 002 | [인덱스 vs 풀 스캔](labs/002-index-scan) | 선택적 조회는 887배 빨라지고, 90%를 읽는 조회는 0.9배 |
| 003 | [MyBatis N+1 (MySQL)](labs/003-mybatis-nplusone) | 중첩 select 21번 → join resultMap 1번, 부모 페이징 + IN 2번. `<id>` 대신 `<result>`면 약 11~19% 느림 |
| 004 | [복합 인덱스 컬럼 순서](labs/004-composite-index-order) | 같은 두 컬럼도 순서에 따라 505배 차이. 등호 컬럼 먼저, 범위 컬럼 나중. 날짜만 조회하면 반대로 10.6배 |
| 005 | [OFFSET vs 커서 페이징](labs/005-offset-vs-keyset-paging) | 10,000페이지 100회 조회 합계 OFFSET 187.96ms vs 커서 0.77ms(244.7배). 대신 특정 페이지로 바로 이동은 OFFSET이 52.3배 빠름 |
| 006 | [인덱스를 못 타는 쿼리](labs/006-non-sargable-queries) | 인덱스는 그대로, WHERE만 고쳐서 날짜 함수 41.3배, `LIKE '%-1234'` 876.4배 (각 100회 조회 합계). 식 인덱스는 같은 식에만 효과 |
| 007 | [커버링 인덱스](labs/007-covering-index) | 테이블을 안 읽으니 한 달 합계 10회 82.20ms → 4.95ms(16.6배). 인덱스 밖 컬럼을 하나라도 조회하면 1.0배, 인덱스 크기는 1.71배 |
| 008 | [대량 INSERT](labs/008-bulk-insert) | 1만 건을 한 건마다 COMMIT 1,948.84ms → 트랜잭션 하나 8.08ms(241.2배). 1,000건 단위 커밋도 10.16ms. 대신 하나로 묶으면 1건 실패에 0건이 남음 |
| 009 | [COUNT(*) 비용과 대안](labs/009-count-cost) | 100만 건 중 59.9만 건 세기: 인덱스 없음 21.16ms, 인덱스 6.12ms, 트리거 카운터 0.0005ms. 대신 카운터 때문에 INSERT 2.90배·DELETE 4.73배 느려짐. 있는지만 볼 땐 EXISTS, 단 결과가 없으면 1.0배 |
| 010 | [Lost update와 낙관적 락](labs/010-lost-update-optimistic-lock) | 한 행에 +1 요청 1,600번: 락 없이 읽고-쓰기는 최종 값 200(1,400건 유실), `version` 낙관적 락은 1,600. 대신 재시도 5,995번에 시간 2.09배, 요청이 흩어지면 1.03배 |
| 011 | [비관적 락과 트레이드오프](labs/011-pessimistic-lock) | 한 행에 +1 요청 1,600번: 행을 먼저 잠그면 재시도 0번, p99 174.32ms → 18.92ms. 전체 시간은 2,767.2ms vs 2,771.7ms로 같고 중앙값은 1.66ms → 13.62ms. 락 하나로 전부 묶으면 흩어진 요청도 2.49배 느려짐 |
| 012 | [재고 차감 동시성](labs/012-stock-decrement-concurrency) | 재고 100개에 주문 400건: 읽고-쓰기는 400개를 팔고(300개 초과), `qty - 1`만 바꾸면 107개에 재고 -7. 원자적 UPDATE는 정확히 100개, 주문 800건은 락보다 2.17배 빠름(634.9ms vs 1,379.7ms). 대신 장바구니처럼 여러 행이면 트랜잭션 필요 |
| 013 | [격리 수준 현상](labs/013-isolation-anomalies) | A를 읽고 0.2ms 뒤 B를 읽는 합계 보고서 1,000건 중 235건이 틀림(문장마다 다른 시점을 읽음). 읽기 트랜잭션으로 묶으면 0건, 시간은 같음(p50 0.315ms vs 0.314ms). 대신 스냅샷을 열어 두면 커밋 3,000번에 WAL 3.93MB → 11.79MB(3.0배) |
| 014 | [멱등성 키로 중복 결제 막기](labs/014-idempotency-key) | 같은 결제 요청 3번씩 주문 200건: 키를 먼저 조회하면 DB 행은 200건인데 PG 결제는 599번, 결제 전에 키부터 저장하면 200번. 재시도는 같은 키여야 함(시도마다 새 키면 400번). 대가는 요청당 0.148ms, PG 2ms 포함 1.22배 |
| 015 | [데드락과 락 순서 고정](labs/015-deadlock-lock-order) | 계좌 2개 사이 이체 200건을 양방향으로: 순서 없이 잠그면 데드락 250번에 521.0ms(감지 없이 30ms 타임아웃만 쓰면 6,658.9ms), id 작은 계좌부터 잠그면 0번에 345.1ms. 계좌 1,000개로 흩어지면 셋 다 0번, 1.0배 |
| 016 | [캐시 없음 vs LRU 캐시](labs/016-lru-cache-latency) | 상품 페이지 2만 건: 인기 상품에 몰리면 LRU 1,000개로 209.6ms → 77.5ms(히트 67.1%, 2.70배), 고르게 흩어지면 1.07배(히트 9.7%). p50은 0.0104 → 0.0001ms인데 p99는 거의 그대로. 요청 2%가 쓰기면 무효화 없이 36.3%가 옛 값 |
| 017 | [Cache stampede와 single-flight](labs/017-cache-stampede-single-flight) | 캐시 만료 순간 같은 키로 요청 32개: 각자 쿼리하면 32번에 140.6ms(p99 161.63ms), single-flight로 합치면 1번에 16.6ms(8.5배). 키 32개로 흩어지면 0.91배로 이득 없음. SWR은 p99 0.47ms 대신 만료된 값을 줌 |
| 018 | [TTL + jitter로 동시 만료 피하기](labs/018-ttl-jitter) | 키 1,000개를 같은 TTL 60초로 한꺼번에 채우면 60초마다 만료가 몰려 가장 바쁜 1초에 DB 조회 592번. TTL에 0~20%를 더하면 96번(±20%는 52번), 전체 조회 수는 거의 같음 |
| 019 | [캐시 쓰기 전략](labs/019-cache-write-policies) | 키 10개에 쓰기 100번: cache-aside 읽기 미스 100번, write-through 0번. 키별 병합 write-behind는 UPDATE 50번, 대신 95번째 응답 후 캐시 유실 시 DB에 최신 값 없는 키 10개 |
| 020 | [Rate limiter](labs/020-rate-limiter) | 999/1000ms 경계 요청 10개: fixed window 허용 10개, exact sliding log·token bucket 5개. 0/800ms burst는 token bucket 9개로 strict rolling 한도와 다름 |
| 021 | [재시도 + 지수 백오프 + jitter](labs/021-retry-backoff-jitter) | 1초 장애 뒤 클라이언트 100개 재시도: 지수 백오프만 쓰면 계속 함께 몰려 복구 후 거절 450번, 마지막 성공 28,700ms. full jitter면 거절 9번, 4,048ms(100 seed low median) |
| 022 | [Circuit breaker 상태 전이](labs/022-circuit-breaker) | 초당 100개 요청, 1초 타임아웃: 장애 중 타임아웃 호출이 breaker 없음 550번 → 109~118번(장애 길이 901가지 low median). 대신 복구 후 거절 OPEN 500ms 75개, 5000ms 330개. 동시에 매달린 호출은 모두 100개 |
| 023 | [커넥션 풀 크기와 대기 시간](labs/023-connection-pool-size) | 4코어 DB, 초당 800개: 풀 2개는 2,478개가 커넥션 대기 1초 뒤 포기, 4개부터 0개. 그 위로는 대기 장소만 DB로 옮겨 p99 25.80 → 40.86ms. 쿼리 뒤 20ms 더 쥐면 16개도 805개 포기(평균 사용 21.5개) |
| 024 | [동기 vs 스레드 풀 vs asyncio](labs/024-sync-threadpool-asyncio) | 50ms 서버에 호출 100번: 순차 5,281.5ms, 스레드 100개 60.5ms, asyncio gather 65.3ms. 같은 동시 수면 비슷(16개: 379.7 vs 383.1ms). `async def` 안 블로킹 호출은 5,254.4ms. CPU 반복문을 붙이면 스레드 100개도 579.2ms |
| 025 | [타임아웃 없는 외부 호출](labs/025-external-call-timeout) | 워커 8개, 외부 API가 2초 멈춤: 타임아웃 없으면 상관없는 `/health`도 최대 6,437.2ms, 읽기 타임아웃 200ms면 0.3ms(주문은 202.1ms에 504). 연결 타임아웃만은 6,439.1ms로 같고, 100ms마다 1바이트씩 오면 읽기 타임아웃도 6,564.7ms. 전체 기한을 둬야 끊김 |
| 026 | [대용량 CSV: 한 번에 읽기 vs 스트리밍](labs/026-csv-load-vs-streaming) | 주문 40만 행 23.4MB CSV: `list(csv.reader(f))`는 메모리 최대치 +187.2MB(DictReader +245.5MB), 한 행씩 읽으면 +0.1MB에 시간도 201.2 → 152.8ms. 중앙값처럼 값 전체가 필요하면 금액 열만 남겨 +24.8MB |
| 027 | [정렬된 두 리스트 병합 vs 해시 조인](labs/027-merge-join-vs-hash-join) | 8만×2만 행 join: 이미 정렬된 입력에서 병합 195.8ms, 해시 57.0ms. 원본 입력을 정렬까지 하면 병합 200.2ms. 두 방식 결과 159,975행 일치 |
| 028 | [직렬화 비용: json vs pickle vs struct](labs/028-serialization-json-pickle-struct) | 주문 10만 건: json 9.01MB, pickle 3.07MB, struct 2.10MB(4.29배). zlib 압축 뒤엔 1.50 vs 1.21MB로 1.24배. 필드를 추가하면 옛 struct 코드가 21건을 에러 없이 25건으로 잘못 읽음 |
| 029 | [Bloom filter로 존재 여부 거르기](labs/029-bloom-filter) | 키 10만 개를 125KB(키당 10비트)에 넣자 90%가 없는 키인 조회 10만 건 중 저장소까지 간 조회가 10,728건. 오탐률 0.84%(공식 0.82%), 놓친 키 0. 2배를 넣으면 오탐률 13.88%, 비트를 지워 키 100개를 삭제하면 남은 키 486개가 '없음' |
| 030 | [Consistent hashing과 키 이동량](labs/030-consistent-hashing) | 키 10만 개, 노드 10→11대: `hash % N`은 90.89%가 옮겨졌고, 가상 노드 100개 해시 링은 9.27%(이상값 9.09%)만 새 노드로. 가상 노드 없이 점 하나면 가장 바쁜 노드가 평균의 3.72배 |
| 031 | [Transactional outbox](labs/031-transactional-outbox) | 주문 1만 건, 단계 사이 죽기 2%: 커밋 후 발행은 이벤트 197건 유실, 발행 후 커밋은 없는 주문 이벤트 317건. outbox는 유실·유령 0, 대신 216건 중복이라 소비자가 이벤트 id로 걸러 9,905건 처리 |
| 032 | [멱등 소비자(inbox)](labs/032-idempotent-consumer) | 이벤트 1만 건, 재전송 3%·반영 뒤 죽기 2%: 그냥 반영 548건 초과, 반영 후 id 따로 기록 223건 초과, inbox와 반영을 한 트랜잭션에 넣으면 0 |
| 033 | [Dead letter queue](labs/033-dead-letter-queue) | 메시지 1만 건 중 poison 129건, 시도 한도 12,000번: 제자리 무한 재시도는 정상 메시지 38건만 처리, 최대 3번 시도 뒤 DLQ는 정상 9,871건 모두 처리하고 DLQ에는 poison 129건만 |
| 034 | [UPSERT](labs/034-upsert-on-conflict) | 같은 새 키에 동시 요청 1,000쌍: 조회 후 쓰기는 중복 키 오류 1,000번·증가분 1,000 사라짐, ON CONFLICT는 0이고 10만 건 98ms 대 45ms |
| 035 | [Soft delete와 부분 인덱스](labs/035-soft-delete-partial-index) | 회원 10만 행 중 9만 행 삭제: 전체 UNIQUE는 탈퇴 이메일 재가입 1,000번 모두 오류·인덱스 757페이지, `WHERE deleted_at IS NULL` 부분 UNIQUE는 재가입 오류 0·활동 중 중복 1,000번은 모두 막고 74페이지 |
| 036 | [ORDER BY ... LIMIT 정렬 인덱스](labs/036-order-by-limit-index) | 주문 20만 행, 회원 한 명의 최근 20건: `INDEX(user_id)`는 임시 B-tree 정렬·요청 1번 VM 8,295단계·1,000번 192.8ms, `INDEX(user_id, created_at)`는 정렬 없음·133단계·4.5ms |
| 037 | [긴 트랜잭션이 막는 정리 작업](labs/037-long-transaction-wal-growth) | 1만 행에 1,000건씩 20번 UPDATE: 긴 읽기 없음은 정리 실패 0/20·WAL 0 MB, 긴 읽기를 열어 두면 20/20 실패·WAL 17.65 MB, 닫으면 다음 정리에서 0 MB |
| 038 | [대량 DELETE 뒤 파일 크기](labs/038-bulk-delete-freelist-vacuum) | 로그 20만 행 중 18만 행 DELETE: 파일은 27.70 MB 그대로·빈 페이지 6,373개, 다시 넣으면 빈 페이지 재사용(27.91 MB), `VACUUM` 2.77 MB, `incremental_vacuum` 2.81 MB |
| 039 | [분산 락 lease 초과와 fencing token](labs/039-lease-lock-fencing-token) | 작업자 8명이 임대 30 ms 락으로 1만 번 증가: 임대 락만 9,756(244 잃음), fencing token을 저장소가 확인하면 늦은 쓰기 216건 거절·재시도로 10,000 |
| 040 | [Saga 보상 처리](labs/040-saga-compensation) | 주문 1만 건 중 1,148건 실패: 보상 없으면 1,181건 어긋남, 1번만 시도 73건, 재시도는 중복 반영 61건, 멱등 키 재시도는 0건 |
| 041 | [Read replica 지연](labs/041-read-replica-lag) | 쓰기 1만 건 직후 읽기: 항상 replica는 1,873건(18.73%) 옛 값, 쓴 뒤 1초 primary는 0건·primary 읽기 20.0%, 반영 위치 확인은 0건·3.7% |
| 042 | [Negative caching](labs/042-negative-caching) | 없는 키 조회 3만 71건: 매번 저장소는 3만 71번, TTL 5초는 1만 8,257번(39.3% 절감)·틀린 "없음" 5건, TTL 30초는 9,784번(67.5%)·61건, 만들 때 지우면 0건 |
| 043 | [캐시 워밍업](labs/043-cache-warmup) | 재시작 직후 10초(요청 1만 건): 워밍업 없으면 적중률 78.5%·저장소 2,146번, 인기 키 500개 미리 채우면 83.3%·1,674번(22.0% 감소), 2,000개는 84.0%·1,596번이지만 전체 조회는 17,341번 |
| 044 | [2단계 캐시(로컬 + 분산)](labs/044-two-level-cache) | 읽기 9만 7,985건: 분산 캐시만 평균 1.090 ms·옛 값 0건, 로컬 TTL 1초 앞에 두면 0.506 ms(53.6% 감소)·옛 값 17,488건(17.85%), TTL 10초는 0.291 ms·39,802건(40.62%) |
| 045 | [Cache-aside 갱신 경쟁](labs/045-cache-aside-race) | 10만 번 중 20,466번에서 늦게 도착한 옛 값이 캐시에 남음. 삭제만 하면 TTL 10초, 500 ms 뒤 한 번 더 삭제하면 평균 498 ms, 버전 확인 후 저장하면 0건 |
| 046 | [캐시 무효화 지연](labs/046-cache-invalidation-delay) | 읽기 100만 건(쓰기 2,000번, 쓰기 뒤 1초): 동기 삭제는 옛 값 0건, 비동기 삭제 큐(평균 50 ms)는 51,832건(5.18%), 꼬리 지연을 섞으면 59,210건(5.92%) |

다음 주제는 [ROADMAP.md](ROADMAP.md)에 있어요.

</details>

### 서비스에서 쓴 곳

공개 저장소 [carryon-app](https://github.com/morningverse67-wq/carryon-app)의 웹사이트 통계 서버(Cloudflare Pages Functions + D1)를 직접 운영하며 같은 방식으로 확인한 일이에요.

- 배포 점검 요청이 다운로드 통계에 섞였던 일을 점검용 표시와 테스트 26개로 정리했어요(`lib/stats.test.mjs`).
- 하루 단위 통계가 UTC와 한국 날짜 사이에서 어긋나는 문제를, 저장 시점부터 한국 날짜로 적는 방식으로 풀었어요.
- GitHub 트래픽이 14일만 남아서, 매일 한 번 복사해 D1에 쌓아요.

### 함께 보면 좋은 저장소

- [backend-interview-lab](https://github.com/morningverse67-wq/backend-interview-lab): 질문 44개의 30초 답 모음
- Velog [@morningverse](https://velog.io/@morningverse): 시리즈 「면접 질문 하나, 숫자 하나」
