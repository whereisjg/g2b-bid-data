# g2b-bid-data

공공데이터포털 API로 공사 개찰결과를 매일 수집하는 저장소 (전기공사업 중심).

| 파일 | 내용 |
|---|---|
| `collect.py` | 나라장터 낙찰정보서비스: 강원 지역 기관 공사 개찰결과·참가업체·복수예가 |
| `enrich.py` | 나라장터 입찰공고정보서비스: 공고별 기초금액·A값·낙찰하한율 |
| `d2b.py`, `collect_d2b.py` | 방위사업청 국방전자조달 시설공사 개찰결과 |
| `collect_nation.py` | 전국 전기공사(업종코드 0037) 개찰결과 요약 (업체별 사정률 위치만 압축 저장) |

데이터: `data/YYYY-MM/{bids,bidders,prices,notices}.csv` (개찰월 기준), `data/nation/` (전국 요약).
원본은 모두 공공데이터포털에 공개된 정보입니다.

실행: GitHub Actions (`collect.yml` 하루 3회, `collect-nation.yml` 하루 1회). Secret `DATA_GO_KR_KEY` 필요.
