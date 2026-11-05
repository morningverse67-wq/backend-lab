"""
Lab 026: Large CSV, read everything at once vs stream row by row (memory)

Builds an order CSV (100k and 400k rows) and runs the same aggregation
in a fresh child process per approach, so each process's peak memory
belongs to that approach alone:

  - list(csv.DictReader(f))   every row as a dict, then aggregate
  - list(csv.reader(f))       every row as a list, then aggregate
  - for row in csv.reader(f)  stream: keep only the running totals
  - median, all rows          median amount from the full row list
  - median, one column        stream, keep only amounts in array('q')

Two memory numbers per run:
  - RSS peak increase: ru_maxrss after the work minus before (what the OS sees)
  - tracemalloc peak: bytes Python allocated at the peak (separate run,
    because tracemalloc itself slows the work down)

Run:
    python3 main.py
"""
from array import array
from statistics import median_low
import csv
import json
import os
import platform
import random
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time
import tracemalloc

SIZES = (100_000, 400_000)
REPEATS = 3
STATUSES = ("PAID", "PAID", "PAID", "PAID", "PAID", "PAID", "PAID", "CANCELLED", "CANCELLED", "REFUNDED")
APPROACHES = [  # key, label
    ("dict_all", "DictReader 전체 리스트"),
    ("list_all", "reader 전체 리스트"),
    ("stream", "reader 스트리밍"),
    ("median_all", "중앙값: 전체 행 보관"),
    ("median_col", "중앙값: 금액 열만 보관"),
]


# ---------------------------------------------------------------- data
def write_csv(path, n):
    rnd = random.Random(26)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "user_id", "status", "amount", "created_at", "memo"])
        for i in range(1, n + 1):
            w.writerow([
                i,
                rnd.randint(1, 50_000),
                rnd.choice(STATUSES),
                rnd.randint(1, 3_000) * 100,               # won, integer
                f"2026-10-{rnd.randint(1, 31):02d}T{rnd.randint(0, 23):02d}:{rnd.randint(0, 59):02d}:00",
                f"memo-{rnd.randint(0, 999_999):06d}",
            ])


# ---------------------------------------------------------------- the five approaches
def aggregate(rows_iter, status_idx, amount_idx):
    count, paid_sum = {}, 0
    for row in rows_iter:
        st = row[status_idx]
        count[st] = count.get(st, 0) + 1
        if st == "PAID":
            paid_sum += int(row[amount_idx])
    return sorted(count.items()), paid_sum


def run_approach(key, path):
    with open(path, newline="", encoding="utf-8") as f:
        if key == "dict_all":
            rows = list(csv.DictReader(f))
            count, paid_sum = {}, 0
            for r in rows:
                st = r["status"]
                count[st] = count.get(st, 0) + 1
                if st == "PAID":
                    paid_sum += int(r["amount"])
            return [sorted(count.items()), paid_sum, len(rows)]
        reader = csv.reader(f)
        header = next(reader)
        s_i, a_i = header.index("status"), header.index("amount")
        if key == "list_all":
            rows = list(reader)
            return [*aggregate(rows, s_i, a_i), len(rows)]
        if key == "stream":
            n = 0

            def counted():
                nonlocal n
                for row in reader:
                    n += 1
                    yield row
            return [*aggregate(counted(), s_i, a_i), n]
        if key == "median_all":
            rows = list(reader)
            amounts = sorted(int(r[a_i]) for r in rows)
            return [median_low(amounts), len(rows)]
        if key == "median_col":
            amounts = array("q", (int(r[a_i]) for r in reader))
            return [median_low(sorted(amounts)), len(amounts)]
    raise ValueError(key)


def maxrss_bytes():
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v if sys.platform == "darwin" else v * 1024   # macOS: bytes, Linux: KiB


def child(key, path, mode):
    if mode == "trace":
        tracemalloc.start()
        tracemalloc.reset_peak()
        result = run_approach(key, path)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(json.dumps({"result": result, "trace_peak": peak}))
    else:
        before = maxrss_bytes()
        t0 = time.perf_counter()
        result = run_approach(key, path)
        ms = (time.perf_counter() - t0) * 1000
        print(json.dumps({"result": result, "ms": ms, "rss_delta": maxrss_bytes() - before}))


def spawn(key, path, mode):
    out = subprocess.run([sys.executable, __file__, "--child", key, path, mode],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


# ---------------------------------------------------------------- parent
def mb(b):
    return b / 1_000_000


def main():
    assert __debug__, "run without -O: the asserts are the correctness checks"
    tmp = tempfile.mkdtemp(prefix="lab026-")
    table = {}
    try:
        for n in SIZES:
            path = os.path.join(tmp, f"orders_{n}.csv")
            write_csv(path, n)
            size = os.path.getsize(path)
            with open(path, "rb") as f:          # warm the OS page cache so every approach reads from memory
                while f.read(1 << 20):
                    pass
            results = {}
            for key, _ in APPROACHES:
                runs = [spawn(key, path, "rss") for _ in range(REPEATS)]
                tr = spawn(key, path, "trace")
                for r in runs + [tr]:
                    assert r["result"] == runs[0]["result"], (key, r["result"], runs[0]["result"])
                results[key] = runs[0]["result"]
                table[(n, key)] = {
                    "file": size,
                    "ms": min(r["ms"] for r in runs),
                    "rss": median_low([r["rss_delta"] for r in runs]),
                    "trace": tr["trace_peak"],
                }
            # every approach agrees on the answer
            assert results["dict_all"] == results["list_all"] == results["stream"]
            assert results["median_all"] == results["median_col"]
            assert results["stream"][2] == n and results["median_col"][1] == n
            # median cross-check with SQLite on the same file
            con = sqlite3.connect(":memory:")
            con.execute("CREATE TABLE t(amount INTEGER)")
            with open(path, newline="", encoding="utf-8") as f:
                r = csv.DictReader(f)
                con.executemany("INSERT INTO t VALUES (?)", ((int(x["amount"]),) for x in r))
            k = (n - 1) // 2
            sql_med = con.execute("SELECT amount FROM t ORDER BY amount LIMIT 1 OFFSET ?", (k,)).fetchone()[0]
            assert sql_med == results["median_col"][0]
            con.close()
            table[(n, "answer")] = results
            os.remove(path)
    finally:
        for name in os.listdir(tmp):
            os.remove(os.path.join(tmp, name))
        os.rmdir(tmp)

    print(f"Python {platform.python_version()} / SQLite {sqlite3.sqlite_version} / {platform.system()} {platform.machine()}")
    for n in SIZES:
        ans = table[(n, "answer")]
        print(f"\n{n:,} rows, file {mb(table[(n, 'stream')]['file']):.1f} MB  "
              f"(status counts {ans['stream'][0]}, PAID sum {ans['stream'][1]:,}, median {ans['median_col'][0]:,})")
        print(f"{'approach':<24}{'time ms':>10}{'RSS peak +MB':>15}{'tracemalloc MB':>16}{'x file':>8}")
        for key, label in APPROACHES:
            t = table[(n, key)]
            print(f"{key:<24}{t['ms']:>10.1f}{mb(t['rss']):>15.1f}{mb(t['trace']):>16.2f}{t['trace'] / t['file']:>8.2f}")
    print("\ntime: min of 3 runs, RSS: median of 3 runs, tracemalloc: 1 separate run; each run is a fresh process")
    print("all approaches returned the same counts, PAID sum and median (checked against SQLite) - asserts passed")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        main()
