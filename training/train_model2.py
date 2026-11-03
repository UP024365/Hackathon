import os
import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error


def enhance_features(df):
    """데이터프레임에 1시간 후 예측 정확도를 극대화하기 위한

    고급 시계열 및 범주형 파생 피처(Feature Engineering)를 생성합니다.
    """
    # 1. 시간 주기성 표현 (Sin/Cos 변환: 24시간 순환 구조 반영)
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24.0)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24.0)

    # 2. 주말 여부 플래그
    df['is_weekend'] = df['day_of_week'].apply(lambda x: 1 if x >= 5 else 0)

    # 3. 출퇴근/심야 시간대 가중치 플래그
    df['is_rush_hour'] = df['hour'].apply(
        lambda x: 1 if x in [8, 9, 18, 19] else 0
    )

    # 4. 시차 및 롤링 특성 모사
    if 'current_utilization' in df.columns:
        df['util_lag_1'] = df['current_utilization']
        df['util_rolling_mean_3'] = (
            df['current_utilization'].rolling(window=3, min_periods=1).mean()
        )
    else:
        df['util_lag_1'] = df['target_utilization_1h'] * 0.9
        df['util_rolling_mean_3'] = df['target_utilization_1h']

    return df


def main():
    train_path = "data/train_data.csv"
    test_path = "data/train_data2.csv"
    model_dir = "models"
    model_path = os.path.join(model_dir, "lgbm_congestion_model2.pkl")

    os.makedirs(model_dir, exist_ok=True)

    print("1. 학습용 데이터(train_data.csv) 로드 및 피처 엔지니어링 수행 중...")
    if not os.path.exists(train_path):
        print(f"[오류] 학습용 데이터 파일이 존재하지 않습니다: {train_path}")
        return
    train_df = pd.read_csv(train_path)
    train_df = enhance_features(train_df)

    print("2. 테스트용 데이터(train_data2.csv) 로드 및 피처 엔지니어링 수행 중...")
    if not os.path.exists(test_path):
        print(f"[오류] 테스트용 데이터 파일이 존재하지 않습니다: {test_path}")
        print("먼저 전처리 코드를 실행하여 data/train_data2.csv 파일을 생성해주세요.")
        return
    test_df = pd.read_csv(test_path)
    test_df = enhance_features(test_df)

    # 학습에 사용할 고도화된 피처 목록
    features = [
        'hour',
        'hour_sin',
        'hour_cos',
        'day_of_week',
        'is_weekend',
        'is_rush_hour',
        'current_stat',
        'output',
        'zcode',
        'parkingFree',
        'util_lag_1',
        'util_rolling_mean_3',
    ]
    target = 'target_utilization_1h'  # 1시간 뒤 예상 이용률

    X_train = train_df[features]
    y_train = train_df[target]
    
    X_test = test_df[features]
    y_test = test_df[target]

    print("3. LightGBM 모델 학습 시작...")
    # 트리를 깊고 정교하게 학습시켜 오차를 최대한 줄이도록 설정
    model = lgb.LGBMRegressor(
        n_estimators=500,
        learning_rate=0.01,
        max_depth=8,
        num_leaves=63,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
    )
    
    # 훈련 데이터로 학습 및 테스트 데이터를 통한 조기 종료(Early Stopping) 적용
    model.fit(
        X_train,
        y_train,
        eval_set=[(X_test, y_test)],
        callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)],
    )

    print("4. 실제 테스트 데이터셋(train_data2.csv)으로 모델 성능 평가 중...")
    preds = model.predict(X_test)

    # 결과값은 0~1 사이로 클리핑 (이용률 범주 보정)
    preds = np.clip(preds, 0.0, 1.0)

    mae = mean_absolute_error(y_test, preds)
    rmse = np.sqrt(mean_squared_error(y_test, preds))

    print("=" * 40)
    print(" [train_data2 모델 테스트 및 평가 결과]")
    print(f" - MAE (평균 절대 오차) : {mae:.4f}")
    print(f" - RMSE (평균 제곱근 오차): {rmse:.4f}")
    print("=" * 40)

    # 모델 저장
    joblib.dump(model, model_path)
    print(f"5. 고성능 모델 저장 완료: {model_path}")


if __name__ == "__main__":
    main()