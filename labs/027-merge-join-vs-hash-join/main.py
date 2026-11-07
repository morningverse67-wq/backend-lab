"""Lab 027: sorted merge join vs hash join, including the sort cost.

Run:
    python3 main.py

Both inputs are generated deterministically. Each measurement runs in a fresh
child process; generation and correctness checks are outside the timed region.
"""
import json
import platform
import random
import subprocess
import sys
import time
import tracemalloc

LEFT_N = 80_000
RIGHT_N = 20_000
KEYS = 10_000
REPEATS = 5


def make_rows(seed, n, offset):
    rnd = random.Random(seed)
    return [(rnd.randrange(KEYS), offset + i) for i in range(n)]


def hash_join(left, right):
    index = {}
    for key, rid in right:
        index.setdefault(key, []).append(rid)
    out = []
    for key, lid in left:
        for rid in index.get(key, ()):
            out.append((lid, rid))
    return out


def merge_join(left, right):
    out = []
    i = j = 0
    while i < len(left) and j < len(right):
        lk, _ = left[i]
        rk, _ = right[j]
        if lk < rk:
            i += 1
        elif lk > rk:
            j += 1
        else:
            i2 = i + 1
            while i2 < len(left) and left[i2][0] == lk:
                i2 += 1
            j2 = j + 1
            while j2 < len(right) and right[j2][0] == rk:
                j2 += 1
            for _, lid in left[i:i2]:
                for _, rid in right[j:j2]:
                    out.append((lid, rid))
            i, j = i2, j2
    return out


def summarize(rows):
    return len(rows), sum(a * 31 + b for a, b in rows)


def child(method, ordered):
    left = make_rows(2701, LEFT_N, 1)
    right = make_rows(2702, RIGHT_N, LEFT_N + 1)
    if ordered:
        left.sort(key=lambda row: row[0])
        right.sort(key=lambda row: row[0])
    tracemalloc.start()
    start = time.perf_counter()
    if method == "hash":
        result = hash_join(left, right)
    else:
        if not ordered:
            left = sorted(left, key=lambda row: row[0])
            right = sorted(right, key=lambda row: row[0])
        result = merge_join(left, right)
    elapsed = (time.perf_counter() - start) * 1000
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps({"summary": summarize(result), "ms": elapsed, "peak_mb": peak / 1_000_000}))


def run(method, ordered):
    cmd = [sys.executable, __file__, "--child", method, "sorted" if ordered else "raw"]
    return json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)


def main():
    assert __debug__, "run without -O: assertions are correctness checks"
    results = {}
    for ordered in (True, False):
        scenario = "already-sorted" if ordered else "unsorted-including-sort"
        for method in ("merge", "hash"):
            runs = [run(method, ordered) for _ in range(REPEATS)]
            assert all(r["summary"] == runs[0]["summary"] for r in runs)
            results[(scenario, method)] = runs
        assert results[(scenario, "merge")][0]["summary"] == results[(scenario, "hash")][0]["summary"]
    expected = results[("already-sorted", "merge")][0]["summary"]
    assert results[("unsorted-including-sort", "merge")][0]["summary"] == expected
    print(f"Python {platform.python_version()} / {platform.system()} {platform.machine()}")
    print(f"rows: left={LEFT_N:,}, right={RIGHT_N:,}, key space={KEYS:,}; result rows={expected[0]:,}; checksum={expected[1]:,}")
    print(f"{'scenario':<29}{'algorithm':<12}{'median ms':>12}{'peak MB':>12}")
    for scenario in ("already-sorted", "unsorted-including-sort"):
        for method in ("merge", "hash"):
            runs = results[(scenario, method)]
            times = sorted(r["ms"] for r in runs)
            peaks = sorted(r["peak_mb"] for r in runs)
            print(f"{scenario:<29}{method:<12}{times[1]:>12.1f}{peaks[1]:>12.1f}")
    print(f"time and tracemalloc peak: median of {REPEATS} fresh-process runs; output counts and checksums match")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3] == "sorted")
    else:
        main()
