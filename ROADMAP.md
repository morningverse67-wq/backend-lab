# Roadmap

위에서부터 하나씩 진행한다. 끝난 항목은 `[x]`와 lab 번호를 단다.

## DB / 쿼리
- [x] N+1 쿼리: 지연 로딩 vs JOIN vs IN 배치 (001)
- [x] MyBatis N+1: 중첩 select vs join resultMap vs 부모 페이징 + IN, `<id>` 유무 (003)
- [x] 인덱스 유무에 따른 조회 성능과 `EXPLAIN QUERY PLAN` 읽기 (002)
- [x] 복합 인덱스 컬럼 순서가 결과를 바꾸는 경우 (004)
- [x] OFFSET 페이징 vs 커서(keyset) 페이징 (005)
- [x] 인덱스를 못 타는 쿼리: 함수 씌운 컬럼, LIKE '%x', 암묵적 형변환 (006)
- [x] 커버링 인덱스 (007)
- [x] 대량 INSERT: 한 건씩 vs executemany vs 트랜잭션 묶기 (008)
- [x] COUNT(*) 비용과 대안 (009)
- [x] UPSERT: 조회 후 INSERT vs `INSERT ... ON CONFLICT` (034)
- [x] Soft delete와 부분 인덱스(partial index) (035)
- [x] 정렬 인덱스로 ORDER BY ... LIMIT의 정렬 단계 없애기 (036)
- [x] 긴 트랜잭션이 막는 정리 작업: 오래 열린 읽기가 남기는 옛 버전 행 (037)
- [x] 대량 DELETE 뒤 파일 크기: freelist와 VACUUM (038)
- [x] Read replica 지연: 쓰기 직후 읽기에서 옛 값을 읽는 비율 (041)

## 트랜잭션 / 동시성
- [x] Lost update 재현과 낙관적 락(version 컬럼) (010)
- [x] 비관적 락(SELECT ... FOR UPDATE 개념)과 트레이드오프 (011)
- [x] 재고 차감 동시성: 원자적 UPDATE vs 읽고-쓰기 (012)
- [x] 트랜잭션 격리 수준별 현상 (dirty/non-repeatable/phantom) (013)
- [x] 멱등성 키로 중복 결제 요청 막기 (014)
- [x] 데드락 재현과 락 순서 고정 (015)
- [x] 분산 락의 임대 시간(lease) 초과와 fencing token (039)
- [x] Saga 보상 처리: 중간 단계 실패 시 되돌린 건수와 남은 불일치 (040)

## 캐시
- [x] 캐시 없음 vs LRU 캐시 응답 시간 (016)
- [x] Cache stampede와 single-flight (017)
- [x] TTL + jitter로 동시 만료 피하기 (018)
- [x] Write-through vs write-behind vs cache-aside (019)
- [x] Negative caching: 없는 키 조회가 저장소까지 가는 횟수 줄이기 (042)
- [x] 캐시 워밍업: 재시작 직후 적중률과 저장소 부하 (043)
- [x] 2단계 캐시(로컬 + 분산): 적중 위치별 응답 시간과 서버 간 값 불일치 (044)
- [x] Cache-aside 갱신 경쟁: 늦게 도착한 옛 값이 새 값을 덮어쓰는 건수와 버전 확인 (045)
- [x] 캐시 무효화 지연: 삭제 메시지가 늦게 도착하는 동안 읽히는 옛 값 비율 (046)
- [ ] 삭제 재시도 간격: 실패한 캐시 삭제를 몇 ms 뒤에 다시 보내야 옛 값 구간이 줄어드는가
- [ ] 캐시 키 버전 접두어: 한 번에 무효화할 때 삭제 요청 수와 옛 키가 남는 양

## API / 서버
- [x] Rate limiter: fixed window vs exact sliding window log vs token bucket (020)
- [x] 재시도 + 지수 백오프 + jitter (021)
- [x] Circuit breaker 상태 전이 (022)
- [x] 커넥션 풀 크기와 대기 시간 (023)
- [x] 동기 vs 스레드풀 vs asyncio로 I/O 작업 처리 (024)
- [x] 타임아웃 없는 외부 호출이 서버를 막는 과정 (025)
- [ ] Bulkhead: 느린 의존성 하나와 스레드풀 나누기

## 데이터 처리
- [x] 대용량 CSV: 한 번에 읽기 vs 스트리밍 (메모리 측정) (026)
- [x] 정렬된 두 리스트 병합 vs 해시 조인 (027)
- [x] 직렬화 비용: json vs pickle vs struct (028)
- [x] Bloom filter로 존재 여부 빠르게 거르기 (029)
- [x] Consistent hashing과 노드 추가 시 키 이동량 (030)
- [ ] 고유 값 세기: set vs HyperLogLog (메모리와 오차)

## 메시징
- [x] Transactional outbox: DB 저장과 이벤트 발행의 유실·유령·중복 (031)
- [x] 멱등 소비자(inbox): 중복 이벤트를 한 번만 반영 (032)
- [x] Dead letter queue: poison 메시지가 큐를 막지 않게 하기 (033)
- [ ] 파티션 키와 메시지 순서: 병렬 소비자에서 순서가 바뀌는 건수
- [ ] Backpressure: 무한 큐 vs 유한 큐의 메모리와 지연
- [ ] 메시지 재처리(replay): DLQ에 쌓인 메시지를 다시 넣을 때 중복과 순서
- [ ] 커넥션 풀 크기: 풀이 작을 때 대기 시간과 클 때 DB 부하
