# backend-lab

![백엔드 성능 문제를 재현하고 측정하는 backend-lab](assets/cover.png)

백엔드에서 자주 만나는 데이터·성능 문제를 **작게 재현하고, 직접 측정해서, 기록**하는 실험실.

- 실험 하나 = 폴더 하나 (`labs/NNN-topic/`)
- 각 폴더에는 바로 실행되는 코드와 `README.md` (문제 → 실험 → 결과 → 배운 점)
- Python lab: 표준 라이브러리만 사용해서 `python3 main.py` 한 줄로 재현
- Java lab: Maven + Docker(MySQL)로 `mvn test` 재현. 실행 명령은 각 lab README에
- 수치는 직접 돌린 값만 적는다. 환경에 따라 달라질 수 있음을 함께 표시

## Labs

| # | 주제 | 한 줄 요약 | 실행 | Velog 글 |
|---|---|---|---|---|
| 001 | [N+1 쿼리](labs/001-n-plus-one) | 쿼리 501번 vs 1번. 비용의 대부분은 실행이 아니라 왕복 | Python | [N+1 쿼리는 왜 생길까?](https://velog.io/@morningverse/N1-쿼리는-왜-생길까) |
| 002 | [인덱스 vs 풀 스캔](labs/002-index-scan) | 선택적 조회는 887배 빨라지고, 90%를 읽는 조회는 0.9배 | Python | 후속 글 후보 |
| 003 | [MyBatis N+1 (MySQL)](labs/003-mybatis-nplusone) | 중첩 select 21번 → join resultMap 1번, 부모 페이징 + IN 2번. `<id>`를 빼면 15% 느림 | Java | [N+1 쿼리는 왜 생길까?](https://velog.io/@morningverse/N1-쿼리는-왜-생길까) |

다음 주제는 [ROADMAP.md](ROADMAP.md)에.

## Links

- Blog: [velog.io/@morningverse](https://velog.io/@morningverse)
- 면접 질문별 정리: [backend-interview-lab](https://github.com/morningverse67-wq/backend-interview-lab)
