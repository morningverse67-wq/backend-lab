"""031. Transactional outbox: DB 저장과 메시지 발행을 같이 할 때 생기는 유실·유령·중복.

주문 10,000건을 SQLite에 저장하고 '주문 생성' 이벤트를 브로커(리스트)에 보낸다.
장애는 고정 시드 난수로 넣는다. 표준 라이브러리만 사용.
"""
import random
import sqlite3

N = 10_000
CRASH = 0.02      # 프로세스가 두 단계 사이에서 죽을 확률
DB_FAIL = 0.01    # DB 커밋이 실패(롤백)할 확률
SEED = 31


def new_db():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY)")
    db.execute("CREATE TABLE outbox(id INTEGER PRIMARY KEY, order_id INT, sent INT DEFAULT 0)")
    return db


def check(db, broker):
    saved = {r[0] for r in db.execute("SELECT id FROM orders")}
    sent = [e for e in broker]
    uniq = set(sent)
    lost = len(saved - uniq)
    ghost = len(uniq - saved)
    dup = len(sent) - len(uniq)
    return len(saved), len(sent), lost, ghost, dup


def commit_then_publish(rng):
    db, broker = new_db(), []
    for i in range(N):
        if rng.random() < DB_FAIL:
            continue  # 커밋 실패: 저장도 발행도 안 함
        with db:
            db.execute("INSERT INTO orders VALUES(?)", (i,))
        if rng.random() < CRASH:
            continue  # 커밋 뒤, 발행 전에 죽음
        broker.append(i)
    return check(db, broker)


def publish_then_commit(rng):
    db, broker = new_db(), []
    for i in range(N):
        broker.append(i)
        if rng.random() < CRASH:
            continue  # 발행 뒤, 커밋 전에 죽음
        if rng.random() < DB_FAIL:
            continue  # 커밋 실패: 이벤트는 이미 나감
        with db:
            db.execute("INSERT INTO orders VALUES(?)", (i,))
    return check(db, broker)


def outbox(rng):
    db, broker = new_db(), []
    for i in range(N):
        if rng.random() < DB_FAIL:
            continue
        with db:  # 주문과 이벤트를 한 트랜잭션에
            db.execute("INSERT INTO orders VALUES(?)", (i,))
            db.execute("INSERT INTO outbox(id, order_id) VALUES(?, ?)", (i, i))
    rounds = 0
    while True:  # 릴레이: 안 보낸 행을 보내고 sent=1. 보낸 뒤 표시 전에 죽을 수 있음
        rows = db.execute("SELECT id, order_id FROM outbox WHERE sent=0 ORDER BY id").fetchall()
        if not rows:
            break
        rounds += 1
        for oid, order_id in rows:
            broker.append(order_id)
            if rng.random() < CRASH:
                continue  # 발행 뒤, sent 표시 전에 죽음 -> 다음 회차에 다시 보냄
            with db:
                db.execute("UPDATE outbox SET sent=1 WHERE id=?", (oid,))
    return check(db, broker) + (rounds,), broker


def consume(broker):
    seen, applied = set(), 0
    for e in broker:
        if e in seen:
            continue  # 처리한 이벤트 id 기록으로 중복 무시
        seen.add(e)
        applied += 1
    return applied


def main():
    print(f"주문 {N:,}건, 단계 사이 죽을 확률 {CRASH:.0%}, 커밋 실패 {DB_FAIL:.0%}, seed {SEED}\n")
    print(f"{'방식':<22}{'저장':>7}{'발행':>8}{'유실':>6}{'유령':>6}{'중복':>6}")
    for name, fn in [("커밋 후 발행", commit_then_publish), ("발행 후 커밋", publish_then_commit)]:
        s = fn(random.Random(SEED))
        print(f"{name:<22}{s[0]:>7,}{s[1]:>8,}{s[2]:>6}{s[3]:>6}{s[4]:>6}")
    s, broker = outbox(random.Random(SEED))
    print(f"{'outbox + 릴레이':<22}{s[0]:>7,}{s[1]:>8,}{s[2]:>6}{s[3]:>6}{s[4]:>6}  (릴레이 {s[5]}회)")
    print(f"\noutbox 소비자: 중복 무시 없이 {len(broker):,}번 처리, 이벤트 id로 거르면 {consume(broker):,}번 처리")


if __name__ == "__main__":
    main()
