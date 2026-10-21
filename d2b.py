"""국방전자조달(방위사업청 군수품조달정보) 시설 경쟁입찰 API 공통 함수
공고: /1690000/BidPblancInfoService, 결과: /1690000/BidResultInfoService — XML 전용, 개발계정 서비스별 하루 100회
공고번호는 나라장터와 겹치지 않게 "D2B-공사번호"로 저장
"""
import os, time
import xml.etree.ElementTree as ET
import requests

KEY = os.environ.get("DATA_GO_KR_KEY", "")
PB = "https://apis.data.go.kr/1690000/BidPblancInfoService"
RS = "https://apis.data.go.kr/1690000/BidResultInfoService"
A_FIELDS = ("inanAmnt", "inheAmnt", "treaAmnt", "retiDedu", "safeAmnt", "safeCost", "quasCost")   # 국민연금·건강보험·노인장기요양·퇴직공제·산업안전보건·안전관리·품질관리
calls = 0


def get(base, op, **p):
    """전체 페이지 조회 (999건씩)"""
    global calls
    out, page = [], 1
    while True:
        for i in range(3):
            try:
                calls += 1
                r = requests.get(f"{base}/{op}", params={"serviceKey": KEY, "pageNo": page, "numOfRows": 999, **p}, timeout=40)
                root = ET.fromstring(r.content); break
            except Exception as e:
                if i == 2: raise RuntimeError(f"{op} 실패: {type(e).__name__}")   # 메시지에 인증키 URL이 들어가므로 종류만
                time.sleep(3)
        if root.findtext(".//returnReasonCode"):   # 한도 초과 등 게이트웨이 오류 (OpenAPI_ServiceResponse)
            raise RuntimeError(f"{op} 오류 {root.findtext('.//returnReasonCode')} {root.findtext('.//returnAuthMsg') or root.findtext('.//errMsg')}")
        code = root.findtext(".//resultCode")
        if code not in ("00", None):
            raise RuntimeError(f"{op} 오류: {code} {root.findtext('.//resultMsg')}")
        out += [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
        if page * 999 >= int(root.findtext(".//totalCount") or 0): return out
        page += 1


def notices(begin, end):
    """개찰일 begin~end(YYYYMMDD) 시설 경쟁입찰 공고 중 공사, 공사번호별 최종 차수만"""
    last = {}
    for x in get(PB, "getFcltyCmpetBidPblancList", opengDateBegin=begin, opengDateEnd=end):
        if x.get("busiDivs") != "공사" or "취소" in x.get("pblancSe", ""): continue
        k = x["cntrwkNo"]
        if k not in last or int(x["pblancOdr"] or 0) > int(last[k]["pblancOdr"] or 0): last[k] = x
    return list(last.values())


def detail(x):
    d = get(PB, "getFcltyCmpetBidPblancDetail", pblancYear=x["pblancYear"], pblancSeCode=x["pblancSeCode"], pblancNo=x["pblancNo"],
            pblancOdr=x["pblancOdr"], cntrwkNo=x["cntrwkNo"], orntCode=x["orntCode"])
    return d[0] if d else None


def eligible(d, license="전기공사업", region="강원"):
    """면허에 전기공사업 + 지역제한이 강원이거나 없음"""
    return license in (d.get("lcnsLmttList") or "") and (not d.get("areaLmttList") or region in d["areaLmttList"])


def a_value(d):
    return sum(int(float(d.get(f) or 0)) for f in A_FIELDS)


def ntce_no(x):
    return f"D2B-{x['cntrwkNo']}", str(x["pblancOdr"]).zfill(3)


def dt(s):
    """202610201100 → 2026-10-20 11:00:00"""
    return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:00" if len(s) >= 12 else s
