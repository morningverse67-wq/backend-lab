"""
Lab 011: Pessimistic locking (SELECT ... FOR UPDATE idea) and its trade-offs

Lab 010 showed that optimistic locking (version column + retry) never loses
an update, but on one hot row it retried 5,995 times. A pessimistic lock takes
the row lock *before* reading, so other requests wait in line instead of doing
work that gets thrown away. This lab:
  1. Shows a real pessimistic lock in SQLite with two connections.
     SQLite has no SELECT ... FOR UPDATE; BEGIN IMMEDIATE takes the write lock
     up front, but for the whole database file, not for one row
  2. Runs 8 threads x 200 "+1" requests with a 1ms gap between read and write
     (same setup as lab 010) and compares
       - optimistic lock   (version column + retry on 0 updated rows)
       - row lock          (lock the row, read, write, unlock: what FOR UPDATE
                            does on an indexed row in InnoDB; emulated with one
                            first-come-first-served lock per row id)
       - one lock for all  (same, but a single lock for every row: a coarse
                            lock, like SQLite's database lock or a FOR UPDATE
                            that has to lock many rows)
     on one hot row and on rows spread over 1,000 rows
  3. Reports lost updates, retries, total time and per-request latency

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
from collections import deque

THREADS = 8
REQUESTS = 200          # per thread
GAP = 0.001             # seconds between SELECT and UPDATE
ROUNDS = 3              # median of 3 rounds is reported
EXPECTED = THREADS * REQUESTS


class FifoLock:
    """A lock that is handed to waiters in arrival order (no barging)."""

    def __init__(self):
        self._mutex = threading.Lock()
        self._waiters = deque()
        self._held = False

    def acquire(self):
        with self._mutex:
            if not self._held and not self._waiters:
                self._held = True
                return
            ev = threading.Event()
            self._waiters.append(ev)
        ev.wait()  # release() hands the lock over to us

    def release(self):
        with self._mutex:
            if self._waiters:
                self._waiters.popleft().set()
            else:
                self._held = False


def connect(path, timeout=30):
    conn = sqlite3.connect(path, isolation_level=None, timeout=timeout)  # autocommit
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    return conn


def remove_db(path):
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


def new_db(path, rows):
    remove_db(path)
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


def optimistic_increment(conn, item_id, locks):
    retries = 0
    while True:
        qty, version = conn.execute(
            "SELECT qty, version FROM items WHERE id = ?", (item_id,)).fetchone()
        time.sleep(GAP)
        cur = conn.execute(
            "UPDATE items SET qty = ?, version = version + 1 WHERE id = ? AND version = ?",
            (qty + 1, item_id, version))
        if cur.rowcount == 1:
            return retries
        retries += 1  # someone else changed the row: read again and retry


def locked_increment(conn, item_id, locks):
    lock = locks(item_id)
    lock.acquire()          # like SELECT ... FOR UPDATE: wait here until it's our turn
    try:
        qty, version = conn.execute(
            "SELECT qty, version FROM items WHERE id = ?", (item_id,)).fetchone()
        time.sleep(GAP)
        cur = conn.execute(
            "UPDATE items SET qty = ?, version = version + 1 WHERE id = ?",
            (qty + 1, item_id))
        assert cur.rowcount == 1
    finally:
        lock.release()      # like COMMIT: the next waiter gets the row
    return 0


def run(path, rows, increment, lock_mode, seed):
    new_db(path, rows)
    if lock_mode == "row":
        table = {i: FifoLock() for i in range(1, rows + 1)}
        locks = table.__getitem__
    elif lock_mode == "global":
        one = FifoLock()
        locks = lambda _id: one  # noqa: E731
    else:
        locks = None
    retries, latency = [], []
    out_lock = threading.Lock()
    barrier = threading.Barrier(THREADS)

    def worker(t):
        rnd = random.Random(seed * 100 + t)
        conn = connect(path)
        my_retries, my_latency = [], []
        barrier.wait()
        for _ in range(REQUESTS):
            s = time.perf_counter()
            my_retries.append(increment(conn, rnd.randint(1, rows), locks))
            my_latency.append((time.perf_counter() - s) * 1000)
        conn.close()
        with out_lock:
            retries.extend(my_retries)
            latency.extend(my_latency)

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
    assert len(latency) == EXPECTED, len(latency)
    assert total == EXPECTED, (total, lock_mode)   # no lost update with either lock
    assert versions == EXPECTED, versions          # exactly one write per request
    latency.sort()
    return {
        "lost": EXPECTED - total,
        "retries": sum(retries),
        "ms": elapsed,
        "p50": latency[len(latency) // 2],
        "p99": latency[int(len(latency) * 0.99) - 1],
        "max": latency[-1],
    }


def step_by_step(path):
    new_db(path, 2)
    a = connect(path)
    b = connect(path, timeout=0)  # B does not wait: show the lock as an error

    a.execute("BEGIN IMMEDIATE")  # A takes the write lock before reading
    qa = a.execute("SELECT qty FROM items WHERE id = 1").fetchone()[0]

    plain_read = b.execute("SELECT qty FROM items WHERE id = 1").fetchone()[0]
    assert plain_read == 0  # a plain SELECT is not blocked: it reads the old value

    blocked = []
    for item_id in (1, 2):  # B wants row 1, then a different row 2
        try:
            b.execute("BEGIN IMMEDIATE")
            b.execute("ROLLBACK")
        except sqlite3.OperationalError as e:
            blocked.append((item_id, str(e)))
    assert [i for i, _ in blocked] == [1, 2], blocked  # even row 2 is locked

    a.execute("UPDATE items SET qty = ? WHERE id = 1", (qa + 1,))
    a.execute("COMMIT")

    b.execute("BEGIN IMMEDIATE")  # now B gets the lock and reads A's result
    qb = b.execute("SELECT qty FROM items WHERE id = 1").fetchone()[0]
    b.execute("UPDATE items SET qty = ? WHERE id = 1", (qb + 1,))
    b.execute("COMMIT")
    final = a.execute("SELECT qty FROM items WHERE id = 1").fetchone()[0]
    assert (qb, final) == (1, 2), (qb, final)
    a.close()
    b.close()
    print("1) step by step, SQLite BEGIN IMMEDIATE (write lock before reading), both do +1 on qty = 0")
    print(f"   A: BEGIN IMMEDIATE, SELECT -> {qa}")
    print(f"   B: plain SELECT row 1         -> {plain_read} (not blocked, reads the old value)")
    print(f"   B: BEGIN IMMEDIATE for row 1  -> {blocked[0][1]}")
    print(f"   B: BEGIN IMMEDIATE for row 2  -> {blocked[1][1]} (the lock covers the whole file)")
    print(f"   A: UPDATE, COMMIT; B: BEGIN IMMEDIATE, SELECT -> {qb}, UPDATE, COMMIT -> qty = {final}\n")


def main():
    path = os.path.join(tempfile.gettempdir(), "lab011.db")
    print(f"file SQLite (WAL), {THREADS} threads x {REQUESTS} '+1' requests = {EXPECTED:,}, "
          f"{GAP * 1000:.0f}ms between SELECT and UPDATE, median of {ROUNDS} rounds, "
          f"Python {sys.version.split()[0]}, SQLite {sqlite3.sqlite_version}\n")
    step_by_step(path)

    print("2) concurrent requests (latency = one request, including waiting and retries)")
    header = (f"{'case':<20} {'method':<16} {'lost':>5} {'retries':>8} {'total ms':>9} "
              f"{'p50 ms':>7} {'p99 ms':>7} {'max ms':>7}")
    print(header)
    print("-" * len(header))
    plan = [
        ("hot row (1 row)", 1, [("optimistic", optimistic_increment, None),
                                ("row lock", locked_increment, "row")]),
        ("spread (1,000 rows)", 1000, [("optimistic", optimistic_increment, None),
                                       ("row lock", locked_increment, "row"),
                                       ("one lock for all", locked_increment, "global")]),
    ]
    for case, rows, methods in plan:
        for method, fn, mode in methods:
            results = [run(path, rows, fn, mode, seed) for seed in range(ROUNDS)]
            med = {k: statistics.median(r[k] for r in results) for k in results[0]}
            print(f"{case:<20} {method:<16} {med['lost']:>5,.0f} {med['retries']:>8,.0f} "
                  f"{med['ms']:>9,.1f} {med['p50']:>7.2f} {med['p99']:>7.2f} {med['max']:>7.2f}")
            print(f"{'':<20} {'  rounds':<16} total ms {[round(r['ms']) for r in results]}, "
                  f"retries {[r['retries'] for r in results]}, "
                  f"p99 {[round(r['p99'], 1) for r in results]}")
    remove_db(path)


if __name__ == "__main__":
    main()
