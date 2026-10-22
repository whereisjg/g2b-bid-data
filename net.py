"""API 재시도 간격: 연결 자체가 안 되면(서버 점검·네트워크 순간 장애) 30초부터 늘려 최대 6번(약 15분), 그 외 오류는 3초 간격 3번"""
import time
import requests

def retry(i, e):
    """i번째 시도가 e로 실패 → 기다린 뒤 True(다시 시도) / False(포기)"""
    conn = isinstance(e, (requests.ConnectionError, requests.Timeout))
    if i + 1 >= (6 if conn else 3): return False
    time.sleep(min(300, 30 * 2 ** i) if conn else 3)
    return True
