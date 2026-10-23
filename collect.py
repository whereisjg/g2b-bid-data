"""나라장터 공사 개찰결과 수집기 (공공데이터포털 낙찰정보서비스)

매 실행마다:
  1) 최근 N일 개찰분 수집 (매일 결과)
  2) 남은 호출 한도로 과거 데이터를 1주 단위로 거꾸로 채움 (BACKFILL_UNTIL = 최근 365일까지)
저장: data/YYYY-MM/{bids,bidders,prices}.csv (개찰월 기준 분할)

사용:
  python collect.py                 # 일일 + 과거 채우기
  python collect.py --days 7        # 최근 7일
  python collect.py --no-backfill   # 과거 채우기 생략
  python collect.py --probe         # API 응답 구조만 출력
환경변수: DATA_GO_KR_KEY (일반 인증키, Decoding)
"""
import argparse, csv, json, os, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
import net

BASE = "https://apis.data.go.kr/1230000/as/ScsbidInfoService"
KEY = os.environ.get("DATA_GO_KR_KEY", "")
DATA = Path(__file__).parent / "data"
CURSOR = DATA / "backfill_cursor.txt"   # 과거 채우기 진행 위치 (이 날짜 이전을 아직 미수집)

REGION_WORDS = ["강원", "춘천"]          # 공고기관·수요기관명 포함 단어
BACKFILL_UNTIL = (datetime.now() - timedelta(days=365)).replace(hour=0, minute=0, second=0, microsecond=0)   # 최근 365일만 (2026-10-23 사용자 결정, 그 이전은 수집·활용 안 함)
DAILY_LIMIT = int(os.environ.get("CALL_LIMIT") or 3500)                       # 운영계정(기능별 하루 100,000회). 실행 시간 기준(~1.3초/회, 약 75분) — enrich·d2b와 합쳐 07:40 전에 끝나게
SAFETY = 80                              # 한도 여유분
STOP_AT = float(os.environ.get("STOP_AT") or "inf")   # 이 시각(epoch)이 지나면 중단 (추가 수집용)
KST = timezone(timedelta(hours=9))
REFILL_DAYS = 14                         # 개찰 후 이 기간 안에 참가업체·예가가 비어 있으면 다시 조회

calls = 0


class Budget(Exception):
    pass


