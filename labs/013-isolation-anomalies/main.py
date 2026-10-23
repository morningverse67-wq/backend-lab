"""
Lab 013: Transaction isolation anomalies - what a read actually sees

Interview answers list dirty read, non-repeatable read and phantom read per
isolation level. This lab reproduces them step by step with two connections
on one SQLite file DB (WAL), then measures what they cost in practice:

Part 1 (step by step, deterministic):
  - dirty read          : another connection's uncommitted UPDATE, later rolled back
                          * shared cache + PRAGMA read_uncommitted = 1  (READ UNCOMMITTED)
                          * shared cache, default                       (table lock error)
                          * normal WAL connection                       (committed value)
  - non-repeatable read : read the same row twice, another connection commits in between
  - phantom read        : COUNT(*) the same range twice, another connection inserts in between
  - write on a snapshot : read in a transaction, someone commits, then UPDATE
  Each one: autocommit reads (every statement sees the latest commit) vs
  one read transaction (BEGIN ... COMMIT, one snapshot for the whole thing).

Part 2 (threads): a "total balance" report reads account A, then account B,
while a writer moves money between them about every 1ms (A + B is always 2,000).
How many reports show a wrong total, and what does the read transaction cost?

Part 3 (trade-off): a read transaction that stays open keeps its snapshot,
so the WAL file cannot be reset. How big does it grow?

Run:
    python3 main.py
"""
import os
import random
import sqlite3
import statistics
import sys
import tempfile
import threading
import time

READERS = 4
REPORTS = 250        # per reader thread
GAP = 0.0002         # seconds between the two reads of a report
PACE = 0.001         # writer pause between transfers (about 1ms)
ROUNDS = 3           # median of 3 rounds is reported
WAL_COMMITS = 3000


def connect(path, timeout=10, uri=False):
    conn = sqlite3.connect(path, isolation_level=None, timeout=timeout, uri=uri,
                           check_same_thread=False)  # autocommit
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    return conn


def remove_db(path):
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


def new_db(path):
    remove_db(path)
    conn = connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, balance INTEGER NOT NULL)")
    conn.execute("INSERT INTO accounts VALUES (1, 1000), (2, 1000)")
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount INTEGER NOT NULL)")
    conn.executemany("INSERT INTO orders (amount) VALUES (?)", [(50,), (120,), (300,), (700,)])
    conn.close()


def bal(conn, account_id=1):
    return conn.execute("SELECT balance FROM accounts WHERE id = ?", (account_id,)).fetchone()[0]


def big_orders(conn):
    return conn.execute("SELECT COUNT(*) FROM orders WHERE amount >= 100").fetchone()[0]


# ---- Part 1 ------------------------------------------------------------------

def dirty_read(path):
    new_db(path)
    shared = "file:" + path + "?cache=shared"
    writer = connect(shared, uri=True)
    ru = connect(shared, uri=True)
    ru.execute("PRAGMA read_uncommitted = 1")
    shared_default = connect(shared, uri=True)
    normal = connect(path)

    writer.execute("BEGIN")
    writer.execute("UPDATE accounts SET balance = 0 WHERE id = 1")   # not committed
    seen_ru = bal(ru)
    try:
        seen_shared = bal(shared_default)
    except sqlite3.OperationalError as e:
        seen_shared = f"error: {e}"
    seen_normal = bal(normal)
    writer.execute("ROLLBACK")
    final = bal(normal)
    for c in (writer, ru, shared_default, normal):
        c.close()
    assert final == 1000 and seen_normal == 1000
    assert seen_ru == 0                                  # a value that was never committed
    assert str(seen_shared).startswith("error: database table is locked")
    return seen_ru, seen_shared, seen_normal, final


def twice(path, in_tx, read, change):
    """Read twice with one connection; another connection commits `change` in between."""
    new_db(path)
    reader, other = connect(path), connect(path)
    if in_tx:
        reader.execute("BEGIN")
    first = read(reader)
    other.execute(change)                                # autocommit = committed now
    second = read(reader)
    if in_tx:
        reader.execute("COMMIT")
    after = read(reader)
    reader.close()
    other.close()
    return first, second, after


def write_on_snapshot(path):
    new_db(path)
    reader, other = connect(path), connect(path)
    reader.execute("BEGIN")
    first = bal(reader)
    other.execute("UPDATE accounts SET balance = 500 WHERE id = 1")
    start = time.perf_counter()
    try:
        reader.execute("UPDATE accounts SET balance = ? WHERE id = 1", (first - 100,))
        result = "ok"
    except sqlite3.OperationalError as e:
        result = f"error: {e}"
    ms = (time.perf_counter() - start) * 1000
    reader.execute("ROLLBACK")
    final = bal(reader)
    reader.close()
    other.close()
    assert result == "error: database is locked" and final == 500, (result, final)
    return first, result, ms, final


# ---- Part 2 ------------------------------------------------------------------

