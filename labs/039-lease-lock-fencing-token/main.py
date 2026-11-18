"""039. 분산 락의 임대 시간(lease) 초과와 fencing token (표준 라이브러리만 사용, 이벤트 시뮬레이션)."""
import argparse
import heapq
import platform
import random
from pathlib import Path

SEED = 39
JOBS = 10_000        # 카운터를 1씩 올리는 작업 수
CLIENTS = 8          # 같은 자원을 다루는 작업자 수
LEASE_MS = 30        # 락 임대 시간
WORK_MS = (5, 15)    # 보통 작업 시간(읽기~쓰기)
PAUSE_P = 0.02       # GC·네트워크 지연으로 멈출 확률
PAUSE_MS = (40, 120) # 멈추는 시간(임대 시간보다 김)
SCENARIOS = {"nolock": "락 없음", "lease": "임대 락만", "fencing": "임대 락 + fencing token"}


def run(kind):
    rnd = random.Random(SEED)
    value = 0
    max_token = 0
    applied = lost = rejected = overlap = 0
    pending = JOBS
    ev = []  # (time, seq, type, payload)
    seq = 0

    def push(t, typ, p):
        nonlocal seq
        seq += 1
        heapq.heappush(ev, (t, seq, typ, p))

    def duration():
        d = rnd.uniform(*WORK_MS)
        if rnd.random() < PAUSE_P:
            d += rnd.uniform(*PAUSE_MS)
        return d

    holder = None   # (client, token, expires)
    token = 0
    queue = list(range(CLIENTS))
    holders_alive = set()  # 자기가 락을 쥐었다고 믿고 쓰기 전인 작업자

    def grant(t):
        nonlocal holder, token, pending, overlap
        while queue and pending > 0 and (holder is None or holder[2] <= t):
            c = queue.pop(0)
            pending -= 1
            token += 1
            holder = (c, token, t + LEASE_MS)
            if holders_alive:
                overlap += 1
            holders_alive.add((c, token))
            push(t, "read", (c, token))
            push(t + LEASE_MS, "expire", token)
            return

    if kind == "nolock":
        for c in range(CLIENTS):
            push(0, "start", c)
    else:
        grant(0)
    reads = {}
    while ev:
        t, _, typ, p = heapq.heappop(ev)
        if typ == "start":
            if pending > 0:
                pending -= 1
                reads[p] = value
                push(t + duration(), "nwrite", p)
        elif typ == "nwrite":
            if value != reads[p]:
                lost += 1
            value = reads[p] + 1
            applied += 1
            push(t, "start", p)
        elif typ == "read":
            c, k = p
            reads[(c, k)] = value
            push(t + duration(), "write", p)
        elif typ == "expire":
            if holder and holder[1] == p:
                holder = (holder[0], holder[1], t)
                grant(t)
        elif typ == "write":
            c, k = p
            holders_alive.discard((c, k))
            if kind == "fencing" and k < max_token:
                rejected += 1
                pending += 1            # 거절된 작업은 다시 줄을 선다
            else:
                if value != reads[(c, k)]:
                    lost += 1
                value = reads[(c, k)] + 1
                max_token = max(max_token, k)
                applied += 1
            if holder and holder[1] == k:
                holder = None
            queue.append(c)
            grant(t)
    return {"final": value, "applied": applied, "lost": JOBS - value, "overwrites": lost, "rejected": rejected, "overlap": overlap, "ms": t}


def regression_tests(res):
    n = res["nolock"]
    assert n["applied"] == JOBS and n["final"] < JOBS * 0.3
    l = res["lease"]
    assert l["applied"] == JOBS and l["overlap"] > 0 and l["lost"] > 0 and l["overwrites"] > 0
    f = res["fencing"]
    assert f["final"] == JOBS and f["lost"] == 0 and f["rejected"] > 0 and f["overlap"] > 0


def experiment():
    res = {k: run(k) for k in SCENARIOS}
    regression_tests(res)
    for k, name in SCENARIOS.items():
        v = res[k]
        print(f"| {name} | {JOBS:,} | {v['final']:,} | {v['lost']:,} | {v['overlap']:,} | {v['rejected']:,} | {v['ms'] / 1000:.1f} |")
    return res


def write_svg(res):
    w, h = 900, 380
    x0, y0, pw, ph = 90, 70, 760, 220
    labels = {"nolock": "no lock", "lease": "lease lock only", "fencing": "lease lock + fencing token"}
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">Final counter after 10,000 increments under a lease lock</title>',
         f'<desc id="d">no lock {res["nolock"]["final"]:,}; lease only {res["lease"]["final"]:,}; with fencing token {res["fencing"]["final"]:,}.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         f'<text x="28" y="40" font-size="22" font-weight="bold">Final counter after {JOBS:,} increments (lease {LEASE_MS} ms, {CLIENTS} clients)</text>',
         f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="#94a3b8"/>']
    gw = pw / 3
    bw = 120
    colors = {"nolock": "#94a3b8", "lease": "#f59e0b", "fencing": "#2563eb"}
    for i, (k, v) in enumerate(res.items()):
        x = x0 + i * gw + (gw - bw) / 2
        bh = v["final"] / JOBS * ph
        s.append(f'<rect x="{x:.1f}" y="{y0 + ph - bh:.1f}" width="{bw}" height="{bh:.1f}" fill="{colors[k]}"/>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph - bh - 6:.1f}" font-size="13" text-anchor="middle">{v["final"]:,} (lost {v["lost"]:,})</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 22}" font-size="13" text-anchor="middle">{labels[k]}</text>')
    f = res["fencing"]
    s.append(f'<text x="28" y="{h - 40}" font-size="13">Fencing token: {f["rejected"]:,} stale writes rejected and retried. Lease-only: {res["lease"]["overlap"]:,} times two clients held the lock.</text>')
    s.append(f'<text x="28" y="{h - 14}" font-size="13">Python {platform.python_version()}, simulated time, seed {SEED}.</text></g></svg>')
    Path(__file__).with_name("result.svg").write_text("\n".join(s) + "\n", encoding="utf-8")
    print("wrote result.svg")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-svg", action="store_true")
    a = ap.parse_args()
    if not __debug__:
        raise SystemExit("Run without -O")
    print(f"Python {platform.python_version()} | {platform.system()} {platform.machine()} | seed {SEED}")
    print("| 방법 | 작업 수 | 최종 카운터 | 덮어써 잃은 수 | 옛 작업자가 아직 쓰기 전에 락을 새로 내준 횟수 | 거절된 쓰기 | 걸린 시간(시뮬레이션 초) |")
    res = experiment()
    if a.write_svg:
        write_svg(res)
