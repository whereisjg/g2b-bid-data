"""전국 전기공사(업종코드 0037 = 전기공사업) 개찰결과 수집 → 분석용 요약만 저장 (data/nation/)

전국은 1주 약 330건·참가업체 약 25만 줄이라 업체별 원본은 저장하지 않고, 공고마다
업체 위치(사정률 %, analysis/position.py와 같은 계산)만 압축해 둔다. 강원 수집분(data/YYYY-MM/)과 겹치는 공고는 건너뜀.
  data/nation/YYYY-MM/bids.csv     공고 목록 원문 + 요약(B·A·하한율·예가범위·실제 사정률·1순위 검증)
  data/nation/YYYY-MM/prices.csv   복수예가 15개 (실제 사정률 분포용)
  data/nation/pos/YYYYMMDD_HHMMSS_구간.csv.gz  공고별 업체 위치 목록 (구간마다 새 파일 — git 이력 부담 줄임)
매 실행: 최근 3일 + 과거를 1주씩 거꾸로 (data/nation/cursor.txt, 하한 2024-01-01). 공고 4건 동시 조회.
"""
import csv, gzip, os, sys, time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
import collect as C
sys.path.insert(0, str(Path(__file__).parent / "analysis"))
import position as M

NATION = C.DATA / "nation"
CURSOR = NATION / "cursor.txt"
AD = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
PRICE_COLS = ["bidNtceNo", "bidNtceOrd", "bssamt", "plnprc", "totRsrvtnPrceNum", "compnoRsrvtnPrceSno", "bsisPlnprc", "drwtYn"]
WORKERS = 4
A_FIELDS = ("npnInsrprm", "mrfnHealthInsrprm", "rtrfundNon", "odsnLngtrmrcprInsrprm", "sftyMngcst", "sftyChckMngcst", "qltyMngcst")   # enrich.py와 같음


def have_keys():
    s = C.done_keys()
    for p in NATION.glob("*/bids.csv"):
        s |= {(r["bidNtceNo"], r["bidNtceOrd"]) for r in csv.DictReader(open(p, encoding="utf-8-sig"))}
    return s


def pick(items, od):
    return [x for x in items if x.get("bidNtceOrd", od) == od] or items


def one(b):
    """공고 1건: 참가업체·복수예가·A값·하한율 조회 → 위치 요약. 참가업체나 예가가 없으면 None (유찰·미등록)"""
    no, od = b["bidNtceNo"], b.get("bidNtceOrd", "000")
    comp = C.call("getOpengResultListInfoOpengCompt", inqryDiv=2, bidNtceNo=no, bidNtceOrd=od)
    prices = C.call("getOpengResultListInfoCnstwkPreparPcDetail", inqryDiv=2, bidNtceNo=no, bidNtceOrd=od)
    if not comp or not prices: return None
    bs = pick(C.call("getBidPblancListInfoCnstwkBsisAmount", base=AD, numOfRows=10, inqryDiv=2, bidNtceNo=no), od)
    ls = pick(C.call("getBidPblancListInfoCnstwk", base=AD, numOfRows=10, inqryDiv=2, bidNtceNo=no, bidNtceOrd=od), od)
    A = None
    if bs:
        x = bs[0]
        A = sum(int(float(x.get(f) or 0)) for f in A_FIELDS) if x.get("bidPrceCalclAYn", "Y") != "N" else 0
    rate = (ls[0].get("sucsfbidLwltRate") if ls else "") or "-"
    for r in comp + prices:
        r.setdefault("bidNtceNo", no); r.setdefault("bidNtceOrd", od)
    return b, comp, prices, A, rate


def summarize(b, comp, prices, A, rate):
    """position.positions()와 같은 계산으로 위치 요약 (분석은 이 값만 사용)"""
    no = b["bidNtceNo"]
    try: R = float(rate) / 100
    except ValueError: R = None
    M.NOTICES[no] = dict(A=A, rate=R)
    p = M.positions(no, {no: prices}, {no: comp})
    M.NOTICES.pop(no, None)
    row = {**b, "A": "" if A is None else A, "sucsfbidLwltRate": rate, "B": prices[0].get("bssamt", ""), "nBid": len(comp)}
    if p:
        row.update(rng=p["rng"], actual=round(p["actual"], 4), verified=int(p["verified"]), trueA=int(p["trueA"]), Aratio=round(p["A"], 6), R=p["R"])
    return row, (" ".join(f"{x - 100:.3f}" for x in p["pos"]) if p else None)


