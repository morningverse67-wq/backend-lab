"""
Lab 014: Idempotency key - stopping duplicate payment requests

A payment request can arrive more than once: a double click, or a client that
timed out and retried while the server had already charged the card. The
charge itself goes to an outside payment gateway (PG), so a DB ROLLBACK cannot
undo it. This lab counts real PG charges (a fake PG that takes 2ms) for:
  - no key            : every request charges and inserts a payment row
  - check, then act   : SELECT the key first; if absent, charge, then INSERT the
                        key (UNIQUE) with the payment in one transaction
  - claim the key     : INSERT the key first (status 'processing',
                        ON CONFLICT DO NOTHING). Only the request that inserted
                        it charges. Others get the stored response, or 409 while
                        it is still processing (the client waits 3ms and retries).

Part 1: 200 orders, each request sent 3 times at once (8 threads).
Part 2: the response is lost and the client retries once. Same key on retry
        vs a new key per attempt. Also: same key with a different amount, and
        a failed PG call with / without releasing the key.
Part 3: the cost - 300 different orders, no duplicates, with and without a key.

Run:
    python3 main.py
"""
import hashlib
import os
import queue
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
import uuid

THREADS = 8
ORDERS = 200
COPIES = 3            # each order request arrives 3 times at once
PG_DELAY = 0.002      # fake payment gateway call (seconds)
RETRY_WAIT = 0.003    # client waits this long after 409, then retries
ROUNDS = 3            # median of 3 rounds is reported
COST_ORDERS = 300
COST_ROUNDS = 5       # part 3 is noisier, so median of 5


class PgError(Exception):
    pass


class FakePG:
    """An outside payment gateway. Its charges are NOT rolled back with the DB."""

    def __init__(self, delay=PG_DELAY):
        self.delay = delay
        self.charges = []          # (order_id, amount)
        self.spent = 0.0           # seconds spent inside charge()
        self.fail_next = 0
        self._lock = threading.Lock()

    def charge(self, order_id, amount):
        t0 = time.perf_counter()
        if self.delay:
            time.sleep(self.delay)
        self.spent += time.perf_counter() - t0   # only read in single-thread part 3
        with self._lock:
            if self.fail_next:
                self.fail_next -= 1
                raise PgError("gateway timeout")
            self.charges.append((order_id, amount))
            return len(self.charges)


def connect(path, timeout=30):
    conn = sqlite3.connect(path, isolation_level=None, timeout=timeout,
                           check_same_thread=False)  # autocommit
    conn.execute(f"PRAGMA busy_timeout = {timeout * 1000}")
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
    conn.execute("CREATE TABLE payments (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL,"
                 " amount INTEGER NOT NULL)")
    conn.execute("CREATE TABLE idempotency_keys (key TEXT PRIMARY KEY NOT NULL,"
                 " request_hash TEXT NOT NULL, status TEXT NOT NULL, payment_id INTEGER)")
    conn.close()


def request_hash(order_id, amount):
    return hashlib.sha256(f"{order_id}:{amount}".encode()).hexdigest()


# ---- three server handlers. Return (http_status, payment_id) ------------------

def no_key(conn, pg, key, order_id, amount):
    pg.charge(order_id, amount)
    cur = conn.execute("INSERT INTO payments (order_id, amount) VALUES (?, ?)", (order_id, amount))
    return 200, cur.lastrowid


def check_then_act(conn, pg, key, order_id, amount):
    row = conn.execute("SELECT payment_id FROM idempotency_keys WHERE key = ?", (key,)).fetchone()
    if row:
        return 200, row[0]                       # replay the stored response
    pg.charge(order_id, amount)                  # two requests can both get here
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("INSERT INTO payments (order_id, amount) VALUES (?, ?)", (order_id, amount))
        conn.execute("INSERT INTO idempotency_keys VALUES (?, ?, 'done', ?)",
                     (key, request_hash(order_id, amount), cur.lastrowid))
        conn.execute("COMMIT")
        return 200, cur.lastrowid
    except sqlite3.IntegrityError:               # UNIQUE caught it, but the PG already charged
        conn.execute("ROLLBACK")
        row = conn.execute("SELECT payment_id FROM idempotency_keys WHERE key = ?", (key,)).fetchone()
        return 200, row[0]


def claim_key(conn, pg, key, order_id, amount, release_on_error=True):
    h = request_hash(order_id, amount)
    claimed = conn.execute(
        "INSERT INTO idempotency_keys (key, request_hash, status) VALUES (?, ?, 'processing')"
        " ON CONFLICT(key) DO NOTHING RETURNING key", (key, h)).fetchone()
    if claimed is None:                          # someone else owns this key
        stored_hash, status, payment_id = conn.execute(
            "SELECT request_hash, status, payment_id FROM idempotency_keys WHERE key = ?",
            (key,)).fetchone()
        if stored_hash != h:
            return 422, None                     # same key, different request
        if status == "processing":
            return 409, None                     # still running: try again later
        return 200, payment_id                   # replay the stored response
    try:
        pg.charge(order_id, amount)
    except PgError:
        if release_on_error:
            conn.execute("DELETE FROM idempotency_keys WHERE key = ?", (key,))
        return 502, None
    conn.execute("BEGIN IMMEDIATE")
    cur = conn.execute("INSERT INTO payments (order_id, amount) VALUES (?, ?)", (order_id, amount))
    conn.execute("UPDATE idempotency_keys SET status = 'done', payment_id = ? WHERE key = ?",
                 (cur.lastrowid, key))
    conn.execute("COMMIT")
    return 200, cur.lastrowid


