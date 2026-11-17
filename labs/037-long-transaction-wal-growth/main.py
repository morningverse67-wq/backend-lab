"""037. 긴 트랜잭션이 막는 정리 작업: 오래 열린 읽기가 남기는 옛 버전 행 (sqlite3 WAL, 표준 라이브러리만 사용)."""
import argparse
import os
import platform
import random
import sqlite3
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

ROWS = 10_000        # 계좌 행
ROUNDS = 20          # 쓰기 묶음 수
UPDATES = 1_000      # 묶음마다 바꾸는 행 수
SCENARIOS = {
    "none": "긴 읽기 없음",
    "closed": "긴 읽기를 10번째 묶음 뒤에 닫음",
    "open": "긴 읽기가 끝까지 열려 있음",
}


def run(kind):
    d = tempfile.mkdtemp(prefix="lab037-")
    path = os.path.join(d, "lab.db")
    w = sqlite3.connect(path, isolation_level=None)
    w.execute("PRAGMA journal_mode=WAL")
    w.execute("PRAGMA wal_autocheckpoint=0")          # 정리는 묶음마다 직접 부른다
    w.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, balance INTEGER NOT NULL, memo TEXT NOT NULL)")
    w.execute("BEGIN")
    w.executemany("INSERT INTO accounts VALUES (?,?,?)", ((i, 1_000, "x" * 80) for i in range(ROWS)))
    w.execute("COMMIT")
    w.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    reader = None
    if kind != "none":
        reader = sqlite3.connect(path, isolation_level=None)
        reader.execute("BEGIN")
        before = reader.execute("SELECT SUM(balance) FROM accounts").fetchone()[0]   # 스냅샷이 이때 고정된다
    rng = random.Random(37)
    wal_mb, busy = [], 0
    for r in range(ROUNDS):
        ids = [rng.randrange(ROWS) for _ in range(UPDATES)]
        w.execute("BEGIN")
        w.executemany("UPDATE accounts SET balance = balance + 1 WHERE id = ?", ((i,) for i in ids))
        w.execute("COMMIT")
        if kind == "closed" and r == ROUNDS // 2 - 1:
            reader.execute("COMMIT"); reader.close(); reader = None
        b, log, ckpt = w.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        busy += b
        wal_mb.append(os.path.getsize(path + "-wal") / 1_048_576)
    res = {"wal_mb": wal_mb, "busy": busy, "max_mb": max(wal_mb), "final_mb": wal_mb[-1]}
    if reader is not None:
        res["reader_sum"] = reader.execute("SELECT SUM(balance) FROM accounts").fetchone()[0]
        res["reader_before"] = before
        reader.execute("COMMIT"); reader.close()
        b, _, _ = w.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        res["after_close_mb"] = os.path.getsize(path + "-wal") / 1_048_576
    res["writer_sum"] = w.execute("SELECT SUM(balance) FROM accounts").fetchone()[0]
    w.close()
    return res


def regression_tests(res):
    assert res["none"]["busy"] == 0 and res["none"]["max_mb"] == 0
    assert res["open"]["busy"] == ROUNDS and res["open"]["final_mb"] == res["open"]["max_mb"] > 0
    assert res["open"]["reader_sum"] == res["open"]["reader_before"] == ROWS * 1_000   # 옛 스냅샷을 계속 본다
    assert res["open"]["after_close_mb"] == 0                                           # 닫으면 정리된다
    assert res["closed"]["final_mb"] == 0 and 0 < res["closed"]["busy"] < ROUNDS
    assert all(v["writer_sum"] == ROWS * 1_000 + ROUNDS * UPDATES for v in res.values())


def experiment():
    res = {k: run(k) for k in SCENARIOS}
    regression_tests(res)
    for k, name in SCENARIOS.items():
        v = res[k]
        print(f"| {name} | {v['busy']}/{ROUNDS} | {v['max_mb']:.2f} | {v['final_mb']:.2f} |")
    o = res["open"]
    print(f"긴 읽기가 본 잔액 합계: {o['reader_sum']:,} (쓰기 쪽 {o['writer_sum']:,}), 닫은 뒤 WAL {o['after_close_mb']:.2f} MB")
    return res


def write_svg(res):
    w, h = 900, 380
    x0, y0, pw, ph = 90, 70, 760, 230
    mx = max(max(v["wal_mb"]) for v in res.values()) or 1
    colors = {"none": "#16a34a", "closed": "#2563eb", "open": "#dc2626"}
    labels = {"none": "no long read", "closed": "long read closed after round 10", "open": "long read kept open"}
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">WAL size after each checkpoint, with and without a long-running read transaction</title>',
         f'<desc id="d">{ROWS:,} rows, {ROUNDS} rounds of {UPDATES:,} updates. Open read: WAL {res["open"]["final_mb"]:.2f} MB; no read: 0 MB.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         '<text x="28" y="40" font-size="22" font-weight="bold">WAL size after checkpoint(TRUNCATE), per round</text>',
         f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="#94a3b8"/>',
         f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y0 + ph}" stroke="#94a3b8"/>',
         f'<text x="{x0 - 8}" y="{y0 + 5}" font-size="12" text-anchor="end">{mx:.2f} MB</text>',
         f'<text x="{x0 - 8}" y="{y0 + ph}" font-size="12" text-anchor="end">0</text>',
         f'<text x="{x0 + pw / 2}" y="{y0 + ph + 22}" font-size="12" text-anchor="middle">round (1..{ROUNDS})</text>']
    for k, v in res.items():
        pts = " ".join(f"{x0 + i / (ROUNDS - 1) * pw:.1f},{y0 + ph - m / mx * ph:.1f}" for i, m in enumerate(v["wal_mb"]))
        s.append(f'<polyline points="{pts}" fill="none" stroke="{colors[k]}" stroke-width="3"/>')
    for i, k in enumerate(res):
        v = res[k]
        s.append(f'<text x="{x0 + 10}" y="{y0 + 18 + i * 20}" font-size="13" fill="{colors[k]}">{escape(labels[k])}: final {v["final_mb"]:.2f} MB, busy {v["busy"]}/{ROUNDS}</text>')
    s.append(f'<text x="28" y="{h - 14}" font-size="13">Python {platform.python_version()}, SQLite {sqlite3.sqlite_version}, page 4096.</text></g></svg>')
    Path(__file__).with_name("result.svg").write_text("\n".join(s) + "\n", encoding="utf-8")
    print("wrote result.svg")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-svg", action="store_true")
    a = ap.parse_args()
    if not __debug__:
        raise SystemExit("Run without -O")
    print(f"Python {platform.python_version()} | SQLite {sqlite3.sqlite_version} | {platform.system()} {platform.machine()}")
    print(f"| 상황 | 정리 실패(busy) | WAL 최대 MB | 마지막 WAL MB |")
    res = experiment()
    if a.write_svg:
        write_svg(res)
