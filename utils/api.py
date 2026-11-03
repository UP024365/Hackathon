import os
import requests
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()
EV_API_KEY = os.getenv("EV_API_KEY")

def fetch_charging_stations(zcode="43", zscode=None, page=1, per_page=50):
    """
    환경부 전기차 충전소 정보 조회 서비스 (getChargerInfo)
    - zcode: 시도 코드 (기본값 '43' = 충청북도)
    - zscode: 시군구 코드 (예: 청주시 '43110', 충주시 '43130' 등)
    """
    url = "https://apis.data.go.kr/B552584/EvCharger/getChargerInfo"
    
    params = {
        "serviceKey": EV_API_KEY,
        "pageNo": page,
        "numOfRows": per_page,
        "dataType": "JSON",
        "zcode": zcode
    }
    
    # 상세 시군구가 지정되어 있다면 파라미터에 추가
    if zscode:
        params["zscode"] = zscode
        
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        
        data = response.json()
        
        # API 응답 구조 검증 (가이드 문서 기준)
        if data.get("resultCode") == "00" or "items" in data:
            items = data.get("items", {})
            # items가 빈 값이거나 없을 경우 처리
            if not items:
                return []
            
            charger_list = items.get("item", [])
            
            # 단일 항목일 경우 딕셔너리로 반환되는 경우 방지 (리스트로 변환)
            if isinstance(charger_list, dict):
                charger_list = [charger_list]
                
            return charger_list
        else:
            print(f"API 응답 에러 메시지: {data.get('resultMsg')}")
            return []
            
    except Exception as e:
        print(f"API 통신 중 에러 발생: {e}")
        return []

if __name__ == "__main__":
    print("--- 충청북도(43) 충전소 데이터 연동 테스트 ---")
    # 충청북도 청주시(43110) 데이터 5개만 테스트 조회
    stations = fetch_charging_stations(zcode="43", zscode="43110", per_page=5)
    print(f"불러온 충전소 수: {len(stations)}개\n")
    
    for s in stations:
        print(f"충전소명: {s.get('statNm')}")
        print(f"주소: {s.get('addr')} {s.get('addrDetail', '')}")
        print(f"위경도: ({s.get('lat')}, {s.get('lng')})")
        print(f"충전기 상태: {s.get('stat')} (2: 충전대기, 3: 충전중 등)")
        print("-" * 40)