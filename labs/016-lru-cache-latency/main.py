"""
Lab 016: No cache vs LRU cache response time

A product page needs two queries: product + review stats (COUNT/AVG over
its reviews) and the 3 latest reviews. This lab puts an in-process LRU cache
(OrderedDict) in front of it and measures per-request latency:
  1. 20,000 page requests over 10,000 products, with
       - skewed traffic (Zipf, a few products get most requests)
       - uniform traffic (every product equally likely)
     and no cache / LRU 100 entries (1% of products) / LRU 1,000 entries (10%)
  2. The same with a cheap query (one primary-key lookup) to see how much a
     cache saves when the DB work is already small
  3. 2% of requests change a price. Without invalidation the cache keeps
     serving the old price; deleting the key on write fixes it (single thread)
  4. Rough memory for 100 / 1,000 cached pages (tracemalloc)
If the cache lookup were free, the best possible speedup is 1 / (1 - hit rate),
so the table prints that bound next to the measured speedup.
Every cached response is checked against the DB result.

Run:
    python3 main.py
"""
import os
import random
import sqlite3
import statistics
import sys
import tempfile
import time
import tracemalloc
from collections import Counter, OrderedDict

PRODUCTS = 10_000
REVIEWS_PER_PRODUCT = 30
REQUESTS = 20_000
ZIPF_S = 1.0
ROUNDS = 5
WRITE_RATIO = 0.02

PAGE_SQL = (
    "SELECT p.id, p.name, p.price, COUNT(r.id), ROUND(AVG(r.rating), 2) "
    "FROM products p LEFT JOIN reviews r ON r.product_id = p.id "
    "WHERE p.id = ? GROUP BY p.id"
)
LATEST_SQL = "SELECT body FROM reviews WHERE product_id = ? ORDER BY created_at DESC LIMIT 3"
CHEAP_SQL = "SELECT id, name, price FROM products WHERE id = ?"


class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.data = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key):
        value = self.data.get(key)
        if value is None:
            self.misses += 1
            return None
        self.data.move_to_end(key)
        self.hits += 1
        return value

    def put(self, key, value):
        self.data[key] = value
        self.data.move_to_end(key)
        if len(self.data) > self.capacity:
            self.data.popitem(last=False)

    def delete(self, key):
        self.data.pop(key, None)


def build_db(path):
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price INTEGER)")
    conn.execute(
        "CREATE TABLE reviews (id INTEGER PRIMARY KEY, product_id INTEGER, rating INTEGER, "
        "created_at INTEGER, body TEXT)"
    )
    rnd = random.Random(1)
    conn.executemany(
        "INSERT INTO products VALUES (?, ?, ?)",
        ((i, f"product-{i}", rnd.randint(1_000, 100_000)) for i in range(1, PRODUCTS + 1)),
    )
    conn.executemany(
        "INSERT INTO reviews (product_id, rating, created_at, body) VALUES (?, ?, ?, ?)",
        (
            (pid, rnd.randint(1, 5), rnd.randint(0, 10_000_000), f"review {pid}-{k} " + "x" * 80)
            for pid in range(1, PRODUCTS + 1)
            for k in range(REVIEWS_PER_PRODUCT)
        ),
    )
    conn.execute("CREATE INDEX idx_reviews_product_created ON reviews(product_id, created_at)")
    conn.commit()
    conn.execute("ANALYZE")
    return conn


def load_page(conn, pid):
    row = conn.execute(PAGE_SQL, (pid,)).fetchone()
    latest = tuple(r[0] for r in conn.execute(LATEST_SQL, (pid,)))
    return (row, latest)


def load_cheap(conn, pid):
    return conn.execute(CHEAP_SQL, (pid,)).fetchone()


def make_requests(kind, seed):
    rnd = random.Random(seed)
    if kind == "uniform":
        return [rnd.randint(1, PRODUCTS) for _ in range(REQUESTS)]
    ranks = list(range(1, PRODUCTS + 1))
    rnd.shuffle(ranks)  # popular products are spread over ids
    weights = [1 / (r ** ZIPF_S) for r in range(1, PRODUCTS + 1)]
    return rnd.choices(ranks, weights=weights, k=REQUESTS)


