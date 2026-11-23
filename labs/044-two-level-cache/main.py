"""044. 2단계 캐시(로컬 + 분산): 적중 위치별 응답 시간과 서버 간 값 불일치 (표준 라이브러리만 사용, 시드 고정 시뮬레이션)."""
import argparse
import bisect
import platform
import random
from itertools import accumulate
from pathlib import Path

SEED = 42
KEYS = 1_000             # 전체 키 수
SERVERS = 4              # 앱 서버 수 (서버마다 로컬 캐시 하나)
REQUESTS = 100_000       # 1ms 간격 요청 = 100초
WRITE_PCT = 0.02         # 요청 중 쓰기 비율
ZIPF_S = 1.1             # 인기 키에 몰리는 정도
LAT_LOCAL, LAT_REMOTE, LAT_STORE = 0.05, 1.0, 10.0   # 가정한 응답 시간(ms)
SCENARIOS = {
    "remote": ("분산 캐시만", None),
    "local1s": ("로컬 TTL 1초 + 분산", 1_000),
    "local10s": ("로컬 TTL 10초 + 분산", 10_000),
}


def run(kind):
    rnd = random.Random(SEED)
    cum = list(accumulate(1 / (i + 1) ** ZIPF_S for i in range(KEYS)))
    total = cum[-1]
    ttl = SCENARIOS[kind][1]
    version = [0] * KEYS                     # 저장소의 최신 버전
    remote = {}                              # 분산 캐시: key -> version (쓸 때 갱신)
    local = [dict() for _ in range(SERVERS)]  # key -> (version, 만료 시각)
    r = dict(reads=0, local=0, remote=0, store=0, stale=0, latency=0.0, writes=0)
    for t in range(REQUESTS):
        k = bisect.bisect_left(cum, rnd.random() * total)
        s = rnd.randrange(SERVERS)
        if rnd.random() < WRITE_PCT:
            r["writes"] += 1
            version[k] += 1
            remote[k] = version[k]
            if ttl:
                local[s][k] = (version[k], t + ttl)   # 쓴 서버의 로컬만 바로 갱신
            continue
        r["reads"] += 1
        hit = local[s].get(k) if ttl else None
        if hit and hit[1] > t:
            ver, where, lat = hit[0], "local", LAT_LOCAL
        elif k in remote:
            ver, where, lat = remote[k], "remote", LAT_REMOTE
        else:
            ver, where, lat = version[k], "store", LAT_STORE
            remote[k] = ver
        if ttl and where != "local":
            local[s][k] = (ver, t + ttl)
        r[where] += 1
        r["latency"] += lat
        if ver != version[k]:
            r["stale"] += 1
    r["avg_ms"] = r["latency"] / r["reads"]
    r["stale_pct"] = r["stale"] / r["reads"] * 100
    r["local_pct"] = r["local"] / r["reads"] * 100
    return r


def regression_tests(res):
    a, b, c = res["remote"], res["local1s"], res["local10s"]
    assert a["avg_ms"] > b["avg_ms"] > c["avg_ms"]
    assert a["stale"] == 0 < b["stale"] < c["stale"]


def experiment():
    res = {k: run(k) for k in SCENARIOS}
    regression_tests(res)
    for k, (name, _) in SCENARIOS.items():
        v = res[k]
        print(f"| {name} | {v['local_pct']:.1f}% | {v['avg_ms']:.3f} | {v['stale']:,} ({v['stale_pct']:.2f}%) |")
    return res


def write_svg(res):
    w, h = 900, 380
    x0, y0, pw, ph = 90, 70, 760, 220
    labels = {"remote": "remote only", "local1s": "local TTL 1 s", "local10s": "local TTL 10 s"}
    colors = {"remote": "#ef4444", "local1s": "#f59e0b", "local10s": "#16a34a"}
    top = max(v["avg_ms"] for v in res.values())
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">Average read latency and stale reads, two-level cache</title>',
         '<desc id="d">' + "; ".join(f'{labels[k]} {v["avg_ms"]:.3f} ms average, {v["stale"]:,} stale reads ({v["stale_pct"]:.2f}%)' for k, v in res.items()) + '.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         '<text x="28" y="40" font-size="22" font-weight="bold">Average read latency (ms) and stale reads</text>',
         f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="#94a3b8"/>']
    gw = pw / 3
    bw = 120
    for i, (k, v) in enumerate(res.items()):
        x = x0 + i * gw + (gw - bw) / 2
        bh = v["avg_ms"] / top * ph
        s.append(f'<rect x="{x:.1f}" y="{y0 + ph - bh:.1f}" width="{bw}" height="{max(bh, 1):.1f}" fill="{colors[k]}"/>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph - bh - 6:.1f}" font-size="13" text-anchor="middle">{v["avg_ms"]:.3f} ms</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 22}" font-size="12" text-anchor="middle">{labels[k]}</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 40}" font-size="12" text-anchor="middle">stale {v["stale"]:,} ({v["stale_pct"]:.2f}%)</text>')
    s.append(f'<text x="28" y="{h - 40}" font-size="13">{REQUESTS:,} requests, {SERVERS} servers, {WRITE_PCT * 100:.0f}% writes; local {LAT_LOCAL} ms, remote {LAT_REMOTE} ms, store {LAT_STORE} ms (assumed).</text>')
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
