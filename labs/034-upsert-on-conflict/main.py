"""034. UPSERT: 조회 후 INSERT/UPDATE vs INSERT ... ON CONFLICT (sqlite3, 표준 라이브러리만 사용)."""
import argparse
import platform
import random
import sqlite3
import statistics
import time
from pathlib import Path
from xml.sax.saxutils import escape

PAIRS = 1_000        # 같은 새 키에 동시에 도착하는 요청 쌍
REQUESTS = 100_000   # 속도 실험 요청 수
KEYS = 1_000
REPEATS = 5
SCHEMA = "CREATE TABLE counter (k INTEGER PRIMARY KEY, n INTEGER NOT NULL)"


def check_then_write(con, k):
    row = con.execute("SELECT n FROM counter WHERE k=?", (k,)).fetchone()
    if row is None:
        con.execute("INSERT INTO counter VALUES (?, 1)", (k,))
    else:
        con.execute("UPDATE counter SET n=n+1 WHERE k=?", (k,))


def on_conflict(con, k):
    con.execute("INSERT INTO counter VALUES (?, 1) ON CONFLICT(k) DO UPDATE SET n=n+1", (k,))


def race(method):
    """요청 A, B가 같은 새 키를 동시에 처리. 둘 다 조회를 끝낸 뒤에 쓰는 순서를 그대로 재현한다."""
    errors = 0
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.execute(SCHEMA)
    for k in range(PAIRS):
        if method == "on_conflict":
            on_conflict(con, k)
            on_conflict(con, k)
            continue
        seen = [con.execute("SELECT n FROM counter WHERE k=?", (k,)).fetchone() for _ in range(2)]  # 둘 다 "없음"
        for row in seen:
            try:
                if row is None:
                    con.execute("INSERT INTO counter VALUES (?, 1)", (k,))
                else:
                    con.execute("UPDATE counter SET n=n+1 WHERE k=?", (k,))
            except sqlite3.IntegrityError:
                errors += 1
    total = con.execute("SELECT COALESCE(SUM(n),0) FROM counter").fetchone()[0]
    return errors, 2 * PAIRS - total


def speed(fn):
    rng = random.Random(34)
    keys = [rng.randrange(KEYS) for _ in range(REQUESTS)]
    runs = []
    for _ in range(REPEATS):
        con = sqlite3.connect(":memory:", isolation_level=None)
        con.execute(SCHEMA)
        con.execute("BEGIN")
        t = time.perf_counter()
        for k in keys:
            fn(con, k)
        runs.append((time.perf_counter() - t) * 1000)
        con.execute("COMMIT")
        total = con.execute("SELECT SUM(n) FROM counter").fetchone()[0]
        assert total == REQUESTS
    return statistics.median(runs)


def regression_tests():
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.execute(SCHEMA)
    for _ in range(3):
        on_conflict(con, 7)
    assert con.execute("SELECT n FROM counter WHERE k=7").fetchone()[0] == 3
    con2 = sqlite3.connect(":memory:", isolation_level=None)
    con2.execute(SCHEMA)
    check_then_write(con2, 7)
    check_then_write(con2, 7)
    assert con2.execute("SELECT n FROM counter WHERE k=7").fetchone()[0] == 2


def experiment():
    r_err, r_lost = race("check")
    o_err, o_lost = race("on_conflict")
    assert r_err == PAIRS and r_lost == PAIRS and o_err == 0 and o_lost == 0
    t_check, t_on = speed(check_then_write), speed(on_conflict)
    print(f"| 조회 후 INSERT/UPDATE | {r_err:,} | {r_lost:,} | {t_check:,.0f} |")
    print(f"| INSERT ... ON CONFLICT | {o_err:,} | {o_lost:,} | {t_on:,.0f} |")
    return {"check": (r_err, r_lost, t_check), "on": (o_err, o_lost, t_on)}


def write_svg(res):
    w, h = 900, 400
    rows = [("Check, then INSERT/UPDATE", res["check"], "#dc2626"), ("INSERT ... ON CONFLICT", res["on"], "#2563eb")]
    metrics = [("Duplicate-key errors", 0, PAIRS, "{:,.0f}"), ("Lost increments", 1, PAIRS, "{:,.0f}"),
               (f"ms for {REQUESTS:,} requests", 2, max(r[1][2] for r in rows), "{:,.0f}")]
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">Two concurrent requests on a new key: check-then-write vs ON CONFLICT</title>',
         f'<desc id="d">{PAIRS:,} pairs of concurrent requests on the same new key, and {REQUESTS:,} requests for speed (median of {REPEATS} runs).</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         f'<text x="28" y="40" font-size="24" font-weight="bold">Upsert: {PAIRS:,} concurrent pairs on a new key</text>']
    for i, (label, idx, mx, fmt) in enumerate(metrics):
        y0 = 80 + i * 100
        s.append(f'<text x="28" y="{y0}" font-size="16" font-weight="bold">{escape(label)}</text>')
        for j, (name, vals, color) in enumerate(rows):
            y = y0 + 12 + j * 30
            bw = vals[idx] / mx * 480 if mx else 0
            s.append(f'<text x="28" y="{y + 15}" font-size="14">{escape(name)}</text>')
            s.append(f'<rect x="270" y="{y}" width="{bw:.1f}" height="20" fill="{color}"/>')
            s.append(f'<text x="{276 + bw:.1f}" y="{y + 15}" font-size="14" fill="{color}">{fmt.format(vals[idx])}</text>')
    s.append(f'<text x="28" y="{h - 16}" font-size="13">Python {platform.python_version()}, SQLite {sqlite3.sqlite_version}; speed varies by machine.</text></g></svg>')
    Path(__file__).with_name("result.svg").write_text("\n".join(s) + "\n", encoding="utf-8")
    print("wrote result.svg")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-svg", action="store_true")
    a = ap.parse_args()
    if not __debug__:
        raise SystemExit("Run without -O")
    print(f"Python {platform.python_version()} | SQLite {sqlite3.sqlite_version} | {platform.system()} {platform.machine()}")
    regression_tests()
    print("| 방식 | 중복 키 오류 | 사라진 증가분 | 10만 건 ms |")
    res = experiment()
    if a.write_svg:
        write_svg(res)