def reports(path, in_tx, seed):
    new_db(path)
    stop = threading.Event()
    transfers = [0]
    rng = random.Random(seed)

    def writer():
        conn = connect(path)
        n = 0
        while not stop.is_set():
            amount = rng.randint(1, 100)
            src, dst = (1, 2) if rng.random() < 0.5 else (2, 1)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE accounts SET balance = balance - ? WHERE id = ?", (amount, src))
            conn.execute("UPDATE accounts SET balance = balance + ? WHERE id = ?", (amount, dst))
            conn.execute("COMMIT")
            n += 1
            time.sleep(PACE)
        conn.close()
        transfers[0] = n

    totals, times = [], []
    out = threading.Lock()
    barrier = threading.Barrier(READERS)

    def reader():
        conn = connect(path)
        mine, ms = [], []
        barrier.wait()
        for _ in range(REPORTS):
            start = time.perf_counter()
            if in_tx:
                conn.execute("BEGIN")
            a = bal(conn, 1)
            time.sleep(GAP)                  # e.g. formatting the first line of the report
            b = bal(conn, 2)
            if in_tx:
                conn.execute("COMMIT")
            ms.append((time.perf_counter() - start) * 1000)
            mine.append(a + b)
        conn.close()
        with out:
            totals.extend(mine)
            times.extend(ms)

    w = threading.Thread(target=writer)
    w.start()
    rs = [threading.Thread(target=reader) for _ in range(READERS)]
    for t in rs:
        t.start()
    for t in rs:
        t.join()
    stop.set()
    w.join()
    conn = connect(path)
    final_total = bal(conn, 1) + bal(conn, 2)
    conn.close()
    assert final_total == 2000                           # the writer itself is always correct
    wrong = sum(t != 2000 for t in totals)
    return {"wrong": wrong, "reports": len(totals), "transfers": transfers[0],
            "p50": statistics.median(times),
            "worst_gap": max(abs(t - 2000) for t in totals)}


# ---- Part 3 ------------------------------------------------------------------

def wal_growth(path, hold_snapshot):
    new_db(path)
    conn = connect(path)
    conn.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY, v INTEGER)")
    conn.executemany("INSERT INTO logs (v) VALUES (?)", [(i,) for i in range(1000)])
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    long_reader = None
    if hold_snapshot:
        long_reader = connect(path)
        long_reader.execute("BEGIN")
        first = bal(long_reader)
    start = time.perf_counter()
    for i in range(WAL_COMMITS):
        conn.execute("UPDATE logs SET v = v + 1 WHERE id = ?", (i % 1000 + 1,))
    ms = (time.perf_counter() - start) * 1000
    size = os.path.getsize(path + "-wal")
    if long_reader:
        assert bal(long_reader) == first                 # still the old snapshot
        long_reader.execute("COMMIT")
        long_reader.close()
    total = conn.execute("SELECT SUM(v) FROM logs").fetchone()[0]
    assert total == sum(range(1000)) + WAL_COMMITS
    conn.close()
    return size, ms


def main():
    path = os.path.join(tempfile.gettempdir(), "lab013.db")
    print(f"file SQLite (WAL), Python {sys.version.split()[0]}, SQLite {sqlite3.sqlite_version}\n")

    print("Part 1. step by step (two connections)")
    ru, shared, normal, final = dirty_read(path)
    print("dirty read: UPDATE balance 1000 -> 0, not committed, then ROLLBACK")
    print(f"  shared cache + read_uncommitted=1 : saw {ru}  (final value {final})")
    print(f"  shared cache, default             : {shared}")
    print(f"  normal WAL connection             : saw {normal}")

    cases = [
        ("non-repeatable read", bal, "UPDATE accounts SET balance = 500 WHERE id = 1",
         (1000, 500)),
        ("phantom read (COUNT amount >= 100)", big_orders,
         "INSERT INTO orders (amount) VALUES (150)", (3, 4)),
    ]
    for title, read, change, (before, changed) in cases:
        print(f"{title}: other connection commits in between")
        for in_tx in (False, True):
            first, second, after = twice(path, in_tx, read, change)
            label = "one read transaction" if in_tx else "autocommit reads"
            expected_second = before if in_tx else changed
            assert (first, second, after) == (before, expected_second, changed)
            print(f"  {label:<21}: first {first}, second {second}, after COMMIT {after}")

    first, result, ms, final = write_on_snapshot(path)
    print(f"write on an old snapshot: read {first}, other commits 500, then UPDATE -> "
          f"{result} after {ms:.3f}ms (busy_timeout 10s), final {final}\n")

    print(f"Part 2. report = read A, wait {GAP * 1000:.1f}ms, read B (A + B is always 2,000)")
    print(f"{READERS} reader threads x {REPORTS} reports, 1 writer moving money every ~{PACE * 1000:.0f}ms, "
          f"median of {ROUNDS} rounds")
    header = f"{'reads':<21} {'wrong total':>12} {'worst diff':>10} {'p50 ms':>7} {'transfers':>10}"
    print(header)
    print("-" * len(header))
    for in_tx in (False, True):
        results = [reports(path, in_tx, seed) for seed in range(ROUNDS)]
        if in_tx:
            assert all(r["wrong"] == 0 for r in results)   # one snapshot = consistent total
        med = {k: statistics.median(r[k] for r in results) for k in results[0]}
        label = "one read transaction" if in_tx else "autocommit reads"
        print(f"{label:<21} {med['wrong']:>6,.0f}/{med['reports']:,.0f} {med['worst_gap']:>10,.0f} "
              f"{med['p50']:>7.3f} {med['transfers']:>10,.0f}")
        print(f"{'  rounds':<21} wrong {[r['wrong'] for r in results]}, "
              f"p50 {[round(r['p50'], 3) for r in results]}, "
              f"transfers {[r['transfers'] for r in results]}")
    print()

    print(f"Part 3. {WAL_COMMITS:,} small commits, WAL file size at the end")
    for hold in (False, True):
        sizes = [wal_growth(path, hold) for _ in range(ROUNDS)]
        size = statistics.median(s for s, _ in sizes)
        ms = statistics.median(m for _, m in sizes)
        label = "read tx held open" if hold else "no open reader"
        print(f"  {label:<18}: WAL {size / 1024 / 1024:.2f} MB, writes {ms:.1f}ms"
              f"  (rounds MB {[round(s / 1024 / 1024, 2) for s, _ in sizes]})")
    remove_db(path)


if __name__ == "__main__":
    main()
