"""047. 삭제 재시도 간격: 실패한 캐시 삭제를 몇 ms 뒤에 다시 보내야 옛 값 구간이 줄어드는가 (표준 라이브러리만 사용, 시드 고정 시뮬레이션)."""
import argparse
import platform
import random
from pathlib import Path

SEED = 42
WRITES = 2_000            # 쓰기 이벤트 수(키 하나에 대한 갱신 1회씩)
WINDOW_MS = 1_000         # 쓰기 뒤 관찰 구간(ms)
READ_RATE = 0.5           # 같은 키의 읽기 도착률(건/ms) = 초당 500건
FAIL_P = 0.30             # 삭제 한 번이 실패할 확률(가정)
MEAN_DELAY = 50.0         # 삭제 메시지가 캐시에 닿기까지 평균 지연(ms, 지수 분포, 가정)
MAX_TRIES = 10
RETRY_GAPS = [0, 10, 50, 200, 500]   # 실패 후 다음 시도까지 기다리는 간격(ms). 0 = 바로 재전송


def run(gap):
    rnd = random.Random(SEED)
    total_reads = 0
    stale_reads = 0
    sends = 0
    windows = []
    for _ in range(WRITES):
        send = 0.0
        visible_until = WINDOW_MS          # 성공한 삭제가 없으면 구간 끝까지 옛 값
        for _try in range(MAX_TRIES):
            sends += 1
            arrive = send + rnd.expovariate(1 / MEAN_DELAY)
            if rnd.random() >= FAIL_P:     # 이번 삭제는 캐시에 닿음
                visible_until = min(arrive, WINDOW_MS)
                break
            send = send + gap              # 실패: gap ms 뒤 다시 보냄
        windows.append(visible_until)
    # 읽기 과정은 같은 시드 흐름을 따로 써서 비교 조건을 맞춤
    rr = random.Random(SEED + 1)
    for w in windows:
        t = 0.0
        while True:
            t += rr.expovariate(READ_RATE)
            if t >= WINDOW_MS:
                break
            total_reads += 1
            if t < w:
                stale_reads += 1
    avg_window = sum(windows) / len(windows)
    return dict(reads=total_reads, stale=stale_reads,
                ratio=stale_reads / total_reads * 100,
                avg_window=avg_window, sends=sends / WRITES)


def regression_tests(res):
    assert res[0]["ratio"] > 0
    assert res[0]["ratio"] <= res[10]["ratio"] <= res[50]["ratio"] <= res[200]["ratio"] <= res[500]["ratio"]
    assert res[500]["sends"] == res[0]["sends"]  # 간격이 바뀌어도 보내는 횟수는 같다
    assert all(v["ratio"] <= 100 for v in res.values())


def experiment():
    res = {g: run(g) for g in RETRY_GAPS}
    regression_tests(res)
    print("| 재시도 간격(ms) | 읽기 수 | 옛 값 읽기 | 옛 값이 보인 평균 시간(ms) | 쓰기당 삭제 요청 |")
    print("|---:|---:|---:|---:|---:|")
    for g in RETRY_GAPS:
        v = res[g]
        print(f"| {g} | {v['reads']:,} | {v['stale']:,} ({v['ratio']:.2f}%) | {v['avg_window']:.1f} | {v['sends']:.3f} |")
    return res


def write_svg(res):
    w, h = 900, 380
    x0, y0, pw, ph = 90, 70, 760, 220
    top = max(v["ratio"] for v in res.values()) or 1
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">Stale read ratio by retry gap</title>',
         '<desc id="d">' + "; ".join(f'gap {g} ms {v["ratio"]:.2f}% stale' for g, v in res.items()) + '.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         '<text x="28" y="40" font-size="22" font-weight="bold">Stale read ratio by delete retry gap</text>',
         f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="#94a3b8"/>']
    gw = pw / len(res)
    bw = 90
    for i, (g, v) in enumerate(res.items()):
        x = x0 + i * gw + (gw - bw) / 2
        bh = v["ratio"] / top * ph
        s.append(f'<rect x="{x:.1f}" y="{y0 + ph - bh:.1f}" width="{bw}" height="{max(bh, 1):.1f}" fill="#2563eb"/>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph - bh - 6:.1f}" font-size="13" text-anchor="middle">{v["ratio"]:.2f}%</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 22}" font-size="12" text-anchor="middle">gap {g} ms</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 40}" font-size="12" text-anchor="middle">{v["stale"]:,} of {v["reads"]:,}</text>')
    s.append(f'<text x="28" y="{h - 40}" font-size="13">Delete fails 30% per try, mean delay 50 ms (assumed), 1 s window, 500 reads/s.</text>')
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
