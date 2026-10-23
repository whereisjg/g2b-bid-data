"""국방전자조달 시설공사 개찰결과 수집 → 나라장터와 같은 형식으로 data/YYYY-MM/{bids,bidders,prices,notices}.csv
대상: 전기공사업 면허 + 지역제한 강원 또는 제한없음 (강원 업체가 참가 가능한 공고)
매 실행: 최근 3일 + 과거를 1주씩 거꾸로 (data/d2b_cursor.txt, 하한 BACKFILL_UNTIL). 하루 LIMIT 회.
이미 확인한 공고(대상 아님 포함)는 data/d2b_seen.txt에 기록해 재조회하지 않음.
"""
import os, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import d2b
from collect import save

DATA = Path(__file__).parent / "data"
CURSOR = DATA / "d2b_cursor.txt"
SEEN = DATA / "d2b_seen.txt"
from collect import BACKFILL_UNTIL   # 최근 365일만
LIMIT = int(os.environ.get("CALL_LIMIT") or 1500)   # 운영계정(2026-10-21). 실행 시간 기준

STOP_AT = float(os.environ.get("STOP_AT") or "inf")   # 이 시각(epoch)이 지나면 중단 (추가 수집용)
KST = timezone(timedelta(hours=9))
RECHECK_DAYS = 3   # 개찰 후 결과가 아직 없으면 이 기간 동안은 다음 실행에서 다시 확인


class Budget(Exception):
    pass


def collect(start, end, label, seen):
    n = [0, 0]
    if time.time() >= STOP_AT: raise Budget()   # 목록 조회 전에도 확인 (확인 완료 공고만 있는 주는 루프 안 확인에 안 걸림)
    today = datetime.now(KST).strftime("%Y%m%d")
    for x in d2b.notices(start.strftime("%Y%m%d"), end.strftime("%Y%m%d")):
        no, od = d2b.ntce_no(x)
        if f"{no}|{od}" in seen or x["opengDt"][:8] > today: continue
        if d2b.calls > LIMIT - 8 or time.time() >= STOP_AT: raise Budget()
        d = d2b.detail(x)
        if not d or not d2b.eligible(d):
            seen.add(f"{no}|{od}"); continue
        key = dict(cntrwkNo=x["cntrwkNo"], orntCode=x["orntCode"], ntatPlanDate=x["opengDt"][:8])
        bs = d2b.get(d2b.RS, "getFcltyCmpetBidResultBsicList", **key)
        mn = d2b.get(d2b.RS, "getFcltyCmpetBidResultMnufList", **key) if bs else []
        if not bs or not mn:   # 유찰이거나 결과 미등록 → 개찰 후 RECHECK_DAYS일 지나야 확인 완료로 기록
            if x["opengDt"][:8] <= (datetime.now(KST) - timedelta(days=RECHECK_DAYS)).strftime("%Y%m%d"): seen.add(f"{no}|{od}")
            continue
        seen.add(f"{no}|{od}")
        Y = [float(b["planPrce"]) for b in bs if b.get("choiYsno") == "Y"]
        E = round(sum(Y) / len(Y)) if Y else ""
        B = int(float(x.get("baseAmnt") or 0))
        m = d2b.dt(x["opengDt"])[:7]
        k = dict(bidNtceNo=no, bidNtceOrd=od)
        prices = [{**k, "bidNtceNm": x["cntrwkNm"], "plnprc": E, "bssamt": B, "totRsrvtnPrceNum": len(bs),
                   "compnoRsrvtnPrceSno": b["epprRank"], "bsisPlnprc": b["planPrce"], "drwtYn": b.get("choiYsno", "")} for b in bs]
        bidders = [{**k, "opengRank": int(b["bidnRank"]) if (b.get("bidnRank") or "0").isdigit() and int(b["bidnRank"]) else "",
                    "prcbdrBizno": b.get("bznsRgnb", ""), "prcbdrNm": b.get("mfkrName", ""), "prcbdrCeoNm": b.get("rptrKore", ""),
                    "bidprcAmt": b.get("tbidAmnt", ""), "bidprcrt": b.get("bidnRate", ""),
                    "rmrk": "낙찰하한선 미달" if "미만" in b.get("bidxNote", "") else (b.get("bidxNote") if "취소" in b.get("bidxNote", "") else "정상"),
                    "bidprcDt": d2b.dt(b.get("gpsTime", ""))} for b in mn]
        notice = {**k, "bssamt": B, "rsrvtnPrceRngBgnRate": d.get("asessRtLwlt", ""), "rsrvtnPrceRngEndRate": d.get("asessRtUplmt", ""),
                  "bidPrceCalclAYn": "Y", "A": d2b.a_value(d), "sucsfbidLwltRate": d.get("scsbidLwltRt") or "-",
                  **{f: d.get(f, "") for f in d2b.A_FIELDS}}
        bid = {**k, "bidNtceNm": x["cntrwkNm"], "opengDt": d2b.dt(x["opengDt"]), "prtcptCnum": len(mn),
               "ntceInsttNm": x.get("ornt", ""), "dminsttNm": d.get("lc", ""), "src": "D2B", "areaLmtt": d.get("areaLmttList") or "제한없음"}
        n[1] += save(m, "bidders", bidders, ["bidNtceNo", "bidNtceOrd", "prcbdrBizno"])
        save(m, "prices", prices, ["bidNtceNo", "bidNtceOrd", "compnoRsrvtnPrceSno"])
        save(m, "notices", [notice], ["bidNtceNo", "bidNtceOrd"])
        n[0] += save(m, "bids", [bid], ["bidNtceNo", "bidNtceOrd"])   # bids를 마지막에 저장
    print(f"[{label}] {start:%Y-%m-%d}~{end:%Y-%m-%d}: 공고 {n[0]} / 투찰 {n[1]} (누적 호출 {d2b.calls})")


def main():
    if not d2b.KEY: sys.exit("DATA_GO_KR_KEY 미설정")
    seen = set(SEEN.read_text().split()) if SEEN.exists() else set()
    today = datetime.now()
    try:
        collect(today - timedelta(days=3), today, "일일", seen)
        cur = datetime.strptime(CURSOR.read_text().strip(), "%Y-%m-%d") if CURSOR.exists() else today - timedelta(days=3)
        while cur > BACKFILL_UNTIL:
            s = max(cur - timedelta(days=7), BACKFILL_UNTIL)
            collect(s, cur - timedelta(days=1), "과거", seen)
            cur = s
            CURSOR.write_text(cur.strftime("%Y-%m-%d"))
    except Budget:
        print(f"마감 시각 도달 ({d2b.calls}회). 다음 실행에서 이어서." if time.time() >= STOP_AT else f"호출 한도 도달 ({d2b.calls}회). 다음 실행에서 이어서.")
    except RuntimeError as e:
        print("오류:", e)
    SEEN.write_text("\n".join(sorted(seen)) + "\n")
    print(f"총 호출 {d2b.calls}회 / 과거 진행 위치: {CURSOR.read_text().strip() if CURSOR.exists() else '-'}")


if __name__ == "__main__":
    main()
