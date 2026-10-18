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
- [ ] COUNT(*) 비용과 대안

## 트랜잭션 / 동시성
- [ ] Lost update 재현과 낙관적 락(version 컬럼)
- [ ] 비관적 락(SELECT ... FOR UPDATE 개념)과 트레이드오프
- [ ] 재고 차감 동시성: 원자적 UPDATE vs 읽고-쓰기
- [ ] 트랜잭션 격리 수준별 현상 (dirty/non-repeatable/phantom)
- [ ] 멱등성 키로 중복 결제 요청 막기
- [ ] 데드락 재현과 락 순서 고정

## 캐시
- [ ] 캐시 없음 vs LRU 캐시 응답 시간
- [ ] Cache stampede와 single-flight
- [ ] TTL + jitter로 동시 만료 피하기
- [ ] Write-through vs write-behind vs cache-aside

## API / 서버
- [ ] Rate limiter: fixed window vs sliding window vs token bucket
- [ ] 재시도 + 지수 백오프 + jitter
- [ ] Circuit breaker 상태 전이
- [ ] 커넥션 풀 크기와 대기 시간
- [ ] 동기 vs 스레드풀 vs asyncio로 I/O 작업 처리
- [ ] 타임아웃 없는 외부 호출이 서버를 막는 과정

## 데이터 처리
- [ ] 대용량 CSV: 한 번에 읽기 vs 스트리밍 (메모리 측정)
- [ ] 정렬된 두 리스트 병합 vs 해시 조인
- [ ] 직렬화 비용: json vs pickle vs struct
- [ ] Bloom filter로 존재 여부 빠르게 거르기
- [ ] Consistent hashing과 노드 추가 시 키 이동량