def send(conn, pg, handler, key, order_id, amount, stats):
    """Client side: retry on 409 after RETRY_WAIT (same key)."""
    for _ in range(200):
        status, payment_id = handler(conn, pg, key, order_id, amount)
        if status != 409:
            return status, payment_id
        stats["409"] += 1
        time.sleep(RETRY_WAIT)
    raise RuntimeError("still 409 after 200 tries")


# ---- Part 1 -------------------------------------------------------------------

def burst(path, handler):
    new_db(path)
    pg = FakePG()
    jobs = queue.Queue()
    for order_id in range(1, ORDERS + 1):
        key = str(uuid.uuid4())                  # one key per order (client made it once)
        for _ in range(COPIES):                  # copies are next to each other -> arrive together
            jobs.put((key, order_id, 1000 + order_id))
    answers = {}
    stats = {"409": 0}
    lock = threading.Lock()
    barrier = threading.Barrier(THREADS)

    def worker():
        conn = connect(path)
        local = {"409": 0}
        barrier.wait()
        while True:
            try:
                key, order_id, amount = jobs.get_nowait()
            except queue.Empty:
                break
            status, payment_id = send(conn, pg, handler, key, order_id, amount, local)
            assert status == 200, status
            with lock:
                answers.setdefault(order_id, set()).add(payment_id)
        with lock:
            stats["409"] += local["409"]
        conn.close()

    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = (time.perf_counter() - t0) * 1000
    conn = connect(path)
    rows = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    conn.close()
    per_order = {}
    for order_id, _ in pg.charges:
        per_order[order_id] = per_order.get(order_id, 0) + 1
    return {
        "charges": len(pg.charges),
        "double": sum(1 for c in per_order.values() if c > 1),
        "rows": rows,
        "mixed": sum(1 for ids in answers.values() if len(ids) > 1),   # copies got different ids
        "409": stats["409"],
        "ms": elapsed,
    }


def part1(path):
    print(f"1) {ORDERS} orders x {COPIES} identical requests at once ({THREADS} threads), "
          f"PG call {PG_DELAY * 1000:.0f}ms, median of {ROUNDS} rounds")
    header = (f"{'method':<16} {'PG charges':>10} {'double-charged':>14} {'DB rows':>8} "
              f"{'mixed answers':>13} {'409s':>6} {'time ms':>9}")
    print(header)
    print("-" * len(header))
    out = {}
    for name, handler in [("no key", no_key), ("check, then act", check_then_act),
                          ("claim the key", claim_key)]:
        results = [burst(path, handler) for _ in range(ROUNDS)]
        med = {k: statistics.median(r[k] for r in results) for k in results[0]}
        out[name] = (med, results)
        print(f"{name:<16} {med['charges']:>10,.0f} {med['double']:>14,.0f} {med['rows']:>8,.0f} "
              f"{med['mixed']:>13,.0f} {med['409']:>6,.0f} {med['ms']:>9,.1f}")
        print(f"{'  rounds':<16} charges {[r['charges'] for r in results]}, "
              f"rows {[r['rows'] for r in results]}, ms {[round(r['ms'], 1) for r in results]}")
    for r in out["no key"][1]:
        assert r["charges"] == ORDERS * COPIES and r["rows"] == ORDERS * COPIES, r
    for r in out["check, then act"][1]:
        assert r["rows"] == ORDERS and r["mixed"] == 0, r      # the DB looks clean...
    for r in out["claim the key"][1]:
        assert r["charges"] == ORDERS and r["double"] == 0, r  # ...but only this charges once
        assert r["rows"] == ORDERS and r["mixed"] == 0, r
    print()
    return out


# ---- Part 2 -------------------------------------------------------------------

def lost_response(path, new_key_per_attempt):
    """Attempt 1 succeeds on the server, but the client never sees it and retries once."""
    new_db(path)
    conn = connect(path)
    pg = FakePG()
    first, retry = [], []
    same = 0
    for order_id in range(1, ORDERS + 1):
        key = str(uuid.uuid4())
        amount = 1000 + order_id
        t0 = time.perf_counter()
        s1, p1 = claim_key(conn, pg, key, order_id, amount)
        first.append((time.perf_counter() - t0) * 1000)
        if new_key_per_attempt:
            key = str(uuid.uuid4())
        t0 = time.perf_counter()
        s2, p2 = claim_key(conn, pg, key, order_id, amount)
        retry.append((time.perf_counter() - t0) * 1000)
        assert s1 == s2 == 200
        same += p1 == p2
    rows = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    conn.close()
    return {"charges": len(pg.charges), "rows": rows, "same": same,
            "first": statistics.median(first), "retry": statistics.median(retry)}


