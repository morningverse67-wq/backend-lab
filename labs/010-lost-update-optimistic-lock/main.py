"""
Lab 010: Lost update and optimistic locking (version column)

Two requests read the same row, change the value in application code and
write it back. The second write silently overwrites the first one: a lost
update. This lab:
  1. Reproduces it step by step with two connections (no threads), then shows
     that `UPDATE ... WHERE id = ? AND version = ?` rejects the stale write
  2. Runs 8 threads x 200 "+1" requests with a 1ms gap between read and write
     (the time an application spends between SELECT and UPDATE), using
       - plain read-then-write  (no lock)
       - optimistic lock        (version column + retry on 0 updated rows)
     on one hot row and on rows spread over 1,000 rows
  3. Reports lost updates, conflicts (retries), slowest request and total time

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

THREADS = 8
REQUESTS = 200          # per thread
GAP = 0.001             # seconds between SELECT and UPDATE
ROUNDS = 3              # median of 3 rounds is reported
EXPECTED = THREADS * REQUESTS


def connect(path):
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)  # autocommit
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def new_db(path, rows):
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    conn = connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, qty INTEGER NOT NULL, "
        "version INTEGER NOT NULL DEFAULT 0)"
    )
    conn.executemany("INSERT INTO items (id, qty) VALUES (?, 0)",
                     [(i,) for i in range(1, rows + 1)])
    conn.close()


def plain_increment(conn, item_id, stats):
    qty = conn.execute("SELECT qty FROM items WHERE id = ?", (item_id,)).fetchone()[0]
    time.sleep(GAP)
    conn.execute("UPDATE items SET qty = ? WHERE id = ?", (qty + 1, item_id))
    stats["retries"].append(0)


def optimistic_increment(conn, item_id, stats):
    retries = 0
    while True:
        qty, version = conn.execute(
            "SELECT qty, version FROM items WHERE id = ?", (item_id,)).fetchone()
        time.sleep(GAP)
        cur = conn.execute(
            "UPDATE items SET qty = ?, version = version + 1 WHERE id = ? AND version = ?",
            (qty + 1, item_id, version))
        if cur.rowcount == 1:
            stats["retries"].append(retries)
            return
        retries += 1  # someone else changed the row: read again and retry


def run(path, rows, increment, seed):
    new_db(path, rows)
    stats = {"retries": []}
    lock = threading.Lock()
    barrier = threading.Barrier(THREADS)

    def worker(t):
        rnd = random.Random(seed * 100 + t)
        conn = connect(path)
        local = {"retries": []}
        barrier.wait()
        for _ in range(REQUESTS):
            increment(conn, rnd.randint(1, rows), local)
        conn.close()
        with lock:
            stats["retries"].extend(local["retries"])

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(THREADS)]
    start = time.perf_counter()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    elapsed = (time.perf_counter() - start) * 1000

    conn = connect(path)
    total, versions = conn.execute("SELECT SUM(qty), SUM(version) FROM items").fetchone()
    conn.close()
    r = stats["retries"]
    assert len(r) == EXPECTED, len(r)
    return {
        "final": total,
        "lost": EXPECTED - total,
        "conflicts": sum(r),
        "max_retries": max(r),
        "over3": sum(1 for x in r if x > 3),
        "ms": elapsed,
        "versions": versions,
    }


def step_by_step(path):
    new_db(path, 1)
    a, b = connect(path), connect(path)
    # plain read-then-write
    qa = a.execute("SELECT qty FROM items WHERE id = 1").fetchone()[0]
    qb = b.execute("SELECT qty FROM items WHERE id = 1").fetchone()[0]
    a.execute("UPDATE items SET qty = ? WHERE id = 1", (qa + 1,))
    b.execute("UPDATE items SET qty = ? WHERE id = 1", (qb + 1,))
    plain = a.execute("SELECT qty FROM items WHERE id = 1").fetchone()[0]
    assert plain == 1, plain  # two +1 requests, but only one survived

    # optimistic lock
    a.execute("UPDATE items SET qty = 0, version = 0 WHERE id = 1")
    qa, va = a.execute("SELECT qty, version FROM items WHERE id = 1").fetchone()
    qb, vb = b.execute("SELECT qty, version FROM items WHERE id = 1").fetchone()
    ra = a.execute("UPDATE items SET qty = ?, version = version + 1 WHERE id = 1 AND version = ?",
                   (qa + 1, va)).rowcount
    rb = b.execute("UPDATE items SET qty = ?, version = version + 1 WHERE id = 1 AND version = ?",
                   (qb + 1, vb)).rowcount
    assert (ra, rb) == (1, 0), (ra, rb)  # the stale write is rejected
    qb, vb = b.execute("SELECT qty, version FROM items WHERE id = 1").fetchone()
    rb = b.execute("UPDATE items SET qty = ?, version = version + 1 WHERE id = 1 AND version = ?",
                   (qb + 1, vb)).rowcount
    assert rb == 1
    opt = a.execute("SELECT qty, version FROM items WHERE id = 1").fetchone()
    assert opt == (2, 2), opt
    a.close()
    b.close()
    print("1) step by step, two connections, both do +1 on qty = 0")
    print(f"   plain read-then-write : A writes 1, B writes 1      -> qty = {plain} (one update lost)")
    print(f"   optimistic (version)  : A updated 1 row, B updated 0 -> B re-reads and retries -> qty = {opt[0]}\n")


def main():
    path = os.path.join(tempfile.gettempdir(), "lab010.db")
    print(f"file SQLite (WAL), {THREADS} threads x {REQUESTS} '+1' requests = {EXPECTED:,}, "
          f"{GAP * 1000:.0f}ms between SELECT and UPDATE, median of {ROUNDS} rounds, "
          f"Python {sys.version.split()[0]}, SQLite {sqlite3.sqlite_version}\n")
    step_by_step(path)

    print("2) concurrent requests")
    header = (f"{'case':<20} {'method':<12} {'final':>6} {'lost':>6} {'conflicts':>10} "
              f"{'max retry':>10} {'>3 retry':>9} {'total ms':>9}")
    print(header)
    print("-" * len(header))
    for case, rows in [("hot row (1 row)", 1), ("spread (1,000 rows)", 1000)]:
        for method, fn in [("plain", plain_increment), ("optimistic", optimistic_increment)]:
            results = [run(path, rows, fn, seed) for seed in range(ROUNDS)]
            for res in results:
                if method == "optimistic":
                    assert res["final"] == EXPECTED, res      # nothing lost
                    assert res["versions"] == EXPECTED, res   # one version bump per request
            if method == "plain" and rows == 1:
                assert all(res["lost"] > 0 for res in results), results  # bug reproduced
            med = {k: statistics.median(res[k] for res in results) for k in results[0]}
            print(f"{case:<20} {method:<12} {med['final']:>6,.0f} {med['lost']:>6,.0f} "
                  f"{med['conflicts']:>10,.0f} {med['max_retries']:>10,.0f} "
                  f"{med['over3']:>9,.0f} {med['ms']:>9,.1f}")
            print(f"{'':<20} {'  rounds':<12} lost {[r['lost'] for r in results]}, "
                  f"conflicts {[r['conflicts'] for r in results]}, "
                  f"ms {[round(r['ms']) for r in results]}")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


if __name__ == "__main__":
    main()
