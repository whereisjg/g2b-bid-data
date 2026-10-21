"""과거 개찰결과로 사정률 위치별 기대 승률 계산

각 과거 공고 h에 대해: 업체별 투찰금액 → 사정률 위치(그 업체가 정확히 하한가가 되는 사정률)로 환산.
A값·하한율은 공고정보(notices.csv). A값이 없으면 1순위 금액으로 역산 (1순위 ≈ 하한가).
검증: 실제 예정가격으로 계산한 하한가 이상 최저가 업체 == 실제 1순위(opengRank=1)인 공고만 verified.
위치 S로 투찰 시 승리 = 실제 사정률 ∈ (S 바로 아래 경쟁자, S].
실제 사정률 분포는 복수예가(15개 중 4개 평균) 시뮬레이션. 같은 그룹 과거 건 평균 = 기대 승률.
"""
import csv, glob, random, bisect
from collections import defaultdict

import os
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

NOTICES = {}

def load():
    bids, prices, bidders = {}, defaultdict(list), defaultdict(list)
    for p in glob.glob(f"{DATA}/*/notices.csv"):
        for r in csv.DictReader(open(p, encoding="utf-8-sig")):
            try: rate = float(r.get("sucsfbidLwltRate") or "") / 100
            except ValueError: rate = None   # "-" = 하한율 없음, "" = 미조회
            NOTICES[r["bidNtceNo"]] = dict(A=float(r["A"]) if r.get("A") not in ("", None) else None, rate=rate)
    for p in glob.glob(f"{DATA}/*/bids.csv"):
        for r in csv.DictReader(open(p, encoding="utf-8-sig")): bids[r["bidNtceNo"]] = r
    for p in glob.glob(f"{DATA}/*/prices.csv"):
        for r in csv.DictReader(open(p, encoding="utf-8-sig")): prices[r["bidNtceNo"]].append(r)
    for p in glob.glob(f"{DATA}/*/bidders.csv"):
        for r in csv.DictReader(open(p, encoding="utf-8-sig")): bidders[r["bidNtceNo"]].append(r)
    return bids, prices, bidders

_sims = {}
PRICE_SETS = {2: [], 3: []}   # 과거 공고의 실제 복수예비가격 15개 (기초금액 대비 %)

def set_price_sets(prices):
    for rs in prices.values():
        if len(rs) != 15: continue
        Bv = float(rs[0]["bssamt"] or 0)
        if not Bv: continue
        d = [(float(r["bsisPlnprc"]) / Bv - 1) * 100 for r in rs]
        PRICE_SETS[2 if max(map(abs, d)) <= 2.05 else 3].append(d)
    _sims.clear()

def sims(rng):
    """실제 사정률 분포: 과거 복수예가 세트를 무작위로 골라 4개 추첨 평균 (생성 방식을 그대로 반영)"""
    if rng not in _sims:
        random.seed(1)
        ps = PRICE_SETS.get(rng) or []
        if len(ps) >= 30:
            s = sorted(100 + sum(random.sample(random.choice(ps), 4)) / 4 for _ in range(200000))
        else:
            s = sorted(100 + sum(random.sample([random.uniform(-rng, rng) for _ in range(15)], 4)) / 4 for _ in range(200000))
        _sims[rng] = s
    return _sims[rng]

def cdf(rng, x):
    s = sims(rng); return bisect.bisect_right(s, x) / len(s)

def positions(no, prices, bidders):
    pr = prices.get(no); bd = [b for b in bidders.get(no, []) if b.get("bidprcAmt")]
    if not pr or len(bd) < 5: return None
    B = float(pr[0]["bssamt"] or 0); E = float(pr[0]["plnprc"] or 0)
    if not B or not E: return None
    dev = max(abs(float(x["bsisPlnprc"]) / B - 1) for x in pr if x.get("bsisPlnprc")) * 100
    rng = 2 if dev <= 2.05 else 3
    nt = NOTICES.get(no) or {}
    R = nt.get("rate")
    if not R: return None
    w = [b for b in bd if b["opengRank"] == "1"]
    A = nt.get("A")
    if A is None:
        if not w: return None
        A = (float(w[0]["bidprcAmt"]) - E * R) / (1 - R)   # 1순위 역산 (근사)
    if not (0 <= A < 0.2 * B): return None
    pos = sorted(((float(b["bidprcAmt"]) - A) / R + A) / B * 100 for b in bd)
    # 계산상 1순위: 하한가 이상·예정가격 이하 최저가 (1원 반올림 오차 허용)
    fl = (E - A) * R + A
    ok = [b for b in bd if fl - 1 <= float(b["bidprcAmt"]) <= E and "취소" not in b.get("rmrk", "")]
    calc = min(ok, key=lambda b: float(b["bidprcAmt"]))["prcbdrBizno"] if ok else None
    verified = nt.get("A") is not None and bool(w) and calc == w[0]["prcbdrBizno"]
    return dict(rng=rng, pos=pos, n=len(bd), actual=E / B * 100, A=A / B, R=R,
                trueA=nt.get("A") is not None, verified=verified)

def win(p, S):
    i = bisect.bisect_left(p["pos"], S)
    lo = p["pos"][i - 1] if i else 90
    return max(0.0, cdf(p["rng"], S) - cdf(p["rng"], lo))

def group_curve(nos, prices, bidders, grid):
    ps = [x for x in (positions(n, prices, bidders) for n in nos) if x]
    if not ps: return [], []
    out = []
    for S in grid:
        rel = sum(win(p, S) * p["n"] for p in ps) / len(ps)   # 평균 대비 배수 (1 = 무작위 평균)
        out.append((S, sum(win(p, S) for p in ps) / len(ps), rel))
    return ps, out

def load_nation():
    """전국 전기공사 요약(collect_nation.py) → [(positions()와 같은 형식, 개찰일)], 복수예가 dict"""
    import gzip
    N = os.path.join(DATA, "nation")
    pos = {}
    for p in sorted(glob.glob(f"{N}/pos/*.csv.gz")):
        for r in csv.DictReader(gzip.open(p, "rt", encoding="utf-8")):
            pos[r["bidNtceNo"]] = [100 + float(x) for x in r["pos"].split()]
    prices, out = defaultdict(list), []
    for p in glob.glob(f"{N}/*/prices.csv"):
        for r in csv.DictReader(open(p, encoding="utf-8-sig")): prices[r["bidNtceNo"]].append(r)
    for p in glob.glob(f"{N}/*/bids.csv"):
        for r in csv.DictReader(open(p, encoding="utf-8-sig")):
            ps = pos.get(r["bidNtceNo"])
            if not ps or not r.get("rng"): continue
            out.append((dict(rng=int(r["rng"]), pos=ps, n=len(ps), actual=float(r["actual"]), A=float(r["Aratio"]), R=float(r["R"]),
                             trueA=r["trueA"] == "1", verified=r["verified"] == "1"), r.get("opengDt", "")))
    return out, prices
