"""045. Cache-aside 갱신 경쟁: 늦게 도착한 옛 값이 새 값을 덮어쓰는 건수와 버전 확인 (표준 라이브러리만 사용, 시드 고정 시뮬레이션)."""
import argparse
import platform
import random
from pathlib import Path

SEED = 42
TRIALS = 100_000         # 한 번의 '읽기 미스 + 쓰기' 경쟁 시나리오
TTL_MS = 10_000          # 캐시 항목 TTL. 삭제가 없으면 옛 값은 이 시간까지 남는다
READ_OFFSET_MS = (-6.0, 2.0)  # 읽기 DB 조회 시각 = 쓰기 시각 + 이 범위(음수면 쓰기 전에 읽음)
SET_DELAY_MS = (0.0, 5.0)     # 읽기 쪽이 DB에서 값을 받은 뒤 캐시에 넣기까지 지연(네트워크·GC 가정)
DELETE_DELAY_MS = (0.0, 2.0)  # 쓰기 쪽 DB 갱신 뒤 캐시 삭제까지 지연
DOUBLE_DELETE_MS = 500        # 전략 B: 삭제를 한 번 더 보내는 시각 (ms)
SCENARIOS = {
    "plain": "쓰기 뒤 삭제 (cache-aside 기본형)",
    "double": "쓰기 뒤 삭제, 500 ms 뒤 한 번 더 삭제",
    "versioned": "버전 확인 후 저장 (옛 버전은 거절)",
}


def run(kind):
    rnd = random.Random(SEED)
    landed = 0        # 읽기 쪽 옛 값 저장이 쓰기 쪽 삭제 뒤에 도착한 건수 (경쟁 발생)
    stale = 0         # 경쟁 뒤 캐시에 옛 값이 실제로 남은 건수
    stale_ms = 0.0    # 남은 옛 값이 사라질 때까지 걸린 시간의 합(ms)
    for _ in range(TRIALS):
        b = 0.0                                    # 쓰기(DB 갱신) 시각
        a = b + rnd.uniform(*READ_OFFSET_MS)       # 읽기가 DB에서 값을 읽은 시각
        set_at = a + rnd.uniform(*SET_DELAY_MS)    # 읽기 쪽 캐시 저장 시각
        del_at = b + rnd.uniform(*DELETE_DELAY_MS) # 쓰기 쪽 캐시 삭제 시각
        if a >= b or set_at <= del_at:
            continue                               # 새 값을 읽었거나, 삭제 전에 저장돼 삭제가 치움
        landed += 1
        if kind == "versioned":
            continue                               # 버전이 낮아 저장이 거절됨: 옛 값이 남지 않음
        stale += 1
        if kind == "plain":
            stale_ms += TTL_MS                     # 삭제가 더 없으니 TTL이 끝날 때까지 남음
        else:
            stale_ms += DOUBLE_DELETE_MS - set_at  # 500 ms 뒤 두 번째 삭제가 치움
    return dict(trials=TRIALS, landed=landed, stale=stale,
                stale_avg_ms=(stale_ms / stale) if stale else 0.0)


def regression_tests(res):
    assert res["plain"]["stale"] == res["plain"]["landed"] > 0
    assert res["double"]["stale"] == res["plain"]["stale"]
    assert res["versioned"]["stale"] == 0 and res["versioned"]["landed"] == res["plain"]["landed"]


def experiment():
    res = {k: run(k) for k in SCENARIOS}
    regression_tests(res)
    for k, name in SCENARIOS.items():
        v = res[k]
        pct = v["stale"] / v["trials"] * 100
        print(f"| {name} | {v['landed']:,} | {v['stale']:,} ({pct:.2f}%) | {v['stale_avg_ms']:.0f} |")
    return res


def write_svg(res):
    w, h = 900, 380
    x0, y0, pw, ph = 90, 70, 760, 220
    labels = {"plain": "plain delete", "double": "double delete", "versioned": "versioned set"}
    colors = {"plain": "#ef4444", "double": "#f59e0b", "versioned": "#16a34a"}
    top = max(v["stale"] for v in res.values()) or 1
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">Stale cache entries after a read-miss and write race</title>',
         '<desc id="d">' + "; ".join(f'{labels[k]} {v["stale"]:,} stale of {v["trials"]:,}' for k, v in res.items()) + '.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         '<text x="28" y="40" font-size="22" font-weight="bold">Stale cache entries after a read-miss and write race</text>',
         f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="#94a3b8"/>']
    gw = pw / 3
    bw = 120
    for i, (k, v) in enumerate(res.items()):
        x = x0 + i * gw + (gw - bw) / 2
        bh = v["stale"] / top * ph
        s.append(f'<rect x="{x:.1f}" y="{y0 + ph - bh:.1f}" width="{bw}" height="{max(bh, 1):.1f}" fill="{colors[k]}"/>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph - bh - 6:.1f}" font-size="13" text-anchor="middle">{v["stale"]:,}</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 22}" font-size="12" text-anchor="middle">{labels[k]}</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 40}" font-size="12" text-anchor="middle">of {v["trials"]:,} races</text>')
    s.append(f'<text x="28" y="{h - 40}" font-size="13">read-miss race, delete delay 0-2 ms, set delay 0-5 ms (assumed); double delete at 500 ms.</text>')
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
