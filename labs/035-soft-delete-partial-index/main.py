"""035. Soft delete와 부분 인덱스: 전체 UNIQUE vs WHERE deleted_at IS NULL (sqlite3, 표준 라이브러리만 사용)."""
import argparse
import platform
import random
import sqlite3
import statistics
import time
from pathlib import Path
from xml.sax.saxutils import escape

ROWS = 100_000       # 전체 회원 행
ACTIVE = 10_000      # 그중 삭제되지 않은 행 (나머지 90%는 deleted_at 있음)
RESIGN = 1_000       # 탈퇴한 이메일로 다시 가입
DUP = 1_000          # 이미 활동 중인 이메일로 또 가입
LOOKUPS = 10_000
REPEATS = 5
TABLE = "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL, deleted_at INTEGER)"
INDEXES = {
    "full": "CREATE UNIQUE INDEX ux_email ON users(email)",
    "partial": "CREATE UNIQUE INDEX ux_email ON users(email) WHERE deleted_at IS NULL",
}


def build(kind):
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.execute(TABLE)
    rows = [(i, f"user{i:06d}@example.com", None if i < ACTIVE else 1_700_000_000 + i) for i in range(ROWS)]
    con.execute("BEGIN")
    con.executemany("INSERT INTO users VALUES (?,?,?)", rows)
    con.execute("COMMIT")
    before = con.execute("PRAGMA page_count").fetchone()[0]
    con.execute(INDEXES[kind])
    index_pages = con.execute("PRAGMA page_count").fetchone()[0] - before
    return con, index_pages


def signup(con, emails):
    errors = 0
    for e in emails:
        try:
            con.execute("INSERT INTO users(email, deleted_at) VALUES (?, NULL)", (e,))
        except sqlite3.IntegrityError:
            errors += 1
    return errors


def lookup_ms(con):
    rng = random.Random(35)
    emails = [f"user{rng.randrange(ACTIVE):06d}@example.com" for _ in range(LOOKUPS)]
    q = "SELECT id FROM users WHERE email=? AND deleted_at IS NULL"
    plan = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + q, ("x",)))
    assert "ux_email" in plan, plan
    runs = []
    for _ in range(REPEATS):
        t = time.perf_counter()
        for e in emails:
            assert con.execute(q, (e,)).fetchone() is not None
        runs.append((time.perf_counter() - t) * 1000)
    return statistics.median(runs)


def run(kind):
    con, pages = build(kind)
    ms = lookup_ms(con)
    resign = signup(con, [f"user{i:06d}@example.com" for i in range(ACTIVE, ACTIVE + RESIGN)])
    dup = signup(con, [f"user{i:06d}@example.com" for i in range(DUP)])
    return {"pages": pages, "resign_err": resign, "dup_blocked": dup, "ms": ms}


def regression_tests():
    con, _ = build("partial")
    con.execute("UPDATE users SET deleted_at=1 WHERE id=0")
    assert signup(con, ["user000000@example.com"]) == 0        # 탈퇴 후 재가입 가능
    assert signup(con, ["user000000@example.com"]) == 1        # 활동 중 중복은 막힘
    con2, _ = build("full")
    assert signup(con2, [f"user{ACTIVE:06d}@example.com"]) == 1  # 전체 UNIQUE는 재가입도 막음


def experiment():
    res = {k: run(k) for k in ("full", "partial")}
    f, p = res["full"], res["partial"]
    assert f["resign_err"] == RESIGN and p["resign_err"] == 0
    assert f["dup_blocked"] == DUP and p["dup_blocked"] == DUP
    print(f"| 전체 UNIQUE(email) | {f['pages']:,} | {f['resign_err']:,} | {f['dup_blocked']:,} | {f['ms']:,.1f} |")
    print(f"| 부분 UNIQUE(email) WHERE deleted_at IS NULL | {p['pages']:,} | {p['resign_err']:,} | {p['dup_blocked']:,} | {p['ms']:,.1f} |")
    return res


def write_svg(res):
    w, h = 900, 500
    rows = [("UNIQUE(email), all rows", res["full"], "#dc2626"), ("UNIQUE(email) WHERE deleted_at IS NULL", res["partial"], "#2563eb")]
    metrics = [("Index size (4 KB pages)", "pages", "{:,.0f}"),
               (f"Re-signup errors ({RESIGN:,} deleted emails)", "resign_err", "{:,.0f}"),
               (f"Duplicate active signups blocked ({DUP:,})", "dup_blocked", "{:,.0f}"),
               (f"ms for {LOOKUPS:,} active lookups", "ms", "{:,.1f}")]
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">Soft delete: full unique index vs partial unique index</title>',
         f'<desc id="d">{ROWS:,} users, {ROWS - ACTIVE:,} soft-deleted. Lookup time is the median of {REPEATS} runs.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         f'<text x="28" y="40" font-size="24" font-weight="bold">Soft delete: {ROWS:,} rows, {ROWS - ACTIVE:,} deleted</text>']
    for i, (label, key, fmt) in enumerate(metrics):
        y0 = 80 + i * 100
        mx = max(r[1][key] for r in rows)
        s.append(f'<text x="28" y="{y0}" font-size="16" font-weight="bold">{escape(label)}</text>')
        for j, (name, vals, color) in enumerate(rows):
            y = y0 + 12 + j * 30
            bw = vals[key] / mx * 420 if mx else 0
            s.append(f'<text x="28" y="{y + 15}" font-size="13">{escape(name)}</text>')
            s.append(f'<rect x="330" y="{y}" width="{bw:.1f}" height="20" fill="{color}"/>')
            s.append(f'<text x="{336 + bw:.1f}" y="{y + 15}" font-size="14" fill="{color}">{fmt.format(vals[key])}</text>')
    s.append(f'<text x="28" y="{h - 16}" font-size="13">Python {platform.python_version()}, SQLite {sqlite3.sqlite_version}; time varies by machine.</text></g></svg>')
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
    print("| 방식 | 인덱스 페이지 | 재가입 오류 | 활동 중 중복 막음 | 조회 1만 건 ms |")
    res = experiment()
    if a.write_svg:
        write_svg(res)