def save_pos(rows, tag):
    """공고별 위치 목록을 구간마다 바로 파일로 (중간에 끊겨도 bids.csv에 저장된 공고의 위치가 사라지지 않게)"""
    if not rows: return
    d = NATION / "pos"; d.mkdir(exist_ok=True)
    with gzip.open(d / f"{datetime.now(C.KST):%Y%m%d_%H%M%S}_{tag}.csv.gz", "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["bidNtceNo", "bidNtceOrd", "pos"]); w.writerows(rows)


def window(start, end, label, have, pos_rows):
    rows = C.call("getOpengResultListInfoCnstwkPPSSrch", inqryDiv=2, indstrytyCd="0037",
                  inqryBgnDt=start.strftime("%Y%m%d0000"), inqryEndDt=end.strftime("%Y%m%d2359"))
    todo = [b for b in rows if (b.get("bidNtceNo"), b.get("bidNtceOrd", "000")) not in have]
    out, stop = [], None
    with ThreadPoolExecutor(WORKERS) as ex:
        fs = [ex.submit(one, b) for b in todo]
        for f in as_completed(fs):
            try:
                r = f.result()
                if r: out.append(r)
            except CancelledError:   # 마감 후 취소한 대기 작업
                continue
            except C.Budget as e:
                stop = stop or e
            except RuntimeError as e:   # 한 건 오류는 건너뜀 (지난 구간은 다시 조회하지 않음)
                print("오류:", str(e)[:120])
            if stop:
                for g in fs: g.cancel()
    by_m, mine = {}, []
    for b, comp, prices, A, rate in out:
        m = (b.get("opengDt") or start.strftime("%Y-%m"))[:7]
        row, pos = summarize(b, comp, prices, A, rate)
        d = by_m.setdefault(m, dict(bids=[], prices=[]))
        d["bids"].append(row)
        d["prices"] += [{k: x.get(k, "") for k in PRICE_COLS} for x in prices]
        if pos: mine.append((b["bidNtceNo"], b.get("bidNtceOrd", "000"), pos))
        have.add((b["bidNtceNo"], b.get("bidNtceOrd", "000")))
    save_pos(mine, f"{start:%Y%m%d}"); pos_rows += mine   # 위치를 bids.csv보다 먼저 저장
    for m, d in by_m.items():
        sub = f"nation/{m}"
        C.save(sub, "prices", d["prices"], ["bidNtceNo", "bidNtceOrd", "compnoRsrvtnPrceSno"])
        C.save(sub, "bids", d["bids"], ["bidNtceNo", "bidNtceOrd"])
    od = sorted(b.get("opengDt", "")[:10] for b in rows)
    print(f"[{label}] {start:%Y-%m-%d}~{end:%Y-%m-%d} (개찰일 {od[0] if od else '-'}~{od[-1] if od else '-'}): 목록 {len(rows)} · 신규 {len(todo)} → 저장 {len(out)} (누적 호출 {C.calls})")
    if stop: raise stop


def main():
    if not C.KEY: sys.exit("DATA_GO_KR_KEY 미설정")
    NATION.mkdir(parents=True, exist_ok=True)
    have, pos_rows = have_keys(), []
    today = datetime.now()
    try:
        window(today - timedelta(days=3), today, "일일", have, pos_rows)
        cur = datetime.strptime(CURSOR.read_text().strip(), "%Y-%m-%d") if CURSOR.exists() else today - timedelta(days=3)
        while cur > C.BACKFILL_UNTIL:
            s = max(cur - timedelta(days=7), C.BACKFILL_UNTIL)
            window(s, cur - timedelta(days=1), "과거", have, pos_rows)
            cur = s
            CURSOR.write_text(cur.strftime("%Y-%m-%d"))
        print("과거 채우기 완료")
    except C.Budget:
        print(f"마감 시각 도달 ({C.calls}회). 다음 실행에서 이어서." if time.time() >= C.STOP_AT else f"호출 한도 도달 ({C.calls}회). 다음 실행에서 이어서.")
    except RuntimeError as e:
        print("오류:", e)
    print(f"총 호출 {C.calls}회 / 위치 저장 {len(pos_rows)}건 / 과거 진행 위치: {CURSOR.read_text().strip() if CURSOR.exists() else '-'}")


if __name__ == "__main__":
    main()