def part2(path):
    print(f"2) {ORDERS} orders, response lost after attempt 1, client retries once (claim the key), "
          f"median of {ROUNDS} rounds")
    header = f"{'retry uses':<22} {'PG charges':>10} {'DB rows':>8} {'same answer':>11} {'1st p50 ms':>10} {'retry p50 ms':>12}"
    print(header)
    print("-" * len(header))
    out = {}
    for name, flag in [("the same key", False), ("a new key", True)]:
        results = [lost_response(path, flag) for _ in range(ROUNDS)]
        r = {k: statistics.median(x[k] for x in results) for k in results[0]}
        out[name] = r
        print(f"{name:<22} {r['charges']:>10,.0f} {r['rows']:>8,.0f} {r['same']:>11,.0f} "
              f"{r['first']:>10.3f} {r['retry']:>12.3f}")
        print(f"{'  rounds':<22} 1st {[round(x['first'], 3) for x in results]}, "
              f"retry {[round(x['retry'], 3) for x in results]}")
        for x in results:
            assert x["charges"] == ORDERS * (2 if flag else 1), x
            assert x["same"] == (0 if flag else ORDERS), x
    print()

    # same key, different amount
    new_db(path)
    conn = connect(path)
    pg = FakePG()
    key = str(uuid.uuid4())
    a = claim_key(conn, pg, key, 1, 1000)
    b = claim_key(conn, pg, key, 1, 9000)
    assert a[0] == 200 and b == (422, None) and len(pg.charges) == 1
    print(f"   same key, amount 1000 then 9000 : {a[0]}, then {b[0]} (PG charges {len(pg.charges)})")

    # PG fails once, then the client retries up to 5 times with the same key
    fail = {}
    for release in (True, False):
        pg = FakePG()
        pg.fail_next = 1
        key = str(uuid.uuid4())
        codes = [claim_key(conn, pg, key, 2, 2000, release_on_error=release)[0] for _ in range(6)]
        fail[release] = (codes, len(pg.charges))
    assert fail[True] == ([502, 200, 200, 200, 200, 200], 1), fail
    assert fail[False] == ([502, 409, 409, 409, 409, 409], 0), fail
    print(f"   PG fails, key released          : {fail[True][0]} (PG charges {fail[True][1]})")
    print(f"   PG fails, key left 'processing' : {fail[False][0]} (PG charges {fail[False][1]})")
    conn.close()
    print()
    return out, (a[0], b[0]), fail


# ---- Part 3 -------------------------------------------------------------------

def cost_run(path, handler, delay):
    new_db(path)
    conn = connect(path)
    pg = FakePG(delay)
    t0 = time.perf_counter()
    for order_id in range(1, COST_ORDERS + 1):
        status, _ = handler(conn, pg, str(uuid.uuid4()), order_id, 1000 + order_id)
        assert status == 200
    elapsed = (time.perf_counter() - t0) * 1000
    assert len(pg.charges) == COST_ORDERS
    conn.close()
    return elapsed / COST_ORDERS, pg.spent * 1000 / COST_ORDERS


def part3(path):
    print(f"3) cost: {COST_ORDERS} different orders one by one (no duplicates), "
          f"ms per request, median of {COST_ROUNDS} rounds")
    header = (f"{'PG call':<8} {'no key':>9} {'claim the key':>14} {'extra':>8} {'ratio':>7}"
              f" {'PG part (no key / claim)':>25}")
    print(header)
    print("-" * len(header))
    out = {}
    for delay in (0, PG_DELAY):
        a, b = [], []
        for _ in range(COST_ROUNDS):             # alternate so drift hits both sides
            a.append(cost_run(path, no_key, delay))
            b.append(cost_run(path, claim_key, delay))
        ma, mb = statistics.median(x[0] for x in a), statistics.median(x[0] for x in b)
        pa, pb = statistics.median(x[1] for x in a), statistics.median(x[1] for x in b)
        out[delay] = (ma, mb, a, b)
        print(f"{delay * 1000:>4.0f}ms   {ma:>9.3f} {mb:>14.3f} {mb - ma:>8.3f} {mb / ma:>6.2f}x"
              f" {pa:>12.3f} / {pb:.3f}")
        print(f"   rounds no key {[round(x[0], 3) for x in a]}, claim {[round(x[0], 3) for x in b]}")
    print()
    return out


def main():
    path = os.path.join(tempfile.gettempdir(), "lab014.db")
    print(f"file SQLite (WAL, synchronous=NORMAL), Python {sys.version.split()[0]}, "
          f"SQLite {sqlite3.sqlite_version}\n")
    t0 = time.perf_counter()
    part1(path)
    part2(path)
    part3(path)
    remove_db(path)
    print(f"total {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()
