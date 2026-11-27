"""048. 캐시 키 버전 접두어: 한 번에 무효화할 때 삭제 요청 수와 옛 키가 남는 양 (표준 라이브러리만 사용, 시드 고정 시뮬레이션)."""
import argparse
import platform
import random
from pathlib import Path

SEED = 42
KEYS = 200                # 한 묶음(예: 한 가게의 메뉴 캐시 키 200개)
WINDOW_MS = 1_000         # 무효화 뒤 관찰 구간(ms)
READ_RATE = 0.5           # 키 하나당 읽기 도착률(건/ms) = 초당 500건
FAIL_P = 0.30             # 삭제(또는 버전 올리기) 한 번이 실패할 확률(가정)
MEAN_DELAY = 50.0         # 메시지가 캐시에 닿기까지 평균 지연(ms, 지수 분포, 가정)
MAX_TRIES = 10
RETRY_GAP = 0             # 실패 뒤 바로 재전송(047 결과 중 옛 값이 가장 짧은 설정)


def send_until_ok(rnd):
    """메시지 하나를 성공할 때까지 보낸다. (성공 시각, 보낸 횟수)를 돌려준다. 끝까지 실패하면 None."""
    send, tries = 0.0, 0
    for _ in range(MAX_TRIES):
        tries += 1
        arrive = send + rnd.expovariate(1 / MEAN_DELAY)
        if rnd.random() >= FAIL_P:
            return arrive, tries
        send += RETRY_GAP
    return None, tries


def read_stale(windows, seed):
    """키마다 구간 안 읽기를 세서 옛 값을 읽은 수를 센다. windows[i] = 키 i가 옛 값을 보이는 끝 시각(ms)."""
    rr = random.Random(seed)
    total = stale = keys_hit = 0
    for w in windows:
        t, hit = 0.0, False
        while True:
            t += rr.expovariate(READ_RATE)
            if t >= WINDOW_MS:
                break
            total += 1
            if t < w:
                stale += 1
                hit = True
        keys_hit += hit
    return total, stale, keys_hit


def per_key(gap=RETRY_GAP):
    """전략 A: 키마다 삭제를 따로 보낸다. 실패하면 그 키만 다시 보낸다."""
    rnd = random.Random(SEED)
    windows, requests, never = [], 0, 0
    for _ in range(KEYS):
        arrive, tries = send_until_ok(rnd)
        requests += tries
        if arrive is None:
            never += 1
            windows.append(WINDOW_MS)
        else:
            windows.append(min(arrive, WINDOW_MS))
    total, stale, keys_hit = read_stale(windows, SEED + 1)
    return dict(requests=requests, reads=total, stale=stale, keys_hit=keys_hit,
                old_keys_left=0, never=never)


def version_bump():
    """전략 B: 키 묶음의 버전 번호 하나만 올린다. 새 키 이름은 새 버전으로 읽히고, 옛 키는 TTL까지 남는다."""
    rnd = random.Random(SEED)
    arrive, tries = send_until_ok(rnd)
    w = WINDOW_MS if arrive is None else min(arrive, WINDOW_MS)
    windows = [w] * KEYS          # 버전이 바뀌기 전까지 모든 키가 옛 버전을 읽는다
    total, stale, keys_hit = read_stale(windows, SEED + 1)
    # 버전이 바뀐 뒤 새 버전 키는 없으니 DB에서 한 번 채운다(옛 값 아님). 옛 키는 KEYS개 남는다.
    return dict(requests=tries, reads=total, stale=stale, keys_hit=keys_hit,
                old_keys_left=KEYS if arrive is not None else 0, never=int(arrive is None))


def regression_tests(a, b):
    assert a["requests"] >= KEYS                       # 키마다 최소 한 번씩 보낸다
    assert b["requests"] < a["requests"]               # 버전 올리기는 요청 한 묶음
    assert b["old_keys_left"] == KEYS and a["old_keys_left"] == 0
    assert 0 < a["stale"] / a["reads"] < 1 and 0 < b["stale"] / b["reads"] < 1


def experiment():
    a, b = per_key(), version_bump()
    regression_tests(a, b)
    print("| 방식 | 삭제·갱신 요청 수 | 옛 값 읽기 | 옛 값을 읽은 키 | 무효화 뒤 남는 옛 키 |")
    print("|---|---:|---:|---:|---:|")
    for name, v in (("키마다 삭제", a), ("버전 접두어 올리기", b)):
        print(f"| {name} | {v['requests']:,} | {v['stale']:,} of {v['reads']:,} ({v['stale'] / v['reads'] * 100:.2f}%) | {v['keys_hit']} of {KEYS} | {v['old_keys_left']} |")
    return {"per_key": a, "version": b}


def write_svg(res):
    w, h = 900, 380
    x0, y0, pw, ph = 90, 70, 760, 220
    rows = [("키마다 삭제", res["per_key"]), ("버전 접두어 올리기", res["version"])]
    top = max(v["requests"] for _, v in rows)
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">Requests and leftover old keys per invalidation strategy</title>',
         '<desc id="d">' + "; ".join(f'{n}: {v["requests"]} requests, {v["stale"]} stale reads, {v["old_keys_left"]} old keys left' for n, v in rows) + '.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         '<text x="28" y="40" font-size="22" font-weight="bold">Invalidation cost: key-by-key delete vs version prefix</text>',
         f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="#94a3b8"/>']
    gw = pw / len(rows)
    bw = 150
    for i, (name, v) in enumerate(rows):
        x = x0 + i * gw + (gw - bw) / 2
        bh = v["requests"] / top * ph
        s.append(f'<rect x="{x:.1f}" y="{y0 + ph - bh:.1f}" width="{bw}" height="{bh:.1f}" fill="#2563eb"/>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph - bh - 6:.1f}" font-size="13" text-anchor="middle">{v["requests"]:,} requests</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 22}" font-size="12" text-anchor="middle">{name}</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 40}" font-size="12" text-anchor="middle">old keys left: {v["old_keys_left"]}</text>')
    s.append(f'<text x="28" y="{h - 40}" font-size="13">{KEYS} keys, delete/bump fails 30% per try, mean delay 50 ms (assumed), retry at once.</text>')
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
