import os
import pandas as pd
import numpy as np
import glob
import zipfile

def convert_history_folders_to_train_data(
    base_dir=r"C:\Users\wkdwh\Downloads\ev-hackathon.github.io-history\ev-hackathon.github.io-history\history", 
    output_path=r"C:\Users\wkdwh\Downloads\ev-hackathon.github.io-main\ev-hackathon.github.io-main\data\train_data2.csv"
):
    print("1. 날짜별 폴더 및 충전소 CSV 파일들 로드 중...")
    
    all_csv_files = glob.glob(os.path.join(base_dir, "**/*.csv"), recursive=True)
    
    if not all_csv_files:
        print(f"[오류] 지정된 경로에 CSV 파일이 없습니다: {base_dir}")
        print("압축이 해제된 실제 'history' 폴더 경로를 확인해주세요.")
        return

    df_list = []
    for file in all_csv_files:
        try:
            # 파일 경로에서 날짜 폴더명 추출 (예: ...\2026-10-14\43110.csv -> '2026-10-14')
            path_parts = os.path.normpath(file).split(os.sep)
            date_str = None
            for part in path_parts:
                if part.startswith("2026-") or part.startswith("2025-"):
                    date_str = part
                    break
            
            station_file_id = os.path.splitext(os.path.basename(file))[0]
            
            try:
                temp_df = pd.read_csv(file, encoding='utf-8')
            except:
                temp_df = pd.read_csv(file, encoding='cp949')
            
            if date_str:
                temp_df['date_str'] = date_str
            
            # 충전소 식별자 부여 (파일명 기반)
            temp_df['충전소명'] = station_file_id
                
            df_list.append(temp_df)
        except Exception as e:
            continue

    if not df_list:
        print("[오류] 유효하게 읽어들인 데이터가 없습니다.")
        return

    df = pd.concat(df_list, ignore_index=True)
    
    print(f"총 {len(df):,}")
    
    # 💡 't' 컬럼(예: 1423 -> 14시 23분)을 시간(Hour)으로 변환
    # 't' 값을 4자리 문자열로 맞춘 후 앞 2자리를 시간으로 추출
    df['t_str'] = df['t'].astype(str).str.zfill(4)
    df['hour'] = df['t_str'].str[:2].astype(int)
    
    # 날짜와 시간 결합하여 datetime 객체 생성
    df['datetime'] = pd.to_datetime(df['date_str'] + ' ' + df['hour'].astype(str) + ':00:00', errors='coerce')
    df['date'] = pd.to_datetime(df['date_str']).dt.date
    df['day_of_week'] = df['datetime'].dt.dayofweek
    
    df = df.dropna(subset=['datetime', 'hour'])
    print(f"유효한 타임스탬프 데이터 수: {len(df):,}건")

    # 충전소별, 날짜별, 시간대별 활성 상태(또는 충전 건수) 집계
    # sc(충전 중 건수) 또는 전체 레코드 수를 활용
    if 'sc' in df.columns:
        hourly_demand = df.groupby(['충전소명', 'date', 'hour', 'day_of_week'])['sc'].sum().reset_index(name='active_count')
    else:
        hourly_demand = df.groupby(['충전소명', 'date', 'hour', 'day_of_week']).size().reset_index(name='active_count')
    
    # 정규화 (충전소별 최대 활성 건수 기준으로 0~1 사이 이용률로 환산)
    max_counts = hourly_demand.groupby('충전소명')['active_count'].transform('max')
    hourly_demand['current_utilization'] = hourly_demand['active_count'] / (max_counts + 1e-5)
    
    # 시계열 특성 부여: 1시간 뒤의 이용률을 타겟(Target)으로 설정
    hourly_demand = hourly_demand.sort_values(['충전소명', 'date', 'hour'])
    hourly_demand['target_utilization_1h'] = hourly_demand.groupby('충전소명')['current_utilization'].shift(-1)
    
    # 결측치 제거
    hourly_demand = hourly_demand.dropna(subset=['target_utilization_1h'])

    # 머신러닝 학습용 데이터프레임 구성
    train_df = pd.DataFrame({
        'hour': hourly_demand['hour'],
        'day_of_week': hourly_demand['day_of_week'],
        'current_stat': 3,       # 충전 중 상태
        'output': 50,            # 기본 급속 용량 가정
        'zcode': 11,             # 지역 코드 (공공코드)
        'parkingFree': 1,        # 주차 무료 여부
        'current_utilization': hourly_demand['current_utilization'],
        'target_utilization_1h': hourly_demand['target_utilization_1h']
    })

    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)
    train_df.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"2. 학습용 데이터셋 생성 완료: {output_path} (총 {len(train_df):,}행)")

if __name__ == "__main__":
    convert_history_folders_to_train_data()