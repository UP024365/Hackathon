import os
import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import train_test_split


def load_and_enhance_data(file_path):
  """학습용 데이터를 불러오고, 1시간 후 예측 정확도를 극대화하기 위한

  고급 시계열 및 범주형 파생 피처(Feature Engineering)를 생성합니다.
  """
  if not os.path.exists(file_path):
    print(
        f"[안내] {file_path} 파일이 존재하지 않아 정교한 샘플 학습 데이터를"
        " 생성합니다."
    )
    np.random.seed(42)
    n_samples = 5000  # 데이터 양을 늘려 학습 완성도 향상

    # 한국환경공단 OpenAPI 명세 기반 가상 데이터 생성[cite: 2]
    hours = np.random.randint(0, 24, n_samples)
    days = np.random.randint(0, 7, n_samples)

    # 출퇴근 시간대(8~9시, 18~19시)에 이용률이 높아지는 경향 반영
    base_util = np.where(
        (hours.isin([8, 9, 18, 19])) | (days >= 5),
        np.random.beta(5, 2, n_samples),
        np.random.beta(2, 5, n_samples),
    )

    sample_data = pd.DataFrame({
        'hour': hours,
        'day_of_week': days,
        'current_stat': np.random.choice([2, 3, 4], n_samples, p=[0.5, 0.4, 0.1]),
        'output': np.random.choice(
            [7, 50, 100, 200], n_samples, p=[0.3, 0.4, 0.2, 0.1]
        ),
        'zcode': np.random.choice([11, 28, 41, 43], n_samples),
        'parkingFree': np.random.choice([0, 1], n_samples, p=[0.7, 0.3]),
        'current_utilization': base_util,
    })

    # 1시간 후 타겟 값 생성 (약간의 시계열 연속성 부여)
    sample_data['target_utilization_1h'] = np.clip(
        sample_data['current_utilization'] + np.random.normal(0, 0.05, n_samples),
        0.0,
        1.0,
    )

    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    sample_data.to_csv(file_path, index=False)

  df = pd.read_csv(file_path)

  # --- 피처 엔지니어링 (정확도 극대화) ---
  # 1. 시간 주기성 표현 (Sin/Cos 변환: 24시간 순환 구조 반영)
  df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24.0)
  df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24.0)

  # 2. 주말 여부 플래그
  df['is_weekend'] = df['day_of_week'].apply(lambda x: 1 if x >= 5 else 0)

  # 3. 출퇴근/심야 시간대 가중치 플래그
  df['is_rush_hour'] = df['hour'].apply(
      lambda x: 1 if x in [8, 9, 18, 19] else 0
  )

  # 4. 시차 및 롤링 특성 모사 (데이터에 과거 이력이 있는 경우)
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
  data_path = "data/train_data.csv"
  model_dir = "models"
  model_path = os.path.join(model_dir, "lgbm_congestion_model.pkl")

  os.makedirs(model_dir, exist_ok=True)

  print("1. 데이터 로드 및 고성능 피처 엔지니어링 수행 중...")
  df = load_and_enhance_data(data_path)

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

  X = df[features]
  y = df[target]

  # 데이터 분리 (훈련:테스트 = 8:2)
  X_train, X_test, y_train, y_test = train_test_split(
      X, y, test_size=0.2, random_state=42
  )

  print("2. 하이퍼파라미터 최적화 및 LightGBM 모델 학습 시작...")
  # 트리를 깊고 정교하게 학습시켜 오차를 최대한 줄이도록 설정 (오래 걸려도 정확도 우선)
  model = lgb.LGBMRegressor(
      n_estimators=500,
      learning_rate=0.01,
      max_depth=8,
      num_leaves=63,
      subsample=0.8,
      colsample_bytree=0.8,
      random_state=42,
  )
  model.fit(
      X_train,
      y_train,
      eval_set=[(X_test, y_test)],
      callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)],
  )

  print("3. 모델 성능 평가 중...")
  preds = model.predict(X_test)

  # 결과값은 0~1 사이로 클리핑 (이용률 범주 보정)
  preds = np.clip(preds, 0.0, 1.0)

  mae = mean_absolute_error(y_test, preds)
  rmse = np.sqrt(mean_squared_error(y_test, preds))

  print("=" * 40)
  print(" [최적화된 1시간 후 혼잡도 예측 모델 평가 결과]")
  print(f" - MAE (평균 절대 오차) : {mae:.4f}")
  print(f" - RMSE (평균 제곱근 오차): {rmse:.4f}")
  print("=" * 40)

  # 모델 저장
  joblib.dump(model, model_path)
  print(f"4. 고성능 모델 저장 완료: {model_path}")


if __name__ == "__main__":
  main()