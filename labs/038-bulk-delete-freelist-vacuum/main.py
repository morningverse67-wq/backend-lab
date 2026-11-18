"""038. 대량 DELETE 뒤 파일 크기: freelist와 VACUUM (sqlite3, 표준 라이브러리만 사용)."""
import argparse
import os
import platform
import sqlite3
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

ROWS = 200_000       # 로그 행
DELETE_RATIO = 0.9   # 지우는 비율(오래된 90%)
SCENARIOS = {
    "delete": "DELETE만",
    "vacuum": "DELETE 뒤 VACUUM",
    "incremental": "auto_vacuum=INCREMENTAL + incremental_vacuum",
}


def mb(path):
    return os.path.getsize(path) / 1_048_576


def pages(c):
    return c.execute("PRAGMA page_count").fetchone()[0], c.execute("PRAGMA freelist_count").fetchone()[0]


def run(kind):
    d = tempfile.mkdtemp(prefix="lab038-")
    path = os.path.join(d, "lab.db")
    c = sqlite3.connect(path, isolation_level=None)
    if kind == "incremental":
        c.execute("PRAGMA auto_vacuum=INCREMENTAL")   # 테이블을 만들기 전에 정해야 한다
    c.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, body TEXT NOT NULL)")
    c.execute("CREATE INDEX logs_created_at ON logs(created_at)")
    c.execute("BEGIN")
    c.executemany("INSERT INTO logs VALUES (?,?,?)", ((i, i, f"event-{i:07d}-" + "x" * 100) for i in range(ROWS)))
    c.execute("COMMIT")
    res = {"before_mb": mb(path), "before_pages": pages(c)}
    cut = int(ROWS * DELETE_RATIO)
    c.execute("DELETE FROM logs WHERE created_at < ?", (cut,))
    res["deleted"] = c.execute("SELECT changes()").fetchone()[0]
    res["after_delete_mb"] = mb(path)
    res["after_delete_pages"] = pages(c)
    if kind == "vacuum":
        c.execute("VACUUM")
    elif kind == "incremental":
        c.execute("PRAGMA incremental_vacuum").fetchall()
    res["final_mb"] = mb(path)
    res["final_pages"] = pages(c)
    # 지운 자리는 다시 쓰인다: 지운 만큼 다시 넣어도 DELETE만 한 파일은 커지지 않는다
    if kind == "delete":
        c.execute("BEGIN")
        c.executemany("INSERT INTO logs VALUES (?,?,?)", ((ROWS + i, ROWS + i, f"event-{ROWS + i:07d}-" + "x" * 100) for i in range(cut)))
        c.execute("COMMIT")
        res["reinsert_mb"] = mb(path)
        res["reinsert_pages"] = pages(c)
    res["left"] = c.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    res["integrity"] = c.execute("PRAGMA integrity_check").fetchone()[0]
    c.close()
    return res


def regression_tests(res):
    cut = int(ROWS * DELETE_RATIO)
    for k, v in res.items():
        assert v["deleted"] == cut and v["integrity"] == "ok"
        assert v["after_delete_mb"] == v["before_mb"]                 # DELETE만으로는 파일이 줄지 않는다
        assert v["after_delete_pages"][1] > 0                        # 빈 페이지는 freelist로 간다
    d = res["delete"]
    assert d["final_pages"][1] == d["after_delete_pages"][1] > 0
    assert d["reinsert_pages"][1] == 0 and d["left"] == ROWS          # 다시 넣으면 빈 페이지를 먼저 쓴다
    assert d["reinsert_pages"][0] - d["before_pages"][0] < d["after_delete_pages"][1] * 0.05
    for k in ("vacuum", "incremental"):
        assert res[k]["final_pages"][1] == 0 and res[k]["final_mb"] < res[k]["before_mb"] * 0.3
        assert res[k]["left"] == ROWS - cut


def experiment():
    res = {k: run(k) for k in SCENARIOS}
    regression_tests(res)
    for k, name in SCENARIOS.items():
        v = res[k]
        print(f"| {name} | {v['before_mb']:.2f} | {v['after_delete_mb']:.2f} ({v['after_delete_pages'][1]:,}) | {v['final_mb']:.2f} ({v['final_pages'][1]:,}) |")
    d = res["delete"]
    print(f"지운 행 {d['deleted']:,}개, DELETE만 한 파일에 {d['deleted']:,}행을 다시 넣은 뒤: {d['reinsert_mb']:.2f} MB, freelist {d['reinsert_pages'][1]:,}, 페이지 {d['before_pages'][0]:,} → {d['reinsert_pages'][0]:,}")
    return res


def write_svg(res):
    w, h = 900, 380
    x0, y0, pw, ph = 90, 70, 760, 220
    mx = max(v["before_mb"] for v in res.values())
    colors = {"before": "#94a3b8", "after": "#f59e0b", "final": "#2563eb"}
    labels = {"delete": "DELETE only", "vacuum": "DELETE + VACUUM", "incremental": "auto_vacuum=INCREMENTAL"}
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">Database file size before and after deleting 90% of rows</title>',
         f'<desc id="d">{ROWS:,} rows, {res["delete"]["deleted"]:,} deleted. DELETE only stays {res["delete"]["final_mb"]:.2f} MB; VACUUM {res["vacuum"]["final_mb"]:.2f} MB.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         f'<text x="28" y="40" font-size="22" font-weight="bold">File size (MB) after deleting {res["delete"]["deleted"]:,} of {ROWS:,} rows</text>',
         f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="#94a3b8"/>']
    gw = pw / len(res)
    bw = 60
    for i, (k, v) in enumerate(res.items()):
        gx = x0 + i * gw + 30
        for j, (key, val) in enumerate((("before", v["before_mb"]), ("after", v["after_delete_mb"]), ("final", v["final_mb"]))):
            bh = val / mx * ph
            x = gx + j * (bw + 6)
            s.append(f'<rect x="{x:.1f}" y="{y0 + ph - bh:.1f}" width="{bw}" height="{bh:.1f}" fill="{colors[key]}"/>')
            s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph - bh - 6:.1f}" font-size="12" text-anchor="middle">{val:.2f}</text>')
        s.append(f'<text x="{gx + 1.5 * bw + 6:.1f}" y="{y0 + ph + 22}" font-size="13" text-anchor="middle">{escape(labels[k])}</text>')
    for j, (key, name) in enumerate((("before", "before DELETE"), ("after", "after DELETE"), ("final", "after cleanup step"))):
        s.append(f'<rect x="{x0 + j * 200}" y="{h - 52}" width="14" height="14" fill="{colors[key]}"/><text x="{x0 + j * 200 + 20}" y="{h - 40}" font-size="13">{name}</text>')
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
    print("| 방법 | DELETE 전 MB | DELETE 뒤 MB (빈 페이지) | 정리 뒤 MB (빈 페이지) |")
    res = experiment()
    if a.write_svg:
        write_svg(res)
