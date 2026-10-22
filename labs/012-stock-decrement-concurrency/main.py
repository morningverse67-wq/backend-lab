"""
Lab 012: Stock decrement under concurrency - atomic UPDATE vs read-then-write

Many orders try to buy the same item at the same time. The classic bug is
"check the stock in the app, then write": two requests both see 1 left and
both sell it. This lab compares, on one hot item:
  - read-then-write (SET qty = ?)       : SELECT qty, check > 0, write qty - 1
  - read-check + relative UPDATE        : SELECT qty, check > 0, SET qty = qty - 1
  - atomic conditional UPDATE           : UPDATE ... SET qty = qty - 1
                                          WHERE id = ? AND qty > 0  (rowcount = sold?)
  - optimistic lock (version + retry)   : from lab 010
  - row lock (pessimistic, emulated)    : from lab 011
Every request does 1ms of app work (e.g. validating the order). The
read-then-write style methods do it between SELECT and UPDATE; the atomic
UPDATE does it before the single UPDATE, outside any lock.

Case 1: stock 100, 400 orders (sold out)  -> who oversells?
Case 2: stock 800, 800 orders (all succeed) -> how long does it take?
Case 3: a cart with two items. Atomic UPDATEs run one by one (autocommit)
        vs inside one transaction (ROLLBACK if any item is sold out).

Run:
    python3 main.py
"""
import os
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from collections import deque

THREADS = 8
WORK = 0.001        # seconds of app work per order
ROUNDS = 3          # median of 3 rounds is reported


class FifoLock:
    """A lock that is handed to waiters in arrival order (same as lab 011)."""

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
        ev.wait()

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


def new_db(path, stocks):
    remove_db(path)
    conn = connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, qty INTEGER NOT NULL, "
        "version INTEGER NOT NULL DEFAULT 0)"
    )
    conn.executemany("INSERT INTO items (id, qty) VALUES (?, ?)",
                     list(enumerate(stocks, start=1)))
    conn.close()


# ---- one order = buy 1 of item 1. Returns (sold?, retries) -----------------

def read_then_write(conn, lock):
    qty = conn.execute("SELECT qty FROM items WHERE id = 1").fetchone()[0]
    if qty <= 0:
        return False, 0
    time.sleep(WORK)
    conn.execute("UPDATE items SET qty = ? WHERE id = 1", (qty - 1,))
    return True, 0


def read_check_relative(conn, lock):
    qty = conn.execute("SELECT qty FROM items WHERE id = 1").fetchone()[0]
    if qty <= 0:
        return False, 0
    time.sleep(WORK)
    conn.execute("UPDATE items SET qty = qty - 1 WHERE id = 1")
    return True, 0


def atomic_update(conn, lock):
    time.sleep(WORK)  # app work happens before, outside any lock
    cur = conn.execute("UPDATE items SET qty = qty - 1 WHERE id = 1 AND qty > 0")
    return cur.rowcount == 1, 0


def optimistic(conn, lock):
    retries = 0
    while True:
        qty, version = conn.execute(
            "SELECT qty, version FROM items WHERE id = 1").fetchone()
        if qty <= 0:
            return False, retries
        time.sleep(WORK)
        cur = conn.execute(
            "UPDATE items SET qty = ?, version = version + 1 WHERE id = 1 AND version = ?",
            (qty - 1, version))
        if cur.rowcount == 1:
            return True, retries
        retries += 1


def row_lock(conn, lock):
    lock.acquire()  # like SELECT ... FOR UPDATE on the item row
    try:
        qty = conn.execute("SELECT qty FROM items WHERE id = 1").fetchone()[0]
        if qty <= 0:
            return False, 0
        time.sleep(WORK)
        conn.execute("UPDATE items SET qty = ? WHERE id = 1", (qty - 1,))
        return True, 0
    finally:
        lock.release()


METHODS = [
    ("read-then-write", read_then_write, False),
    ("read-check + qty-1", read_check_relative, False),
    ("atomic UPDATE", atomic_update, True),
    ("optimistic lock", optimistic, True),
    ("row lock", row_lock, True),
]