def call(op, base=BASE, **params):
    global calls
    p = {"serviceKey": KEY, "type": "json", "pageNo": 1, "numOfRows": 999, **params}
    out = []
    while True:
        if calls >= DAILY_LIMIT - SAFETY or time.time() >= STOP_AT:
            raise Budget()
        r = None
        for i in range(6):
            try:
                calls += 1
                r = requests.get(f"{base}/{op}", params=p, timeout=30)
                r.raise_for_status()
                j = r.json()
                break
            except Exception as e:
                if not net.retry(i, e):
                    raise RuntimeError(f"{op} 실패: {e} / {r.text[:300] if r is not None else ''}")
        err = (j.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader")
        if err: raise RuntimeError(f"{op} 오류 {err.get('returnReasonCode')} {err.get('returnAuthMsg') or err.get('errMsg')}")
        hdr = j.get("response", {}).get("header", {})
        if hdr.get("resultCode") not in ("00", None):
            raise RuntimeError(f"{op} 오류: {hdr}")
        body = j.get("response", {}).get("body", {})
        items = body.get("items") or []
        if isinstance(items, dict):
            items = items.get("item", [])
        if isinstance(items, dict):
            items = [items]
        out += items
        if p["pageNo"] * p["numOfRows"] >= int(body.get("totalCount", 0) or 0):
            return out
        p["pageNo"] += 1


def save(month, name, rows, key):
    if not rows:
        return 0
    d = DATA / month
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.csv"
    old = []
    if path.exists():
        with open(path, encoding="utf-8-sig") as f:
            old = list(csv.DictReader(f))
    seen = {tuple(r.get(k, "") for k in key) for r in old}
    new = [r for r in rows if tuple(str(r.get(k, "")) for k in key) not in seen]
    if not new:
        return 0
    cols = list(dict.fromkeys([c for r in old + new for c in r]))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(old + new)
    return len(new)


def done_keys():
    s = set()
    for p in DATA.glob("*/bids.csv"):
        with open(p, encoding="utf-8-sig") as f:
            s |= {(r["bidNtceNo"], r["bidNtceOrd"]) for r in csv.DictReader(f)}
    return s


def match(b):
    inst = f"{b.get('ntceInsttNm', '')} {b.get('dminsttNm', '')}"
    return any(w in inst for w in REGION_WORDS)


def fetch_detail(b, m):
    """공고 1건의 참가업체·복수예가 조회 → m월 파일에 저장. (새 투찰 행 수, 새 예가 행 수)"""
    no, od = b.get("bidNtceNo"), b.get("bidNtceOrd", "000")
    comp = call("getOpengResultListInfoOpengCompt", inqryDiv=2, bidNtceNo=no, bidNtceOrd=od)
    prices = call("getOpengResultListInfoCnstwkPreparPcDetail", inqryDiv=2, bidNtceNo=no, bidNtceOrd=od)
    for r in comp + prices:
        r.setdefault("bidNtceNo", no); r.setdefault("bidNtceOrd", od)
    time.sleep(0.2)
    return (save(m, "bidders", comp, ["bidNtceNo", "bidNtceOrd", "prcbdrBizno"]),
            save(m, "prices", prices, ["bidNtceNo", "bidNtceOrd", "compnoRsrvtnPrceSno"]))


def refill():
    """개찰 직후라 참가업체·복수예가가 비어 있는 채 저장된 공고를 다시 조회 (개찰 후 REFILL_DAYS일까지, 유찰 공고도 이 기간엔 재확인)"""
    since = (datetime.now(KST) - timedelta(days=REFILL_DAYS)).strftime("%Y-%m-%d")
    tried = filled = 0
    for bp in sorted(DATA.glob("20*-*/bids.csv"))[-2:]:
        d = bp.parent
        have = {}
        for name in ("bidders", "prices"):
            f = d / f"{name}.csv"
            have[name] = {(r["bidNtceNo"], r["bidNtceOrd"]) for r in csv.DictReader(open(f, encoding="utf-8-sig"))} if f.exists() else set()
        for b in list(csv.DictReader(open(bp, encoding="utf-8-sig"))):
            k = (b["bidNtceNo"], b["bidNtceOrd"])
            if b.get("src") == "D2B" or b.get("opengDt", "")[:10] < since or (k in have["bidders"] and k in have["prices"]): continue
            nb, np_ = fetch_detail(b, d.name)
            tried += 1; filled += bool(nb or np_)
    print(f"[재수집] 참가업체·예가가 비어 있던 최근 {REFILL_DAYS}일 공고 {tried}건 다시 조회 → {filled}건 채움 (누적 호출 {calls})")


def collect(start, end, label):
    """start~end 개찰분 수집. 한도 초과 시 Budget 발생 (이미 저장한 건 유지)."""
    rng = dict(inqryBgnDt=start.strftime("%Y%m%d0000"), inqryEndDt=end.strftime("%Y%m%d2359"))
    bids = call("getOpengResultListInfoCnstwk", inqryDiv=1, **rng)
    have = done_keys()
    targets = [b for b in bids if match(b) and (b.get("bidNtceNo"), b.get("bidNtceOrd", "000")) not in have]
    n = [0, 0, 0]
    for b in targets:
        m = (b.get("opengDt") or start.strftime("%Y-%m"))[:7]
        nb, np_ = fetch_detail(b, m)
        n[1] += nb; n[2] += np_
        # bids를 마지막에 저장: 중간에 끊겨도 다음 실행에서 재수집됨. 참가업체·예가가 비어 있으면 refill()이 다시 조회
        n[0] += save(m, "bids", [b], ["bidNtceNo", "bidNtceOrd"])
    print(f"[{label}] {start:%Y-%m-%d}~{end:%Y-%m-%d}: 대상 {len(targets)}건 → 공고 {n[0]} / 투찰 {n[1]} / 예가 {n[2]} (누적 호출 {calls})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--no-backfill", action="store_true")
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    if not KEY:
        sys.exit("DATA_GO_KR_KEY 미설정")
    DATA.mkdir(exist_ok=True)

    today = datetime.now()
    if a.probe:
        rng = dict(inqryBgnDt=(today - timedelta(days=a.days)).strftime("%Y%m%d0000"), inqryEndDt=today.strftime("%Y%m%d2359"))
        bids = call("getOpengResultListInfoCnstwk", inqryDiv=1, **rng)
        print(len(bids), "건 / 매칭", sum(map(match, bids)), "/ 예:", json.dumps(bids[:1], ensure_ascii=False, indent=1))
        return

    try:
        # 1) 매일 결과
        collect(today - timedelta(days=a.days), today, "일일")
        # 1-1) 비어 있던 최근 공고 다시 조회
        refill()
        # 2) 과거 채우기
        if not a.no_backfill:
            cur = datetime.strptime(CURSOR.read_text().strip(), "%Y-%m-%d") if CURSOR.exists() \
                else today - timedelta(days=a.days)
            while cur > BACKFILL_UNTIL:
                s = max(cur - timedelta(days=7), BACKFILL_UNTIL)
                collect(s, cur - timedelta(days=1), "과거")
                cur = s
                CURSOR.write_text(cur.strftime("%Y-%m-%d"))
            if cur <= BACKFILL_UNTIL:
                print("과거 채우기 완료")
    except Budget:
        print(f"마감 시각 도달 ({calls}회). 다음 실행에서 이어서 수집." if time.time() >= STOP_AT else f"일일 호출 한도 도달 ({calls}회). 다음 실행에서 이어서 수집.")
    except RuntimeError as e:   # 연결 오류 3회·게이트웨이 오류 → 이 단계만 멈추고 다음 단계(enrich·d2b)는 계속
        print("오류:", e)
    print(f"총 호출 {calls}회 / 과거 진행 위치: {CURSOR.read_text().strip() if CURSOR.exists() else '-'}")


if __name__ == "__main__":
    main()
