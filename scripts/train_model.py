import os
import pandas as pd
import numpy as np
import joblib
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, mean_squared_error
import lightgbm as lgb

def load_data(file_path):
    """
    학습용 데이터를 불러오는 함수
    데이터가 없을 경우 구조에 맞는 샘플 데이터를 생성하여 반환합니다.
    """
    if not os.path.exists(file_path):
        print(f"[안내] {file_path} 파일이 존재하지 않아 가상의 샘플 학습 데이터를 생성합니다.")
        np.random.seed(42)
        n_samples = 1000
        
        sample_data = pd.DataFrame({
            'hour': np.random.randint(0, 24, n_samples),
            'day_of_week': np.random.randint(0, 7, n_samples),
            'current_stat': np.random.choice([2, 3, 4], n_samples), # 2: 대기, 3: 충전중, 4: 중지 등
            'output': np.random.choice([7, 50, 100, 200], n_samples), # 충전 용량 (kW)
            'zcode': np.random.choice([11, 28, 41], n_samples), # 지역 코드 (서울, 인천, 경기 등)
            'parkingFree': np.random.choice([0, 1], n_samples), # 주차 무료 여부 (0 또는 1)
            'target_utilization_1h': np.random.beta(2, 5, n_samples) # 1시간 후 예상 이용률 (0~1 사이)
        })
        
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        sample_data.to_csv(file_path, index=False)
    
    return pd.read_csv(file_path)

def main():
    # 1. 파일 경로 설정
    data_path = "data/train_data.csv"
    model_dir = "models"
    model_path = os.path.join(model_dir, "lgbm_congestion_model.pkl")
    
    os.makedirs(model_dir, exist_ok=True)
    
    print("1. 데이터 로드 중...")
    df = load_data(data_path)
    
    # 2. 피처(X)와 타겟(y) 설정
    # 공단 OpenAPI 가이드의 항목(시간, 요일, 충전기 상태, 용량, 지역 등)을 피처로 활용[cite: 2]
    features = ['hour', 'day_of_week', 'current_stat', 'output', 'zcode', 'parkingFree']
    target = 'target_utilization_1h' # 1시간 뒤 예상 이용률
    
    X = df[features]
    y = df[target]
    
    # 3. 학습/테스트 데이터 분리 (8:2)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    print("2. LightGBM 모델 학습 시작...")
    model = lgb.LGBMRegressor(
        n_estimators=100,
        learning_rate=0.05,
        max_depth=6,
        random_state=42
    )
    model.fit(X_train, y_train)
    
    print("3. 모델 성능 평가 중...")
    preds = model.predict(X_test)
    
    mae = mean_absolute_error(y_test, preds)
    rmse = np.sqrt(mean_squared_error(y_test, preds))
    
    print("=" * 30)
    print(f" [모델 평가 결과]")
    print(f" - MAE (평균 절대 오차) : {mae:.4f}")
    print(f" - RMSE (평균 제곱근 오차): {rmse:.4f}")
    print("=" * 30)
    
    # 4. 학습된 모델 저장 (.pkl)
    joblib.dump(model, model_path)
    print(f"4. 모델 저장 완료: {model_path}")

if __name__ == "__main__":
    main()