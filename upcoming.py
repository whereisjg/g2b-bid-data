"""다가오는 강원 공사 공고(나라장터·국방전자조달)와 공고별 면허·지역·기초금액을 data/upcoming.json에 저장
추천 시각(07:40·16:47)에 data.go.kr 연결이 안 될 때 추천이 이 목록으로 계산하도록 매 수집 회차마다 갱신 (2026-10-24 16:47 연결 시간 초과)
공고 정보만 저장 (회사 정보 없음). 이전 파일의 공고별 조회 결과는 재사용해 호출을 줄임
"""
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import collect, d2b

KST = timezone(timedelta(hours=9))
OUT = Path(__file__).parent / "data" / "upcoming.json"
G2B = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
DAYS = 7   # 마감이 이 기간 안인 공고만


def main():
    now = datetime.now(KST)
    old = json.load(open(OUT)) if OUT.exists() else {}
    snap = {"at": now.isoformat(timespec="minutes"), "g2b": [], "g2b_detail": {}, "d2b": [], "d2b_detail": {}}
    end = (now + timedelta(days=DAYS)).strftime("%Y-%m-%d %H:%M")

    lst = collect.call("getBidPblancListInfoCnstwk", base=G2B, inqryDiv=1,
                       inqryBgnDt=(now - timedelta(days=21)).strftime("%Y%m%d0000"), inqryEndDt=now.strftime("%Y%m%d%H%M"))
    for b in lst:   # 강원 현장 + 마감 전 (나머지 판단은 recommend.py가 그대로 함)
        where = f"{b.get('cnstrtsiteRgnNm', '')} {b.get('ntceInsttNm', '')} {b.get('dminsttNm', '')}"
        if ("강원" in where or "춘천" in where) and now.strftime("%Y-%m-%d %H:%M") < b.get("bidClseDt", "")[:16] <= end:
            snap["g2b"].append(b)
    for b in snap["g2b"]:
        no, od = b["bidNtceNo"], b.get("bidNtceOrd", "000")
        for op in ("getBidPblancListInfoLicenseLimit", "getBidPblancListInfoPrtcptPsblRgn", "getBidPblancListInfoCnstwkBsisAmount"):
            k = f"{op}|{no}|{od}"
            v = old.get("g2b_detail", {}).get(k)
            if v is None: v = collect.call(op, base=G2B, inqryDiv=2, bidNtceNo=no, bidNtceOrd=od)
            if op.endswith("BsisAmount") and not any(x.get("bssamt") for x in v): continue   # 기초금액 미공개는 저장 안 함 (다음 회차에 다시)
            snap["g2b_detail"][k] = v
    print(f"나라장터 {len(snap['g2b'])}건 · 호출 {collect.calls}회")

    try:   # 국방: 실패해도 나라장터 목록은 저장
        for x in d2b.notices(now.strftime("%Y%m%d"), (now + timedelta(days=10)).strftime("%Y%m%d")):
            if not now.strftime("%Y-%m-%d %H:%M") < d2b.dt(x.get("biddocPresentnClosDt", ""))[:16] <= end: continue
            k = f"{x['cntrwkNo']}|{x['pblancOdr']}"
            d = old.get("d2b_detail", {}).get(k) or d2b.detail(x)
            snap["d2b"].append(x)
            if d: snap["d2b_detail"][k] = d
        print(f"국방 {len(snap['d2b'])}건 · 호출 {d2b.calls}회")
    except RuntimeError as e:
        print("국방 조회 실패:", e); snap["d2b"], snap["d2b_detail"] = old.get("d2b", []), old.get("d2b_detail", {})

    json.dump(snap, open(OUT, "w"), ensure_ascii=False)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, collect.Budget) as e:   # 실패하면 이전 파일 유지
        print("다가오는 공고 저장 실패:", e); sys.exit(0)
