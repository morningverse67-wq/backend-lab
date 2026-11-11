"""032. 멱등 소비자(inbox): 최소 한 번 전달된 이벤트를 한 번만 반영하기.

포인트 적립 이벤트 10,000건을 소비자가 처리한다. 브로커는 3% 확률로 같은 이벤트를
다시 보내고(at-least-once), 소비자는 2% 확률로 '반영 뒤, 처리 기록 전'에 죽는다.
죽으면 ack가 안 가서 그 이벤트는 다시 온다. 표준 라이브러리만 사용.
"""
import random
import sqlite3

N = 10_000
REDELIVER = 0.03  # 브로커가 같은 이벤트를 한 번 더 보낼 확률
CRASH = 0.02      # 반영 뒤, 처리 기록 전에 죽을 확률
POINT = 100
SEED = 32


def new_db():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE account(id INTEGER PRIMARY KEY, point INT)")
    db.execute("INSERT INTO account VALUES(1, 0)")
    db.execute("CREATE TABLE inbox(event_id INTEGER PRIMARY KEY)")
    return db


def deliveries(rng):
    """브로커가 소비자에게 넘기는 이벤트 순서(중복 포함)."""
    queue = []
    for i in range(N):
        queue.append(i)
        if rng.random() < REDELIVER:
            queue.append(i)
    return queue


def run(mode, rng):
    db = new_db()
    queue = deliveries(rng)
    received = 0
    while queue:
        e = queue.pop(0)
        received += 1
        crash = rng.random() < CRASH
        if mode == "plain":
            with db:
                db.execute("UPDATE account SET point = point + ? WHERE id=1", (POINT,))
            if crash:
                queue.append(e)  # ack 전에 죽음 → 다시 옴
        elif mode == "record_after":
            if db.execute("SELECT 1 FROM inbox WHERE event_id=?", (e,)).fetchone():
                continue
            with db:
                db.execute("UPDATE account SET point = point + ? WHERE id=1", (POINT,))
            if crash:
                queue.append(e)  # 반영은 커밋됐는데 기록 전에 죽음
                continue
            with db:
                db.execute("INSERT INTO inbox VALUES(?)", (e,))
        elif mode == "inbox_tx":
            try:
                with db:  # 기록과 반영을 한 트랜잭션에
                    db.execute("INSERT INTO inbox VALUES(?)", (e,))
                    if crash:
                        raise RuntimeError("crash")
                    db.execute("UPDATE account SET point = point + ? WHERE id=1", (POINT,))
            except sqlite3.IntegrityError:
                continue  # 이미 처리한 이벤트
            except RuntimeError:
                queue.append(e)  # 롤백됐고 다시 옴
    point = db.execute("SELECT point FROM account").fetchone()[0]
    applied = point // POINT
    return received, applied, applied - N


def main():
    names = {"plain": "그냥 반영", "record_after": "반영 후 id 기록(따로 커밋)",
             "inbox_tx": "inbox + 반영 한 트랜잭션"}
    print(f"이벤트 {N:,}건, 재전송 {REDELIVER:.0%}, 반영 뒤 죽기 {CRASH:.0%}")
    print(f"{'방식':<28}{'받은 횟수':>10}{'반영 횟수':>10}{'초과 반영':>10}")
    for mode in names:
        r, a, extra = run(mode, random.Random(SEED))
        print(f"{names[mode]:<28}{r:>10,}{a:>10,}{extra:>10,}")
    print(f"정답 포인트: {N * POINT:,}")


if __name__ == "__main__":
    main()
