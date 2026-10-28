"""
Lab 018: TTL + jitter to avoid synchronized expiry

After a deploy (or a cache flush) many keys are filled within the same
second, all with the same TTL. They then expire in the same second, and
because each reload sets the same TTL again, they keep expiring together
every TTL seconds. This lab replays that with a virtual clock and counts DB
loads per second for:
  - fixed TTL 60 s
  - TTL 60 s with jitter (+0..10%, +0..20%, +-20%)
Traffic: 1,000 keys, 2,000 requests/s spread uniformly over the keys,
600 seconds. The cache is cold at t=0 (every key misses in the first second).
A load costs one real SQLite query; its median time is measured and used to
show the DB work in the busiest second. Median of 3 seeds.

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

KEYS = 1_000
RPS = 2_000
DURATION = 600          # seconds of virtual time
TTL = 60.0
SEEDS = (1, 2, 3)
POLICIES = [
    ("fixed 60s", lambda r: TTL),
    ("60s +0..10%", lambda r: TTL * (1 + r.uniform(0, 0.10))),
    ("60s +0..20%", lambda r: TTL * (1 + r.uniform(0, 0.20))),
    ("60s +-20%", lambda r: TTL * (1 + r.uniform(-0.20, 0.20))),
]


def simulate(ttl_fn, seed):
    rnd = random.Random(seed)
    expires = [None] * KEYS
    loads = [0] * DURATION
    hits = misses = 0
    step = 1.0 / RPS
    t = 0.0
    for i in range(RPS * DURATION):
        t = i * step
        k = rnd.randrange(KEYS)
        e = expires[k]
        if e is not None and t < e:
            hits += 1
            continue
        misses += 1
        loads[int(t)] += 1
        expires[k] = t + ttl_fn(rnd)
    after = loads[int(TTL):]            # skip the cold start minute
    return {
        "peak": max(after),
        "peak_t": int(TTL) + after.index(max(after)),
        "mean": statistics.mean(after),
        "p99": sorted(after)[int(len(after) * 0.99)],
        "loads": misses,
        "hit": hits / (hits + misses) * 100,
        "series": loads,
    }


def query_ms():
    path = os.path.join(tempfile.gettempdir(), "lab018.db")
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, category INTEGER, price INTEGER)")
    conn.executemany("INSERT INTO products VALUES (?, ?, ?)",
                     ((i, i % KEYS, (i * 37) % 10_000) for i in range(1, 200_001)))
    conn.commit()
    times = []
    for k in range(200):
        t0 = time.perf_counter()
        conn.execute("SELECT COUNT(*), AVG(price) FROM products WHERE category = ?", (k,)).fetchall()
        times.append((time.perf_counter() - t0) * 1000)
    conn.close()
    os.remove(path)
    return statistics.median(times)


def main():
    q = query_ms()
    print(f"Python {sys.version.split()[0]} / SQLite {sqlite3.sqlite_version}, cpu={os.cpu_count()}")
    print(f"keys={KEYS:,}, {RPS:,} req/s, {DURATION}s virtual, cold start at t=0, median of {len(SEEDS)} seeds")
    print(f"one load query alone (median of 200): {q:.2f} ms\n")
    header = f"{'policy':<13} {'peak/s':>7} {'p99/s':>6} {'mean/s':>7} {'loads':>7} {'hit %':>6} {'peak DB ms/s':>13}"
    print(header)
    print("-" * len(header))
    results = {}
    for name, fn in POLICIES:
        rs = [simulate(fn, s) for s in SEEDS]
        m = {k: statistics.median(r[k] for r in rs) for k in ("peak", "mean", "p99", "loads", "hit")}
        results[name] = (m, rs[0]["series"])
        print(f"{name:<13} {m['peak']:>7.0f} {m['p99']:>6.0f} {m['mean']:>7.1f} {m['loads']:>7.0f} "
              f"{m['hit']:>6.2f} {m['peak'] * q:>13.1f}")
    print("\nfixed 60s, loads in seconds 60..66, 120..126, 540..546 (seed 1):")
    s = results["fixed 60s"][1]
    for a in (60, 120, 540):
        print(f"  t={a}: {s[a:a + 7]}")
    s = results["60s +0..20%"][1]
    print("60s +0..20%, max loads in any second of minute 1..9 (seed 1):",
          [max(s[m * 60:(m + 1) * 60]) for m in range(1, 10)])


if __name__ == "__main__":
    main()
