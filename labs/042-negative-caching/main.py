"""042. Negative caching: 없는 키 조회가 저장소까지 가는 횟수 (표준 라이브러리만 사용, 시드 고정 시뮬레이션)."""
import argparse
import platform
import random
from pathlib import Path

SEED = 42
REQUESTS = 100_000       # 1ms 간격 요청 = 100초
MISS_SHARE = 0.30        # 요청 중 없는 키를 찾는 비율
MISS_KEYS = 5_000        # 없는 키 후보 수 (자주 찾는 키가 몰린다)
CREATED_SHARE = 0.02     # 후보 중 실험 도중 실제로 만들어지는 키 비율
SCENARIOS = {
    "none": ("없는 키는 매번 저장소", 0),
    "ttl5": ("부정 캐시 TTL 5초", 5_000),
    "ttl30": ("부정 캐시 TTL 30초", 30_000),
    "ttl30inv": ("TTL 30초 + 만들 때 지움", 30_000),
}


def run(kind):
    rnd = random.Random(SEED)
    created_at = {}
    for k in range(MISS_KEYS):
        if rnd.random() < CREATED_SHARE:
            created_at[k] = rnd.randrange(REQUESTS)
    ttl = SCENARIOS[kind][1]
    cache = {}                                  # 키 -> 부정 캐시가 만료되는 시각(ms)
    r = dict(miss_requests=0, store_lookups=0, wrong=0)
    for t in range(REQUESTS):
        if kind == "ttl30inv":
            for k, c in created_at.items():
                if c == t:
                    cache.pop(k, None)
        if rnd.random() >= MISS_SHARE:
            continue
        k = int(MISS_KEYS * rnd.random() ** 3)  # 앞쪽 키에 몰림
        r["miss_requests"] += 1
        exists = k in created_at and created_at[k] <= t
        if ttl and cache.get(k, -1) > t:
            if exists:
                r["wrong"] += 1                 # 이미 만들어졌는데 '없음'으로 답함
            continue
        r["store_lookups"] += 1
        if not exists and ttl:
            cache[k] = t + ttl
    r["saved_pct"] = (1 - r["store_lookups"] / r["miss_requests"]) * 100
    return r


def regression_tests(res):
    n = res["none"]
    assert n["store_lookups"] == n["miss_requests"] and n["wrong"] == 0
    assert res["ttl5"]["store_lookups"] < n["store_lookups"]
    assert res["ttl30"]["store_lookups"] < res["ttl5"]["store_lookups"]
    assert res["ttl30"]["wrong"] >= res["ttl5"]["wrong"] > 0
    assert res["ttl30inv"]["wrong"] == 0


def experiment():
    res = {k: run(k) for k in SCENARIOS}
    regression_tests(res)
    for k, (name, _) in SCENARIOS.items():
        v = res[k]
        print(f"| {name} | {v['store_lookups']:,} | {v['saved_pct']:.1f}% | {v['wrong']:,} |")
    return res


def write_svg(res):
    w, h = 900, 380
    x0, y0, pw, ph = 90, 70, 760, 220
    labels = {"none": "no negative cache", "ttl5": "TTL 5 s", "ttl30": "TTL 30 s", "ttl30inv": "TTL 30 s + invalidate"}
    colors = {"none": "#ef4444", "ttl5": "#f59e0b", "ttl30": "#2563eb", "ttl30inv": "#16a34a"}
    top = max(v["store_lookups"] for v in res.values())
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">Store lookups for missing keys</title>',
         '<desc id="d">' + "; ".join(f'{labels[k]} {v["store_lookups"]:,} store lookups, {v["wrong"]:,} wrong not-found answers' for k, v in res.items()) + '.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         f'<text x="28" y="40" font-size="22" font-weight="bold">Store lookups for missing keys ({next(iter(res.values()))["miss_requests"]:,} requests)</text>',
         f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="#94a3b8"/>']
    gw = pw / 4
    bw = 100
    for i, (k, v) in enumerate(res.items()):
        x = x0 + i * gw + (gw - bw) / 2
        bh = v["store_lookups"] / top * ph
        s.append(f'<rect x="{x:.1f}" y="{y0 + ph - bh:.1f}" width="{bw}" height="{max(bh, 1):.1f}" fill="{colors[k]}"/>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph - bh - 6:.1f}" font-size="13" text-anchor="middle">{v["store_lookups"]:,}</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 22}" font-size="12" text-anchor="middle">{labels[k]}</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 40}" font-size="12" text-anchor="middle">wrong answers {v["wrong"]:,}</text>')
    s.append(f'<text x="28" y="{h - 40}" font-size="13">Wrong answer = cache said "not found" for a key that had been created in the meantime.</text>')
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
