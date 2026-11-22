"""043. 캐시 워밍업: 재시작 직후 적중률과 저장소 조회 (표준 라이브러리만 사용, 시드 고정 시뮬레이션)."""
import argparse
import bisect
import platform
import random
from collections import OrderedDict
from itertools import accumulate
from pathlib import Path

SEED = 42
KEYS = 10_000            # 전체 키 수
CAPACITY = 2_000         # 캐시에 들어가는 키 수 (LRU)
REQUESTS = 100_000       # 1ms 간격 요청 = 100초
ZIPF_S = 1.1             # 인기 키에 몰리는 정도
FIRST_MS = 10_000        # '재시작 직후'로 보는 처음 10초
SCENARIOS = {
    "cold": ("워밍업 없음", 0),
    "warm500": ("인기 키 500개 미리 채움", 500),
    "warm2000": ("인기 키 2,000개 미리 채움", 2_000),
}


def run(kind):
    rnd = random.Random(SEED)
    cum = list(accumulate(1 / (i + 1) ** ZIPF_S for i in range(KEYS)))
    total = cum[-1]
    cache = OrderedDict()
    preload = SCENARIOS[kind][1]
    for k in range(preload):                     # 이전 서버가 알려 준 인기 순서대로 채움
        cache[k] = True
    r = dict(preload=preload, early_req=0, early_hit=0, early_store=0, store=0)
    for t in range(REQUESTS):
        k = bisect.bisect_left(cum, rnd.random() * total)
        early = t < FIRST_MS
        if early:
            r["early_req"] += 1
        if k in cache:
            cache.move_to_end(k)
            if early:
                r["early_hit"] += 1
            continue
        r["store"] += 1
        if early:
            r["early_store"] += 1
        cache[k] = True
        if len(cache) > CAPACITY:
            cache.popitem(last=False)
    r["early_hit_pct"] = r["early_hit"] / r["early_req"] * 100
    r["store_total"] = r["store"] + preload      # 미리 채우는 조회도 저장소 부하
    return r


def regression_tests(res):
    c, w5, w2 = res["cold"], res["warm500"], res["warm2000"]
    assert c["early_hit_pct"] < w5["early_hit_pct"] < w2["early_hit_pct"]
    assert c["early_store"] > w5["early_store"] > w2["early_store"]


def experiment():
    res = {k: run(k) for k in SCENARIOS}
    regression_tests(res)
    for k, (name, _) in SCENARIOS.items():
        v = res[k]
        print(f"| {name} | {v['early_hit_pct']:.1f}% | {v['early_store']:,} | {v['store_total']:,} |")
    return res


def write_svg(res):
    w, h = 900, 380
    x0, y0, pw, ph = 90, 70, 760, 220
    labels = {"cold": "no warm-up", "warm500": "warm 500 keys", "warm2000": "warm 2,000 keys"}
    colors = {"cold": "#ef4444", "warm500": "#f59e0b", "warm2000": "#16a34a"}
    top = max(v["early_store"] for v in res.values())
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">Store lookups in the first 10 seconds after restart</title>',
         '<desc id="d">' + "; ".join(f'{labels[k]} {v["early_store"]:,} store lookups, hit rate {v["early_hit_pct"]:.1f}%' for k, v in res.items()) + '.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         f'<text x="28" y="40" font-size="22" font-weight="bold">Store lookups in the first 10 s after restart</text>',
         f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="#94a3b8"/>']
    gw = pw / 3
    bw = 120
    for i, (k, v) in enumerate(res.items()):
        x = x0 + i * gw + (gw - bw) / 2
        bh = v["early_store"] / top * ph
        s.append(f'<rect x="{x:.1f}" y="{y0 + ph - bh:.1f}" width="{bw}" height="{max(bh, 1):.1f}" fill="{colors[k]}"/>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph - bh - 6:.1f}" font-size="13" text-anchor="middle">{v["early_store"]:,}</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 22}" font-size="12" text-anchor="middle">{labels[k]}</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 40}" font-size="12" text-anchor="middle">hit rate {v["early_hit_pct"]:.1f}%</text>')
    s.append(f'<text x="28" y="{h - 40}" font-size="13">First 10 s = {FIRST_MS:,} requests of {REQUESTS:,}; LRU cache {CAPACITY:,} of {KEYS:,} keys.</text>')
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
