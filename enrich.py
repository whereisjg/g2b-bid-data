"""수집한 개찰 공고에 공고정보(A값 구성·기초금액·순공사원가·낙찰하한율)를 붙여 data/YYYY-MM/notices.csv 저장
입찰공고정보서비스는 낙찰정보서비스와 호출 한도가 별도. 하루 LIMIT 회까지, 최신 공고부터.
  - 기초금액 API(getBidPblancListInfoCnstwkBsisAmount): A값·기초금액
  - 공고 목록 API(getBidPblancListInfoCnstwk): 낙찰하한율 sucsfbidLwltRate ("-" = 조회했으나 없음, 재시도 안 함)
"""
import csv, os, time, requests
import net
from pathlib import Path
KEY = os.environ["DATA_GO_KR_KEY"]
B = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
DATA = Path(__file__).parent / "data"
LIMIT = int(os.environ.get("CALL_LIMIT") or 3000)   # 운영계정(2026-10-21). 한도보다 실행 시간 기준(~0.6초/회, 약 30분) — collect·d2b와 합쳐 07:40 전에 끝나게
STOP_AT = float(os.environ.get("STOP_AT") or "inf")   # 이 시각(epoch)이 지나면 중단 (추가 수집용)
A_FIELDS = ("npnInsrprm", "mrfnHealthInsrprm", "rtrfundNon", "odsnLngtrmrcprInsrprm", "sftyMngcst", "sftyChckMngcst", "qltyMngcst")

def get(op, **p):
    for i in range(6):
        try:
            r = requests.get(f"{B}/{op}", params={"serviceKey": KEY, "type": "json", "pageNo": 1, "numOfRows": 10, **p}, timeout=30)
            j = r.json(); break
        except Exception as e:
            if not net.retry(i, e): raise
    err = (j.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader")
    if err: raise RuntimeError(f"{op} 오류 {err.get('returnReasonCode')} {err.get('returnAuthMsg') or err.get('errMsg')}")
    hdr = j.get("response", {}).get("header", {})
    if hdr.get("resultCode") not in ("00", None):
        raise RuntimeError(hdr)
    items = (j.get("response", {}).get("body", {}) or {}).get("items") or []
    if isinstance(items, dict): items = items.get("item", [])
    if isinstance(items, dict): items = [items]
    return items

def pick(items, od):
    return [x for x in items if x.get("bidNtceOrd", od) == od] or items

# 월별 notices 로드, 미보유(A 없음) 또는 하한율 미조회 공고 선별
store, todo = {}, []
for month in sorted(DATA.glob("20*-*"), reverse=True):
    bf = month / "bids.csv"
    if not bf.exists(): continue
    nf = month / "notices.csv"
    old = list(csv.DictReader(open(nf, encoding="utf-8-sig"))) if nf.exists() else []
    store[month] = {(r["bidNtceNo"], r["bidNtceOrd"]): r for r in old}
    for r in csv.DictReader(open(bf, encoding="utf-8-sig")):
        row = store[month].get((r["bidNtceNo"], r["bidNtceOrd"]))
        if row is None or not row.get("sucsfbidLwltRate"):
            todo.append((month, r["bidNtceNo"], r["bidNtceOrd"], r.get("opengDt", "")))
todo.sort(key=lambda x: x[3], reverse=True)
print("공고정보 미보유(A값 또는 하한율) 공고", len(todo), "건")

calls = saved = errs = 0
changed = set()
for month, no, od, _ in todo:
    row = store[month].get((no, od))
    need = 1 if row is not None else 2
    if calls + need > LIMIT or time.time() >= STOP_AT:
        print("마감 시각 도달, 다음 실행에서 이어서" if time.time() >= STOP_AT else "한도 도달, 다음 실행에서 이어서"); break
    try:
        if row is None:
            calls += 1
            it = pick(get("getBidPblancListInfoCnstwkBsisAmount", inqryDiv=2, bidNtceNo=no), od)
            if it:
                x = it[0]
                row = {k: x.get(k, "") for k in ("bidNtceNo", "bssamt", "rsrvtnPrceRngBgnRate", "rsrvtnPrceRngEndRate", "bssAmtPurcnstcst", "bidPrceCalclAYn", *A_FIELDS)}
                row["bidNtceOrd"] = od
                row["A"] = sum(int(float(x.get(f) or 0)) for f in A_FIELDS) if x.get("bidPrceCalclAYn", "Y") != "N" else 0
            else:
                row = {"bidNtceNo": no, "bidNtceOrd": od, "A": ""}   # 공고정보 없음 (자체공고 등) — 재시도 안 함
        calls += 1
        it = pick(get("getBidPblancListInfoCnstwk", inqryDiv=2, bidNtceNo=no, bidNtceOrd=od), od)
        row["sucsfbidLwltRate"] = (it[0].get("sucsfbidLwltRate") if it else "") or "-"
    except Exception as e:
        print("오류", no, type(e).__name__)   # 메시지에 인증키 URL이 들어가므로 종류만
        errs += 1
        if errs >= 5: print("연속 오류 5회, 중단"); break
        continue
    errs = 0
    store[month][(no, od)] = row
    changed.add(month); saved += 1
    time.sleep(0.1)

for month in changed:
    rs = list(store[month].values())
    cols = list(dict.fromkeys(c for r in rs for c in r))
    with open(month / "notices.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rs)
print("저장", saved, "건 / 호출", calls)
