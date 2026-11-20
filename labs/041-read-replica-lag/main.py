"""041. Read replica 지연: 쓰기 직후 읽기에서 옛 값을 읽는 비율 (표준 라이브러리만 사용, 시드 고정 시뮬레이션)."""
import argparse
import platform
import random
from pathlib import Path

SEED = 41
WRITES = 10_000          # 쓰기 1번 + 곧바로 이어지는 읽기 1번
OTHER_READS = 4          # 쓰기와 무관한 읽기(쓰기 뒤 5~60초 사이)
SPIKE_P = 0.03           # 복제 지연이 튀는 확률
WINDOW_MS = 1_000        # '쓴 뒤 1초는 primary' 방식의 창
SCENARIOS = {
    "replica": "항상 replica",
    "window": "쓴 뒤 1초는 primary",
    "lsn": "반영 위치 확인 후 replica",
    "primary": "항상 primary",
}


def lag_ms(rnd):
    """복제 지연(ms): 평소 평균 30ms, 3%는 평균 1.5초로 튄다."""
    return rnd.expovariate(1 / 1500) if rnd.random() < SPIKE_P else rnd.expovariate(1 / 30)


def run(kind):
    rnd = random.Random(SEED)
    r = dict(after_reads=0, stale=0, primary_reads=0, total_reads=0, waited=0)
    for _ in range(WRITES):
        lag = lag_ms(rnd)                     # 이 쓰기가 replica에 닿기까지
        gap = rnd.expovariate(1 / 150)        # 쓰기 뒤 읽기까지 걸린 시간(ms)
        r["after_reads"] += 1
        r["total_reads"] += 1 + OTHER_READS
        replica_has_it = gap >= lag
        if kind == "primary":
            r["primary_reads"] += 1
        elif kind == "window":
            if gap < WINDOW_MS:
                r["primary_reads"] += 1
            elif not replica_has_it:
                r["stale"] += 1
        elif kind == "lsn":
            if not replica_has_it:            # 반영 전이면 primary로 돌린다
                r["primary_reads"] += 1
        else:
            if not replica_has_it:
                r["stale"] += 1
        if kind == "primary":
            r["primary_reads"] += OTHER_READS
    r["stale_pct"] = r["stale"] / r["after_reads"] * 100
    r["primary_pct"] = r["primary_reads"] / r["total_reads"] * 100
    return r


def regression_tests(res):
    assert res["replica"]["stale"] > 0
    assert res["primary"]["stale"] == 0 and res["lsn"]["stale"] == 0
    assert res["window"]["stale"] < res["replica"]["stale"]
    assert res["primary"]["primary_pct"] == 100
    assert res["lsn"]["primary_pct"] < res["window"]["primary_pct"] < res["primary"]["primary_pct"]


def experiment():
    res = {k: run(k) for k in SCENARIOS}
    regression_tests(res)
    for k, name in SCENARIOS.items():
        v = res[k]
        print(f"| {name} | {v['stale']:,} | {v['stale_pct']:.2f}% | {v['primary_reads']:,} | {v['primary_pct']:.1f}% |")
    return res


def write_svg(res):
    w, h = 900, 380
    x0, y0, pw, ph = 90, 70, 760, 220
    labels = {"replica": "always replica", "window": "primary for 1 s", "lsn": "check applied position", "primary": "always primary"}
    colors = {"replica": "#ef4444", "window": "#f59e0b", "lsn": "#2563eb", "primary": "#94a3b8"}
    top = max(v["stale"] for v in res.values())
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">Stale reads right after a write</title>',
         '<desc id="d">' + "; ".join(f'{labels[k]} {v["stale"]:,} stale, {v["primary_pct"]:.1f}% reads on primary' for k, v in res.items()) + '.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         f'<text x="28" y="40" font-size="22" font-weight="bold">Stale reads right after a write ({WRITES:,} writes)</text>',
         f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="#94a3b8"/>']
    gw = pw / 4
    bw = 100
    for i, (k, v) in enumerate(res.items()):
        x = x0 + i * gw + (gw - bw) / 2
        bh = v["stale"] / top * ph
        s.append(f'<rect x="{x:.1f}" y="{y0 + ph - bh:.1f}" width="{bw}" height="{max(bh, 1):.1f}" fill="{colors[k]}"/>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph - bh - 6:.1f}" font-size="13" text-anchor="middle">{v["stale"]:,}</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 22}" font-size="12" text-anchor="middle">{labels[k]}</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 40}" font-size="12" text-anchor="middle">primary {v["primary_pct"]:.1f}%</text>')
    s.append(f'<text x="28" y="{h - 40}" font-size="13">Stale = read right after a write returned the old value. "primary %" = share of all reads served by the primary.</text>')
    s.append(f'<text x="28" y="{h - 14}" font-size="13">Python {platform.python_version()}, seeded simulation (seed {SEED}).</text></g></svg>')
    Path(__file__).with_name("result.svg").write_text("\n".join(s) + "\n", encoding="utf-8")
    print("wrote result.svg")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-svg", action="store_true")
    a = ap.parse_args()
    res = experiment()
    if a.write_svg:
        write_svg(res)
