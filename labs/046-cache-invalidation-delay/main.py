"""046. 캐시 무효화 지연: 삭제 메시지가 늦게 도착하는 동안 읽히는 옛 값 비율 (표준 라이브러리만 사용, 시드 고정 시뮬레이션)."""
import argparse
import math
import platform
import random
from pathlib import Path

SEED = 42
WRITES = 2_000            # 쓰기 이벤트 수(키 하나에 대한 갱신 1회씩)
WINDOW_MS = 1_000         # 쓰기 뒤 관찰 구간(ms). 이 구간의 읽기를 분모로 씀
READ_RATE = 0.5           # 같은 키의 읽기 도착률(건/ms) = 초당 500건
SCENARIOS = {
    "sync": "동기 삭제 (쓰기 안에서 바로 삭제)",
    "queue": "비동기 삭제 큐 (지연 평균 50 ms, 지수 분포)",
    "queue_tail": "비동기 삭제 큐 + 꼬리 지연 (5%는 300~1,500 ms)",
}


def delete_delay(kind, rnd):
    if kind == "sync":
        return 0.0
    if kind == "queue":
        return rnd.expovariate(1 / 50.0)
    # queue_tail: 95%는 평균 20 ms, 5%는 300~1,500 ms 사이로 늦게 도착
    if rnd.random() < 0.95:
        return rnd.expovariate(1 / 20.0)
    return rnd.uniform(300.0, 1_500.0)


def run(kind):
    rnd = random.Random(SEED)
    total_reads = 0
    stale_reads = 0
    stale_windows = []     # 쓰기 뒤 옛 값이 실제로 보인 시간(ms)
    for _ in range(WRITES):
        d = delete_delay(kind, rnd)            # 삭제가 캐시에 닿는 시각(쓰기 시각 = 0)
        visible_until = min(d, WINDOW_MS)      # 이 시각 전까지 캐시에는 옛 값이 남아 있음
        t = 0.0
        while True:                            # 구간 안의 읽기를 포아송 과정으로 뽑음
            t += rnd.expovariate(READ_RATE)
            if t >= WINDOW_MS:
                break
            total_reads += 1
            if t < visible_until:
                stale_reads += 1
        stale_windows.append(visible_until)
    avg_window = sum(stale_windows) / len(stale_windows)
    return dict(writes=WRITES, reads=total_reads, stale=stale_reads,
                ratio=stale_reads / total_reads * 100, avg_window=avg_window)


def regression_tests(res):
    assert res["sync"]["stale"] == 0
    assert 0 < res["queue"]["ratio"] < res["queue_tail"]["ratio"]
    assert res["queue_tail"]["avg_window"] > res["queue"]["avg_window"] > 0


def experiment():
    res = {k: run(k) for k in SCENARIOS}
    regression_tests(res)
    for k, name in SCENARIOS.items():
        v = res[k]
        print(f"| {name} | {v['reads']:,} | {v['stale']:,} ({v['ratio']:.2f}%) | {v['avg_window']:.1f} |")
    return res


def write_svg(res):
    w, h = 900, 380
    x0, y0, pw, ph = 90, 70, 760, 220
    labels = {"sync": "sync delete", "queue": "queue delete", "queue_tail": "queue + tail"}
    colors = {"sync": "#16a34a", "queue": "#f59e0b", "queue_tail": "#ef4444"}
    top = max(v["ratio"] for v in res.values()) or 1
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">Stale read ratio during delete delay</title>',
         '<desc id="d">' + "; ".join(f'{labels[k]} {v["ratio"]:.2f}% stale reads' for k, v in res.items()) + '.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         '<text x="28" y="40" font-size="22" font-weight="bold">Stale read ratio during delete delay</text>',
         f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="#94a3b8"/>']
    gw = pw / 3
    bw = 120
    for i, (k, v) in enumerate(res.items()):
        x = x0 + i * gw + (gw - bw) / 2
        bh = v["ratio"] / top * ph
        s.append(f'<rect x="{x:.1f}" y="{y0 + ph - bh:.1f}" width="{bw}" height="{max(bh, 1):.1f}" fill="{colors[k]}"/>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph - bh - 6:.1f}" font-size="13" text-anchor="middle">{v["ratio"]:.2f}%</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 22}" font-size="12" text-anchor="middle">{labels[k]}</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 40}" font-size="12" text-anchor="middle">{v["stale"]:,} of {v["reads"]:,} reads</text>')
    s.append(f'<text x="28" y="{h - 40}" font-size="13">1 s after each write, read rate 500/s, {WRITES:,} writes (delay model is an assumption).</text>')
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
