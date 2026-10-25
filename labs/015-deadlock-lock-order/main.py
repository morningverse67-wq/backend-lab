"""
Lab 015: Deadlock reproduction and fixed lock order

Two transfers that touch the same two accounts in opposite order
(A -> B and B -> A) can each hold one row and wait for the other forever.
A database breaks the cycle by rolling one transaction back, either right away
(deadlock detection, InnoDB's default) or after a lock wait timeout. Both cost
a retry. Taking the locks in one fixed order (smaller id first) removes the
cycle itself. This lab:
  1. Shows what SQLite does (WAL, two connections). SQLite has one write lock
     for the whole file, so the "deadlock" is two read transactions that both
     try to become the writer. It returns "database is locked" at once even
     with busy_timeout = 5,000ms. BEGIN IMMEDIATE takes the write lock first,
     so the second transaction waits instead of failing
  2. Runs 4 threads x 50 transfers and compares three ways to take two row
     locks. Row locks are emulated in Python (SQLite has no row locks): lock
     the source row, 1ms of work (like the gap between two UPDATE statements),
     lock the target row, then write both rows in one SQLite transaction
       - no order + timeout   (wait up to 30ms, then roll back and retry:
                               what you get without deadlock detection,
                               e.g. innodb_deadlock_detect = OFF)
       - no order + detection (wait-for graph check on every wait; the
                               requester that would close a cycle is rolled
                               back and retries, like InnoDB's detector)
       - fixed order          (always lock the smaller account id first)
     on 2 hot accounts and on 1,000 accounts
  3. Checks that every method ends with the same balances and the same total

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

THREADS = 4
TRANSFERS = 50           # per thread
GAP = 0.001              # seconds between locking the first and the second row
LOCK_TIMEOUT = 0.030     # lock wait timeout for the "timeout" method
BACKOFF = 0.002          # retry after a random 0..2ms
ROUNDS = 3               # median of 3 rounds is reported
START_BALANCE = 1_000_000
TOTAL = THREADS * TRANSFERS


DB_WRITER = threading.Lock()   # queue writers in Python instead of SQLite's busy sleep


class Deadlock(Exception):
    pass


class LockWaitTimeout(Exception):
    pass


class RowLocks:
    """Exclusive row locks with FIFO hand-off, a wait timeout and optional
    deadlock detection on a wait-for graph (who waits for whom)."""

    def __init__(self, detect, timeout):
        self.detect = detect
        self.timeout = timeout
        self.mutex = threading.Lock()
        self.owner = {}       # row -> tx
        self.queue = {}       # row -> deque of (tx, event)
        self.waiting = {}     # tx -> row it waits for

    def _closes_cycle(self, tx, row):
        seen = set()
        holder = self.owner.get(row)
        while holder is not None and holder not in seen:
            if holder == tx:
                return True
            seen.add(holder)
            next_row = self.waiting.get(holder)
            holder = self.owner.get(next_row) if next_row is not None else None
        return False

    def acquire(self, tx, row):
        with self.mutex:
            if row not in self.owner and not self.queue.get(row):
                self.owner[row] = tx
                return
            if self.detect and self._closes_cycle(tx, row):
                raise Deadlock(row)
            ev = threading.Event()
            self.queue.setdefault(row, deque()).append((tx, ev))
            self.waiting[tx] = row
        got = ev.wait(self.timeout)
        with self.mutex:
            if self.owner.get(row) == tx:   # handed over (maybe just after timeout)
                self.waiting.pop(tx, None)
                return
            self.queue[row].remove((tx, ev))
            self.waiting.pop(tx, None)
        assert not got
        raise LockWaitTimeout(row)

    def release(self, tx, row):
        with self.mutex:
            assert self.owner.get(row) == tx
            q = self.queue.get(row)
            if q:
                nxt, ev = q.popleft()
                self.owner[row] = nxt
                ev.set()
            else:
                del self.owner[row]


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
    conn.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, balance INTEGER NOT NULL)")
    conn.executemany("INSERT INTO accounts VALUES (?, ?)",
                     [(i, START_BALANCE) for i in range(1, rows + 1)])
    conn.close()


def make_plan(rows, seed):
    """The same transfers (src, dst, amount) for every method."""
    rnd = random.Random(seed)
    plan = []
    for _ in range(THREADS):
        mine = []
        for _ in range(TRANSFERS):
            src, dst = rnd.sample(range(1, rows + 1), 2)
            mine.append((src, dst, rnd.randint(1, 100)))
        plan.append(mine)
    return plan


def transfer(conn, locks, tx, src, dst, amount, ordered):
    first, second = (min(src, dst), max(src, dst)) if ordered else (src, dst)
    locks.acquire(tx, first)
    try:
        time.sleep(GAP)                 # work done while holding the first row
        locks.acquire(tx, second)
        try:
            with DB_WRITER:             # SQLite allows one writer at a time
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE accounts SET balance = balance - ? WHERE id = ?", (amount, src))
                conn.execute("UPDATE accounts SET balance = balance + ? WHERE id = ?", (amount, dst))
                conn.execute("COMMIT")
        finally:
            locks.release(tx, second)
    finally:
        locks.release(tx, first)        # on Deadlock/Timeout this is the rollback


def run(path, rows, plan, method, seed):
    new_db(path, rows)
    ordered = method == "fixed order"
    locks = RowLocks(detect=(method == "no order + detection"),
                     timeout=LOCK_TIMEOUT if method == "no order + timeout" else 10.0)
    out = {"deadlock": 0, "timeout": 0, "latency": []}
    out_lock = threading.Lock()
    barrier = threading.Barrier(THREADS)

    def worker(t):
        rnd = random.Random(seed * 100 + t)
        conn = connect(path)
        dl = to = 0
        lat = []
        barrier.wait()
        for n, (src, dst, amount) in enumerate(plan[t]):
            s = time.perf_counter()
            attempt = 0
            while True:
                tx = (t, n, attempt)
                try:
                    transfer(conn, locks, tx, src, dst, amount, ordered)
                    break
                except Deadlock:
                    dl += 1
                except LockWaitTimeout:
                    to += 1
                attempt += 1
                time.sleep(rnd.random() * BACKOFF)
            lat.append((time.perf_counter() - s) * 1000)
        conn.close()
        with out_lock:
            out["deadlock"] += dl
            out["timeout"] += to
            out["latency"].extend(lat)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(THREADS)]
    start = time.perf_counter()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    elapsed = (time.perf_counter() - start) * 1000

    conn = connect(path)
    balances = dict(conn.execute("SELECT id, balance FROM accounts"))
    conn.close()
    lat = sorted(out["latency"])
    assert len(lat) == TOTAL, len(lat)
    return {
        "balances": balances,
        "deadlock": out["deadlock"],
        "timeout": out["timeout"],
        "ms": elapsed,
        "p50": lat[len(lat) // 2],
        "p99": lat[int(len(lat) * 0.99) - 1],
        "max": lat[-1],
    }


def expected_balances(rows, plan):
    bal = {i: START_BALANCE for i in range(1, rows + 1)}
    for mine in plan:
        for src, dst, amount in mine:
            bal[src] -= amount
            bal[dst] += amount
    return bal


def sqlite_step_by_step(path):
    print("1) SQLite (WAL), two connections, busy_timeout = 5,000ms")
    # a) DEFERRED: both read first, then both want to write
    new_db(path, 2)
    a, b = connect(path, 5), connect(path, 5)
    a.execute("BEGIN")
    a.execute("SELECT balance FROM accounts WHERE id = 1").fetchone()
    b.execute("BEGIN")
    b.execute("SELECT balance FROM accounts WHERE id = 2").fetchone()
    a.execute("UPDATE accounts SET balance = balance - 10 WHERE id = 1")   # A becomes the writer
    s = time.perf_counter()
    try:
        b.execute("UPDATE accounts SET balance = balance - 10 WHERE id = 2")
        b_error = None
    except sqlite3.OperationalError as e:
        b_error = str(e)
    b_ms = (time.perf_counter() - s) * 1000
    b.execute("ROLLBACK")
    a.execute("COMMIT")
    assert b_error == "database is locked", b_error
    assert b_ms < 1000, b_ms        # did not wait for busy_timeout
    print(f"   BEGIN (deferred): A read, B read, A UPDATE, B UPDATE -> '{b_error}' "
          f"after {b_ms:.3f}ms (no wait), B must roll back and retry")

    # b) IMMEDIATE: take the write lock before reading
    a.execute("BEGIN IMMEDIATE")
    a.execute("SELECT balance FROM accounts WHERE id = 1").fetchone()
    result = {}

    def b_side():
        b2 = connect(path, 5)                   # B's own connection in its thread
        s2 = time.perf_counter()
        b2.execute("BEGIN IMMEDIATE")           # waits here until A commits
        result["wait"] = (time.perf_counter() - s2) * 1000
        b2.execute("SELECT balance FROM accounts WHERE id = 2").fetchone()
        b2.execute("UPDATE accounts SET balance = balance - 10 WHERE id = 2")
        b2.execute("COMMIT")
        b2.close()

    th = threading.Thread(target=b_side)
    th.start()
    time.sleep(0.030)                           # A works for 30ms holding the lock
    a.execute("UPDATE accounts SET balance = balance - 10 WHERE id = 1")
    a.execute("COMMIT")
    th.join()
    final = dict(a.execute("SELECT id, balance FROM accounts"))
    a.close()
    b.close()
    assert final == {1: START_BALANCE - 20, 2: START_BALANCE - 10}, final
    print(f"   BEGIN IMMEDIATE: A holds the write lock 30ms, B waits {result['wait']:.1f}ms, "
          f"then both commit (no error)\n")


def main():
    path = os.path.join(tempfile.gettempdir(), "lab015.db")
    print(f"file SQLite (WAL), {THREADS} threads x {TRANSFERS} transfers = {TOTAL:,}, "
          f"{GAP * 1000:.0f}ms between first and second row lock, lock wait timeout "
          f"{LOCK_TIMEOUT * 1000:.0f}ms, retry after 0..{BACKOFF * 1000:.0f}ms, "
          f"median of {ROUNDS} rounds, Python {sys.version.split()[0]}, "
          f"SQLite {sqlite3.sqlite_version}\n")
    sqlite_step_by_step(path)

    print("2) concurrent transfers (latency = one transfer, including waits and retries)")
    header = (f"{'case':<22} {'method':<22} {'deadlock':>8} {'timeout':>8} {'total ms':>9} "
              f"{'p50 ms':>7} {'p99 ms':>7} {'max ms':>7}")
    print(header)
    print("-" * len(header))
    methods = ["no order + timeout", "no order + detection", "fixed order"]
    for case, rows in (("hot (2 accounts)", 2), ("spread (1,000 accounts)", 1000)):
        per_method = {m: [] for m in methods}
        for seed in range(ROUNDS):
            plan = make_plan(rows, seed)
            want = expected_balances(rows, plan)
            for m in methods:
                r = run(path, rows, plan, m, seed)
                assert r["balances"] == want, (case, m)   # same result, nothing lost
                assert sum(r["balances"].values()) == rows * START_BALANCE
                if m == "fixed order":
                    assert r["deadlock"] == 0 and r["timeout"] == 0, r
                if m == "no order + detection":
                    assert r["timeout"] == 0, r   # backstop timeout never fired
                per_method[m].append(r)
        for m in methods:
            rs = per_method[m]
            med = {k: statistics.median(r[k] for r in rs)
                   for k in ("deadlock", "timeout", "ms", "p50", "p99", "max")}
            print(f"{case:<22} {m:<22} {med['deadlock']:>8,.0f} {med['timeout']:>8,.0f} "
                  f"{med['ms']:>9,.1f} {med['p50']:>7.2f} {med['p99']:>7.2f} {med['max']:>7.2f}")
            print(f"{'':<22} {'  rounds':<22} total ms {[round(r['ms'], 1) for r in rs]}, "
                  f"deadlock {[r['deadlock'] for r in rs]}, timeout {[r['timeout'] for r in rs]}, "
                  f"p99 {[round(r['p99'], 1) for r in rs]}")
    remove_db(path)


if __name__ == "__main__":
    main()
