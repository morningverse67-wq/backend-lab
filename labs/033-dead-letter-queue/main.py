"""033. 독이 든 메시지(poison message)와 dead letter queue.

메시지 10,000건 중 1%는 몇 번을 다시 처리해도 항상 실패하고(poison), 5%는 첫 시도만
실패하고 다시 하면 성공한다(일시 오류). 소비자가 처리 시도를 최대 12,000번만 할 수 있을 때
재시도 정책에 따라 정상 메시지가 얼마나 처리되는지 센다. 표준 라이브러리만 사용.
"""
import random
from collections import deque

N = 10_000
POISON = 0.01
TRANSIENT = 0.05
BUDGET = 12_000  # 소비자가 쓸 수 있는 처리 시도 횟수
SEED = 33


def make_messages(rng):
    kinds = []
    for _ in range(N):
        r = rng.random()
        kinds.append("poison" if r < POISON else "transient" if r < POISON + TRANSIENT else "ok")
    return kinds


def attempt(kind, tries_before):
    """이 메시지를 (tries_before+1)번째로 처리했을 때 성공 여부."""
    if kind == "ok":
        return True
    if kind == "transient":
        return tries_before >= 1
    return False


def run(policy):
    kinds = make_messages(random.Random(SEED))
    tries = [0] * N
    queue = deque(range(N))
    used = done = 0
    dlq = []
    while queue and used < BUDGET:
        m = queue.popleft()
        ok = attempt(kinds[m], tries[m])
        tries[m] += 1
        used += 1
        if ok:
            done += 1
        elif policy == "block":      # 성공할 때까지 그 자리에서 재시도
            queue.appendleft(m)
        elif policy == "requeue":    # 실패하면 맨 뒤에 다시 넣기(횟수 제한 없음)
            queue.append(m)
        elif policy == "dlq1":       # 재시도 없이 바로 DLQ
            dlq.append(m)
        elif policy == "dlq3":       # 최대 3번 시도 뒤 DLQ
            if tries[m] >= 3:
                dlq.append(m)
            else:
                queue.append(m)
    good = sum(k != "poison" for k in kinds)
    good_done = done
    poison_dlq = sum(kinds[m] == "poison" for m in dlq)
    good_dlq = len(dlq) - poison_dlq
    poison_tries = sum(t for t, k in zip(tries, kinds) if k == "poison")
    return used, good_done, good, len(queue), len(dlq), good_dlq, poison_tries


def main():
    names = {"block": "제자리 무한 재시도", "requeue": "맨 뒤로 보내며 무한 재시도",
             "dlq1": "재시도 없이 바로 DLQ", "dlq3": "최대 3번 시도 뒤 DLQ"}
    kinds = make_messages(random.Random(SEED))
    print(f"메시지 {N:,}건 (poison {kinds.count('poison')}, 일시 오류 {kinds.count('transient')}), 시도 한도 {BUDGET:,}")
    print(f"{'정책':<22}{'쓴 시도':>8}{'정상 처리':>10}{'남은 큐':>8}{'DLQ':>6}{'DLQ 속 정상':>10}{'poison 시도':>11}")
    for p, name in names.items():
        used, gd, good, left, dlq, gdlq, pt = run(p)
        print(f"{name:<22}{used:>8,}{gd:>10,}{left:>8,}{dlq:>6,}{gdlq:>10,}{pt:>11,}")


if __name__ == "__main__":
    main()
