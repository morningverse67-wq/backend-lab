"""
Lab 017: Cache stampede and single-flight

A category ranking page is expensive: it aggregates 300,000 reviews with no
index on the category. It is cached. The moment the cached entry expires,
many requests arrive at once, all miss, and all run the same query
(cache stampede). This lab forces that moment R times and compares:
  1. no protection   : every miss runs the query
  2. single-flight   : one request per key runs the query, the rest wait for it
  3. stale-while-revalidate: return the expired value right away and refresh
                       it once in a background thread
for N concurrent requests on ONE hot key, and again when the same N requests
are spread over N different keys (where there is nothing to merge).
Expiry is forced by the test (the entry is marked expired before each burst),
so every burst starts with a miss. Each thread has its own SQLite connection.
Every response is checked against the query result.

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

PRODUCTS = 10_000
REVIEWS_PER_PRODUCT = 30
CATEGORIES = 100
ROUNDS = 10          # forced expiries per scenario
RUNS = 3             # repeat each scenario, report the median
CONCURRENCY = (8, 32)

RANK_SQL = (
    "SELECT r.product_id, ROUND(AVG(r.rating), 3) AS avg_rating, COUNT(*) AS n "
    "FROM reviews r JOIN products p ON p.id = r.product_id "
    "WHERE p.category = ? GROUP BY r.product_id "
    "ORDER BY avg_rating DESC, r.product_id LIMIT 10"
)


def build_db(path):
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, category INTEGER, name TEXT)")
    conn.execute("CREATE TABLE reviews (id INTEGER PRIMARY KEY, product_id INTEGER, rating INTEGER, body TEXT)")
    conn.execute("BEGIN")
    conn.executemany("INSERT INTO products VALUES (?, ?, ?)",
                     ((i, i % CATEGORIES, f"product-{i}") for i in range(1, PRODUCTS + 1)))
    rows = []
    rid = 0
    for p in range(1, PRODUCTS + 1):
        for k in range(REVIEWS_PER_PRODUCT):
            rid += 1
            rows.append((rid, p, (p * 7 + k * 13) % 5 + 1, f"review {rid}"))
    conn.executemany("INSERT INTO reviews VALUES (?, ?, ?, ?)", rows)
    conn.execute("COMMIT")
    conn.close()


class Entry:
    def __init__(self, value):
        self.value = value
        self.expired = False


class Call:
    def __init__(self):
        self.event = threading.Event()
        self.value = None


class Server:
    """Shared cache + counters. mode: none | single_flight | swr"""

    def __init__(self, mode):
        self.mode = mode
        self.cache = {}
        self.lock = threading.Lock()
        self.calls = {}
        self.refreshing = set()
        self.db_loads = 0
        self.stale = 0
        self.bg = []

    def load(self, conn, key):
        with self.lock:
            self.db_loads += 1
        value = conn.execute(RANK_SQL, (key,)).fetchall()
        with self.lock:
            self.cache[key] = Entry(value)
        return value

    def get(self, conn, bg_conn_factory, key):
        entry = self.cache.get(key)
        if entry is not None and not entry.expired:
            return entry.value
        if self.mode == "none":
            return self.load(conn, key)
        if self.mode == "single_flight":
            with self.lock:
                entry = self.cache.get(key)          # filled while we waited for the lock?
                if entry is not None and not entry.expired:
                    return entry.value
                call = self.calls.get(key)
                leader = call is None
                if leader:
                    call = self.calls[key] = Call()
            if not leader:
                call.event.wait()
                return call.value
            try:
                call.value = self.load(conn, key)
            finally:
                with self.lock:
                    del self.calls[key]
                call.event.set()
            return call.value
        # stale-while-revalidate
        if entry is None:                            # nothing to serve yet: load inline
            return self.load(conn, key)
        with self.lock:
            self.stale += 1
            start = key not in self.refreshing
            if start:
                self.refreshing.add(key)
        if start:
            def refresh():
                c = bg_conn_factory()
                try:
                    self.load(c, key)
                finally:
                    c.close()
                    with self.lock:
                        self.refreshing.discard(key)
            t = threading.Thread(target=refresh)
            self.bg.append(t)
            t.start()
        return entry.value


def run_scenario(path, mode, n, spread, expected):
    server = Server(mode)
    keys = [(i % CATEGORIES) if spread else 1 for i in range(n)]
    warm = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    for k in set(keys):
        server.load(warm, k)
    server.db_loads = 0
    conns = [sqlite3.connect(path, isolation_level=None, check_same_thread=False) for _ in range(n)]
    factory = lambda: sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    latencies, bursts, results = [], [], []
    for _ in range(ROUNDS):
        for k in set(keys):
            server.cache[k].expired = True
        barrier = threading.Barrier(n + 1)
        out = [None] * n

        def worker(i):
            barrier.wait()
            t0 = time.perf_counter()
            v = server.get(conns[i], factory, keys[i])
            out[i] = (time.perf_counter() - t0, v)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        t0 = time.perf_counter()
        barrier.wait()
        for t in threads:
            t.join()
        bursts.append((time.perf_counter() - t0) * 1000)
        for t in server.bg:
            t.join()
        server.bg.clear()
        for i, (sec, v) in enumerate(out):
            latencies.append(sec * 1000)
            results.append((keys[i], v))
    for c in conns + [warm]:
        c.close()
    for k, v in results:
        assert v == expected[k], "wrong ranking returned"
    latencies.sort()
    return {
        "loads": server.db_loads,
        "requests": n * ROUNDS,
        "stale": server.stale,
        "p50": statistics.median(latencies),
        "p99": latencies[min(len(latencies) - 1, int(len(latencies) * 0.99))],
        "burst": statistics.median(bursts),
    }


def median_of(path, mode, n, spread, expected):
    rs = [run_scenario(path, mode, n, spread, expected) for _ in range(RUNS)]
    med = {k: statistics.median(r[k] for r in rs) for k in ("p50", "p99", "burst")}
    med["loads"] = statistics.median(r["loads"] for r in rs)
    med["stale"] = statistics.median(r["stale"] for r in rs)
    med["requests"] = rs[0]["requests"]
    return med


def main():
    path = os.path.join(tempfile.gettempdir(), "lab017.db")
    build_db(path)
    conn = sqlite3.connect(path)
    expected = {k: conn.execute(RANK_SQL, (k,)).fetchall() for k in range(CATEGORIES)}
    t0 = time.perf_counter()
    for _ in range(5):
        conn.execute(RANK_SQL, (1,)).fetchall()
    one = (time.perf_counter() - t0) / 5 * 1000
    conn.close()

    print(f"Python {sys.version.split()[0]} / SQLite {sqlite3.sqlite_version}, cpu={os.cpu_count()}")
    print(f"products={PRODUCTS:,}, reviews={PRODUCTS * REVIEWS_PER_PRODUCT:,}, categories={CATEGORIES}")
    print(f"one ranking query alone: {one:.1f} ms")
    print(f"{ROUNDS} forced expiries per scenario, median of {RUNS} runs\n")

    header = f"{'keys':<7} {'N':>3} {'mode':<14} {'db loads':>9} {'stale':>6} {'p50 ms':>8} {'p99 ms':>8} {'burst ms':>9}"
    print(header)
    print("-" * len(header))
    t_all = time.perf_counter()
    for spread in (False, True):
        for n in CONCURRENCY:
            for mode in ("none", "single_flight", "swr"):
                r = median_of(path, mode, n, spread, expected)
                print(f"{'spread' if spread else 'hot':<7} {n:>3} {mode:<14} "
                      f"{r['loads']:>4.0f}/{r['requests']:<4} {r['stale']:>6.0f} "
                      f"{r['p50']:>8.2f} {r['p99']:>8.2f} {r['burst']:>9.1f}")
        print()
    print(f"total run time {time.perf_counter() - t_all:.1f} s")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


if __name__ == "__main__":
    main()