def run(path, stock, orders, fn):
    new_db(path, [stock])
    per_thread = orders // THREADS
    lock = FifoLock()
    sold, retries = [0], [0]
    out = threading.Lock()
    barrier = threading.Barrier(THREADS)

    def worker():
        conn = connect(path)
        s = r = 0
        barrier.wait()
        for _ in range(per_thread):
            ok, rt = fn(conn, lock)
            s += ok
            r += rt
        conn.close()
        with out:
            sold[0] += s
            retries[0] += r

    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    start = time.perf_counter()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    ms = (time.perf_counter() - start) * 1000
    conn = connect(path)
    final = conn.execute("SELECT qty FROM items WHERE id = 1").fetchone()[0]
    conn.close()
    return {"sold": sold[0], "final": final, "oversold": max(0, sold[0] - stock),
            "retries": retries[0], "ms": ms}


def case(path, title, stock, orders, check):
    print(f"{title}: stock {stock:,}, {orders:,} orders ({THREADS} threads x {orders // THREADS})")
    header = (f"{'method':<20} {'sold':>6} {'final qty':>9} {'oversold':>9} "
              f"{'retries':>8} {'total ms':>9}")
    print(header)
    print("-" * len(header))
    rows = {}
    for name, fn, safe in METHODS:
        results = [run(path, stock, orders, fn) for _ in range(ROUNDS)]
        if safe:
            for r in results:  # correct methods: never sell more than the stock
                check(r)
        med = {k: statistics.median(r[k] for r in results) for k in results[0]}
        rows[name] = med
        print(f"{name:<20} {med['sold']:>6,.0f} {med['final']:>9,.0f} {med['oversold']:>9,.0f} "
              f"{med['retries']:>8,.0f} {med['ms']:>9,.1f}")
        print(f"{'  rounds':<20} sold {[r['sold'] for r in results]}, "
              f"final {[r['final'] for r in results]}, ms {[round(r['ms'], 1) for r in results]}")
    print()
    return rows


def cart(path, in_tx):
    """200 orders, each buys 1 of item A (stock 100) AND 1 of item B (stock 30)."""
    new_db(path, [100, 30])
    orders, per_thread = 200, 200 // THREADS
    sold = [0]
    out = threading.Lock()
    barrier = threading.Barrier(THREADS)

    def worker():
        conn = connect(path)
        s = 0
        barrier.wait()
        for _ in range(per_thread):
            if in_tx:
                conn.execute("BEGIN IMMEDIATE")
            ok = True
            for item_id in (1, 2):
                cur = conn.execute(
                    "UPDATE items SET qty = qty - 1 WHERE id = ? AND qty > 0", (item_id,))
                if cur.rowcount == 0:
                    ok = False
                    break
            if in_tx:
                conn.execute("COMMIT" if ok else "ROLLBACK")
            s += ok
        conn.close()
        with out:
            sold[0] += s

    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    conn = connect(path)
    a, b = (r[0] for r in conn.execute("SELECT qty FROM items ORDER BY id"))
    conn.close()
    return sold[0], a, b, orders


def main():
    path = os.path.join(tempfile.gettempdir(), "lab012.db")
    print(f"file SQLite (WAL), {WORK * 1000:.0f}ms app work per order, median of {ROUNDS} rounds, "
          f"Python {sys.version.split()[0]}, SQLite {sqlite3.sqlite_version}\n")

    def no_oversell(r):
        assert r["sold"] == 100 and r["final"] == 0, r

    def all_sold(r):
        assert r["sold"] == 800 and r["final"] == 0, r

    case(path, "1) sold out", 100, 400, no_oversell)
    case(path, "2) enough stock", 800, 800, all_sold)

    print("3) cart: each order needs item A (stock 100) and item B (stock 30), 200 orders")
    sold, a, b, orders = cart(path, in_tx=False)
    assert (sold, a, b) == (30, 0, 0), (sold, a, b)   # A is decremented even when B fails
    print(f"   atomic UPDATEs one by one (autocommit): sold {sold}, A left {a}, B left {b}"
          f" -> {100 - sold - a} of A gone without an order")
    sold, a, b, orders = cart(path, in_tx=True)
    assert (sold, a, b) == (30, 70, 0), (sold, a, b)  # ROLLBACK gives A back
    print(f"   same UPDATEs in one transaction      : sold {sold}, A left {a}, B left {b}"
          f" -> {100 - sold - a} of A gone without an order")
    remove_db(path)


if __name__ == "__main__":
    main()