def run(conn, loader, requests, capacity):
    cache = LRUCache(capacity) if capacity else None
    lat = []
    out = []
    clock = time.perf_counter
    start = clock()
    for pid in requests:
        t0 = clock()
        if cache is None:
            value = loader(conn, pid)
        else:
            value = cache.get(pid)
            if value is None:
                value = loader(conn, pid)
                cache.put(pid, value)
        lat.append(clock() - t0)
        out.append(value)
    total = (clock() - start) * 1000
    lat.sort()
    hit = cache.hits / REQUESTS if cache else 0.0
    return {
        "ms": total,
        "p50": lat[len(lat) // 2] * 1000,
        "p99": lat[int(len(lat) * 0.99)] * 1000,
        "hit": hit,
        "out": out,
    }


def median_run(conn, loader, requests, capacity):
    rs = [run(conn, loader, requests, capacity) for _ in range(ROUNDS)]
    med = {k: statistics.median(r[k] for r in rs) for k in ("ms", "p50", "p99", "hit")}
    med["out"] = rs[0]["out"]
    med["rounds"] = [round(r["ms"], 1) for r in rs]
    return med


def stale_run(conn, requests, invalidate, seed):
    rnd = random.Random(seed)
    cache = LRUCache(1_000)
    price = dict(conn.execute("SELECT id, price FROM products"))
    stale = writes = reads = 0
    for pid in requests:
        if rnd.random() < WRITE_RATIO:
            new_price = price[pid] + 100
            conn.execute("UPDATE products SET price = ? WHERE id = ?", (new_price, pid))
            conn.commit()
            price[pid] = new_price
            writes += 1
            if invalidate:
                cache.delete(pid)  # delete after the DB commit
            continue
        reads += 1
        page = cache.get(pid)
        if page is None:
            page = load_page(conn, pid)
            cache.put(pid, page)
        if page[0][2] != price[pid]:
            stale += 1
    return {"reads": reads, "writes": writes, "stale": stale, "hit": cache.hits / reads}


def cache_memory(conn, n):
    pages = [load_page(conn, pid) for pid in range(1, n + 1)]
    tracemalloc.start()
    cache = LRUCache(n)
    for pid in range(1, n + 1):
        # copy so the measured memory is what the cache itself holds
        row, latest = pages[pid - 1]
        cache.put(pid, (tuple(row), tuple(s.encode().decode() for s in latest)))
    size, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return size / 1024


def main():
    path = os.path.join(tempfile.gettempdir(), "lab016.db")
    t = time.perf_counter()
    conn = build_db(path)
    print(f"Python {sys.version.split()[0]} / SQLite {sqlite3.sqlite_version}")
    print(f"products={PRODUCTS:,}, reviews={PRODUCTS * REVIEWS_PER_PRODUCT:,}, "
          f"requests={REQUESTS:,}, median of {ROUNDS} rounds (setup {time.perf_counter() - t:.1f}s)")
    plan = " / ".join(r[-1] for r in conn.execute("EXPLAIN QUERY PLAN " + LATEST_SQL, (1,)))
    print(f"latest reviews plan: {plan}\n")

    workloads = {"zipf": make_requests("zipf", 7), "uniform": make_requests("uniform", 7)}
    for kind, reqs in workloads.items():
        counts = sorted(Counter(reqs).values(), reverse=True)
        print(f"{kind:<8} distinct products requested {len(counts):,}, "
              f"top 100 products get {sum(counts[:100]) / REQUESTS:.1%}, "
              f"top 1,000 get {sum(counts[:1_000]) / REQUESTS:.1%} of requests")
    print()

    header = f"{'query':<8} {'traffic':<8} {'cache':<10} {'hit %':>6} {'total ms':>9} {'p50 ms':>8} {'p99 ms':>8} {'speedup':>8} {'1/(1-hit)':>9}"
    print(header)
    print("-" * len(header))
    results = {}
    for qname, loader in (("page", load_page), ("pk", load_cheap)):
        for kind, reqs in workloads.items():
            if qname == "pk" and kind == "uniform":
                continue
            base = None
            for cap in (0, 100, 1_000):
                if qname == "pk" and cap == 100:
                    continue
                r = median_run(conn, loader, reqs, cap)
                if base is None:
                    base = r
                    truth = r["out"]
                else:
                    assert r["out"] == truth, (qname, kind, cap)  # cache returns the DB result
                results[(qname, kind, cap)] = r
                label = "none" if cap == 0 else f"LRU {cap:,}"
                print(f"{qname:<8} {kind:<8} {label:<10} {r['hit'] * 100:>5.1f}% {r['ms']:>9.1f} "
                      f"{r['p50']:>8.4f} {r['p99']:>8.4f} {base['ms'] / r['ms']:>7.2f}x {1 / (1 - r['hit']):>8.2f}x   rounds {r['rounds']}")
    print()

    zipf = workloads["zipf"]
    a = stale_run(conn, zipf, invalidate=False, seed=11)
    b = stale_run(conn, zipf, invalidate=True, seed=11)
    assert a["writes"] == b["writes"] and a["reads"] == b["reads"]
    assert b["stale"] == 0
    print(f"writes {WRITE_RATIO:.0%} (zipf, LRU 1,000): reads={a['reads']:,}, writes={a['writes']:,}")
    print(f"  no invalidation    : stale reads {a['stale']:,} ({a['stale'] / a['reads']:.1%}), hit {a['hit']:.1%}")
    print(f"  delete key on write: stale reads {b['stale']:,}, hit {b['hit']:.1%}")
    print()
    for n in (100, 1_000):
        print(f"memory for {n:,} cached pages: {cache_memory(conn, n):,.0f} KB")

    conn.close()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


if __name__ == "__main__":
    main()
