import os
import pandas as pd
import numpy as np

def convert_excel_to_train_data(excel_path="서울시 소유 전기차충전기 일별 시간별 충전현황(2025년).xlsx", output_path="data/train_data.csv"):
    print("1. 서울시 실제 충전현황 엑셀 파일 로드 중...")
    if not os.path.exists(excel_path):
        print(f"[오류] 엑셀 파일을 찾을 수 없습니다: {excel_path}")
        return

    # 엑셀 헤더 스킵 후 로드 (3행이 헤더)
    df = pd.read_excel(excel_path, skiprows=3)
    df.columns = ['충전소명', '충전시작시간', '충전종료시간', '충전량(kW)']
    
    # 날짜 시간형으로 변환
    df['충전시작시간'] = pd.to_datetime(df['충전시작시간'], errors='coerce')
    df['충전종료시간'] = pd.to_datetime(df['충전종료시간'], errors='coerce')
    df = df.dropna(subset=['충전시작시간', '충전종료시간'])
    
    print(f"총 {len(df):,}건의 충전 이력 데이터를 전처리합니다.")

    # 시간대별(Hourly) 빈도 및 이용률 집계를 위해 시간 단위로 확장
    # 각 기록이 속한 '시작 시간(hour)'과 '요일(day_of_week)' 추출
    df['hour'] = df['충전시작시간'].dt.hour
    df['day_of_week'] = df['충전시작시간'].dt.dayofweek
    df['date'] = df['충전시작시간'].dt.date

    # 충전소별, 날짜별, 시간대별 충전 건수(수요) 집계
    hourly_demand = df.groupby(['충전소명', 'date', 'hour', 'day_of_week']).size().reset_index(name='active_count')
    
    # 정규화 (충전소별 최대 충전 건수 기준으로 0~1 사이 이용률로 환산)
    max_counts = hourly_demand.groupby('충전소명')['active_count'].transform('max')
    hourly_demand['current_utilization'] = hourly_demand['active_count'] / (max_counts + 1e-5)
    
    # 시계열 특성 부여: 1시간 뒤의 이용률을 타겟(Target)으로 설정
    # 같은 충전소 내에서 시간순 정렬 후 shift(-1) 적용
    hourly_demand = hourly_demand.sort_values(['충전소명', 'date', 'hour'])
    hourly_demand['target_utilization_1h'] = hourly_demand.groupby('충전소명')['current_utilization'].shift(-1)
    
    # 결측치 제거 (마지막 시간대 등)
    hourly_demand = hourly_demand.dropna(subset=['target_utilization_1h'])

    # 머신러닝 학습에 맞게 컬럼 매핑
    # (필요시 output, parkingFree 등 기본 스펙 정보를 충전소명에 매핑하여 추가 가능)
    train_df = pd.DataFrame({
        'hour': hourly_demand['hour'],
        'day_of_week': hourly_demand['day_of_week'],
        'current_stat': 3, # 충전 중 상태로 간주
        'output': 50,      # 기본 급속 용량 가정 (추후 매핑 가능)
        'zcode': 11,       # 서울시 지역 코드 (공공코드 11)
        'parkingFree': 1,  # 공영주차장 위주이므로 기본값 부여
        'current_utilization': hourly_demand['current_utilization'],
        'target_utilization_1h': hourly_demand['target_utilization_1h']
    })

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    train_df.to_csv(output_path, index=False)
    print(f"2. 실제 데이터 기반 학습용 데이터셋 생성 완료: {output_path} (총 {len(train_df):,}행)")

if __name__ == "__main__":
    convert_excel_to_train_data()