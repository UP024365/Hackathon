"""
1시간 뒤 충전소 이용률 예측 모델 — 전처리부터 학습·평가·저장까지 한 번에.

사용법 (프로젝트 루트에서):
    python -m training.train_model
    python -m training.train_model --history 충북기록/history     # 충북 실측 검증까지

데이터
  1) 서울시 소유 충전기 충전 이력(엑셀, 2025년 1년치)  → 학습·검증·서울 테스트
     충전 세션(시작·종료 시각)을 충전소 × 1시간 격자에 펼쳐 '그 시간에 평균 몇 대가 충전 중이었나'를 구하고,
     충전소 동시 사용 최대치(≈충전기 수)로 나눠 이용률(0~1)을 만든다. 충전이 없던 시간도 0으로 채워
     shift(1)이 정확히 '1시간 전/뒤'가 되게 한다.
  2) 충북 수집 기록(history 브랜치: 날짜/시군코드.csv, 실행마다 바뀐 충전소만 저장) → 충북 실측 검증
     실행 시각마다 충전소 상태를 복원해 이용률 = 충전중 / (대기 + 충전중) 을 구하고,
     45~75분 간격으로 찍힌 두 기록을 '현재 → 1시간 뒤' 쌍으로 써서 평가한다.
     (하루 3~7번만 찍혀 1시간 간격 쌍이 적으므로 학습에는 쓰지 않고 검증에만 쓴다)

피처 (충북 서버에서도 그대로 만들 수 있는 것만)
  시각·요일 정보 + 현재 이용률 + 1·2·24·168시간 전 이용률.
  충북 기록은 띄엄띄엄 있어 지난 시각 값이 없을 때가 많으므로, 학습 때 지난 값 일부를 일부러 비워(NaN)
  모델이 '값이 없을 때'도 예측하도록 한다. 예측할 때 모르는 값은 NaN(None)으로 넘기면 된다.

분할 (시간 순서, 미래 정보 누수 없음)
  학습 1~10월 / 검증(조기 종료) 11월 / 서울 테스트 12월. 테스트는 마지막에 한 번만 평가한다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL = ROOT / "서울시 소유 전기차충전기 일별 시간별 충전현황(2025년).xlsx"
MODEL_OUT = ROOT / "models" / "lgbm_congestion_model2.pkl"
METRICS_OUT = ROOT / "models" / "lgbm_congestion_model2_metrics.json"

LAGS = (1, 2, 24, 168)            # 몇 시간 전 이용률을 쓸지
FEATURES = ["hour", "hour_sin", "hour_cos", "day_of_week", "is_weekend", "is_rush_hour",
            "util_now", *[f"util_lag_{h}" for h in LAGS], "util_mean_3h"]
TARGET = "target_util_1h"
LAG_DROP_RATE = 0.4               # 학습 때 지난 값을 비워 두는 비율 (충북 기록 빈틈 대비)
SEED = 42


# ─────────────────────────────────────────────────────────────
# 공통: 시각 피처
# ─────────────────────────────────────────────────────────────
def time_features(ts: pd.Series) -> pd.DataFrame:
    hour = ts.dt.hour
    dow = ts.dt.dayofweek
    return pd.DataFrame({
        "hour": hour,
        "hour_sin": np.sin(2 * np.pi * hour / 24),
        "hour_cos": np.cos(2 * np.pi * hour / 24),
        "day_of_week": dow,
        "is_weekend": (dow >= 5).astype(int),
        "is_rush_hour": hour.isin([8, 9, 18, 19]).astype(int),
    }, index=ts.index)


def add_mean_3h(df: pd.DataFrame) -> pd.DataFrame:
    df["util_mean_3h"] = df[["util_now", "util_lag_1", "util_lag_2"]].mean(axis=1, skipna=True)
    return df


# ─────────────────────────────────────────────────────────────
# 1. 서울 충전 이력 → 충전소 × 1시간 이용률
# ─────────────────────────────────────────────────────────────
def load_seoul_sessions(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, skiprows=3)
    df.columns = ["station", "start", "end", "kwh"]
    df["start"] = pd.to_datetime(df["start"], errors="coerce")
    df["end"] = pd.to_datetime(df["end"], errors="coerce")
    df = df.dropna(subset=["station", "start", "end"])
    dur_h = (df["end"] - df["start"]).dt.total_seconds() / 3600
    df = df[(dur_h > 0) & (dur_h <= 24)]            # 음수·하루 넘는 기록은 오류로 보고 제외
    return df.reset_index(drop=True)


def seoul_hourly(sessions: pd.DataFrame) -> pd.DataFrame:
    """세션을 1시간 칸에 펼쳐 칸마다 '충전 중이던 시간(분)/60' = 평균 충전 대수를 구한다."""
    s = sessions[["station", "start", "end"]].copy()
    s["h0"] = s["start"].dt.floor("h")
    s["n"] = ((s["end"] - s["h0"]) / pd.Timedelta(hours=1)).apply(np.ceil).astype(int)
    rep = s.loc[s.index.repeat(s["n"])].copy()
    rep["slot"] = rep["h0"] + pd.to_timedelta(rep.groupby(level=0).cumcount(), unit="h")
    slot_end = rep["slot"] + pd.Timedelta(hours=1)
    overlap = (np.minimum(rep["end"], slot_end) - np.maximum(rep["start"], rep["slot"])).dt.total_seconds()
    rep["busy"] = overlap.clip(lower=0) / 3600
    busy = rep.groupby(["station", "slot"])["busy"].sum()

    # 빈 시간도 0으로 채운 완전한 격자 (그래야 shift가 정확히 1시간)
    year = pd.date_range("2025-01-01", "2025-12-31 23:00", freq="h")
    grid = pd.MultiIndex.from_product([busy.index.levels[0], year], names=["station", "slot"])
    df = busy.reindex(grid, fill_value=0.0).reset_index()

    # 충전소별 '동시에 쓰인 최대 대수'를 충전기 수로 간주 → 이용률 0~1
    cap = np.ceil(df.groupby("station")["busy"].transform("max")).clip(lower=1)
    df["util_now"] = (df["busy"] / cap).clip(0, 1)
    return df[["station", "slot", "util_now"]]


def seoul_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["station", "slot"]).reset_index(drop=True)
    g = df.groupby("station")["util_now"]
    for h in LAGS:
        df[f"util_lag_{h}"] = g.shift(h)
    df[TARGET] = g.shift(-1)
    df = add_mean_3h(df)
    df = pd.concat([df, time_features(df["slot"])], axis=1)
    return df.dropna(subset=[TARGET])


# ─────────────────────────────────────────────────────────────
# 2. 충북 수집 기록 → 충전소별 이용률 시계열 (검증용)
# ─────────────────────────────────────────────────────────────
COUNT_COLS = ["fa", "fc", "ff", "sa", "sc", "sf"]   # 급속 대기·충전중·고장 / 완속 대기·충전중·고장
RUN_MARK = "__run__"


def chungbuk_states(history_dir: Path) -> pd.DataFrame:
    """날짜/시군코드.csv (바뀐 충전소만 기록) → 실행 시각마다 전체 충전소 상태 복원."""
    out = []
    for path in sorted(history_dir.glob("20*/*.csv")):
        day = path.parent.name
        raw = pd.read_csv(path, dtype={"t": str, "statId": str})
        raw["t"] = raw["t"].str.zfill(4)
        runs = sorted(raw.loc[raw["statId"] == RUN_MARK, "t"].unique())
        data = raw[raw["statId"] != RUN_MARK]
        if data.empty or not runs:
            continue
        last = data.groupby(["statId", "t"])[COUNT_COLS].last()
        wide = last.unstack("t").sort_index(axis=1)
        frames = []
        for c in COUNT_COLS:
            frames.append(wide[c].reindex(columns=runs).ffill(axis=1).stack().rename(c))
        st = pd.concat(frames, axis=1).reset_index()
        st.columns = ["station", "t", *COUNT_COLS]
        st["ts"] = pd.to_datetime(day + " " + st["t"], format="%Y-%m-%d %H%M")
        out.append(st)
    df = pd.concat(out, ignore_index=True)
    busy = df["fc"] + df["sc"]
    usable = busy + df["fa"] + df["sa"]
    df = df[usable > 0].copy()
    df["util_now"] = busy[usable > 0] / usable[usable > 0]
    return df[["station", "ts", "util_now"]].sort_values(["station", "ts"]).reset_index(drop=True)


def _nearest(base: pd.DataFrame, obs: pd.DataFrame, offset: pd.Timedelta, tol: pd.Timedelta, name: str) -> pd.Series:
    """base 각 행의 (ts + offset)에 가장 가까운 같은 충전소 기록값 (tol 안에 없으면 NaN)."""
    left = base[["station", "ts"]].copy()
    left["key"] = left["ts"] + offset
    left["_i"] = np.arange(len(left))
    right = obs.rename(columns={"ts": "key", "util_now": name})[["station", "key", name]]
    m = pd.merge_asof(left.sort_values("key"), right.sort_values("key"), on="key", by="station",
                      direction="nearest", tolerance=tol)
    return m.sort_values("_i")[name].to_numpy()


def chungbuk_pairs(states: pd.DataFrame) -> pd.DataFrame:
    """45~75분 뒤 기록이 있는 시점만 골라 '현재 → 1시간 뒤' 평가 쌍을 만든다."""
    df = states.copy()
    df[TARGET] = _nearest(df, states, pd.Timedelta(hours=1), pd.Timedelta(minutes=15), "y")
    df = df.dropna(subset=[TARGET])
    for h in LAGS:
        tol = pd.Timedelta(minutes=20 if h < 24 else 45)
        df[f"util_lag_{h}"] = _nearest(df, states, -pd.Timedelta(hours=h), tol, f"l{h}")
    df = add_mean_3h(df)
    df = pd.concat([df, time_features(df["ts"])], axis=1)
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# 3. 학습·평가
# ─────────────────────────────────────────────────────────────
def drop_some_lags(df: pd.DataFrame, rate: float, seed: int = SEED) -> pd.DataFrame:
    """지난 값 일부를 비워, 기록이 띄엄띄엄한 충북에서도 쓸 수 있게 한다."""
    df = df.copy()
    rng = np.random.default_rng(seed)
    for h in LAGS:
        col = f"util_lag_{h}"
        df.loc[rng.random(len(df)) < rate, col] = np.nan
    return add_mean_3h(df)


def scores(y: pd.Series, pred) -> dict:
    pred = np.asarray(pred, dtype=float)
    return {"MAE": round(float(np.mean(np.abs(y - pred))), 4),
            "RMSE": round(float(np.sqrt(np.mean((y - pred) ** 2))), 4)}


def evaluate(name: str, df: pd.DataFrame, model, train_mean: float, hour_mean: pd.Series) -> dict:
    y = df[TARGET]
    res = {
        "n": int(len(df)),
        "model": scores(y, np.clip(model.predict(df[FEATURES]), 0, 1)),
        "baseline_mean": scores(y, np.full(len(y), train_mean)),                    # 평균값 하나로 찍기
        "baseline_persistence": scores(y, df["util_now"]),                          # 지금 값 그대로
        "baseline_hour_mean": scores(y, df["hour"].map(hour_mean).fillna(train_mean)),  # 시간대 평균
    }
    print(f"\n[{name}] n={res['n']:,}")
    for k in ("model", "baseline_mean", "baseline_persistence", "baseline_hour_mean"):
        print(f"  {k:<22} MAE {res[k]['MAE']:.4f}  RMSE {res[k]['RMSE']:.4f}")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", type=Path, default=EXCEL)
    ap.add_argument("--history", type=Path, default=None, help="충북 수집 기록 history 폴더 (선택)")
    ap.add_argument("--out", type=Path, default=MODEL_OUT)
    args = ap.parse_args()

    print("1. 서울 충전 이력 로드·전처리")
    sessions = load_seoul_sessions(args.excel)
    hourly = seoul_hourly(sessions)
    data = seoul_features(hourly)
    print(f"   세션 {len(sessions):,}건 → 충전소 {data['station'].nunique()}곳 × 시간 = {len(data):,}행")

    train = data[data["slot"] < "2025-11-01"]
    valid = data[(data["slot"] >= "2025-11-01") & (data["slot"] < "2025-12-01")]
    test = data[data["slot"] >= "2025-12-01"]

    print("2. LightGBM 학습 (조기 종료는 11월 검증셋으로)")
    model = lgb.LGBMRegressor(n_estimators=2000, learning_rate=0.05, num_leaves=63, min_child_samples=200,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                              random_state=SEED, verbose=-1)
    tr = drop_some_lags(train, LAG_DROP_RATE)
    va = drop_some_lags(valid, LAG_DROP_RATE, seed=SEED + 1)
    model.fit(tr[FEATURES], tr[TARGET], eval_set=[(va[FEATURES], va[TARGET])], eval_metric="l1",
              callbacks=[lgb.early_stopping(100, verbose=False)])
    print(f"   트리 {model.best_iteration_}개에서 멈춤")

    print("3. 평가 (테스트는 여기서 처음 사용)")
    train_mean = float(train[TARGET].mean())
    hour_mean = train.groupby("hour")[TARGET].mean()
    metrics = {"features": FEATURES, "best_iteration": int(model.best_iteration_),
               "split": {"train": "2025-01~10 (서울)", "valid": "2025-11 (서울)", "test": "2025-12 (서울)"}}
    metrics["seoul_test_full"] = evaluate("서울 12월 · 지난 값 모두 있음", test, model, train_mean, hour_mean)
    metrics["seoul_test_sparse"] = evaluate("서울 12월 · 지난 값 일부 없음", drop_some_lags(test, LAG_DROP_RATE, SEED + 2),
                                            model, train_mean, hour_mean)
    if args.history:
        states = chungbuk_states(args.history)
        pairs = chungbuk_pairs(states)
        metrics["chungbuk"] = evaluate("충북 실측 (45~75분 뒤)", pairs, model, train_mean, hour_mean)
        metrics["chungbuk"]["period"] = [str(pairs["ts"].min()), str(pairs["ts"].max())]
        metrics["chungbuk"]["stations"] = int(pairs["station"].nunique())

    imp = pd.Series(model.booster_.feature_importance("gain"), index=FEATURES)
    metrics["importance"] = (imp / imp.sum()).round(3).sort_values(ascending=False).to_dict()
    print("\n피처 중요도:", ", ".join(f"{k} {v:.0%}" for k, v in metrics["importance"].items() if v >= 0.01))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, args.out)
    METRICS_OUT.write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n4. 저장: {args.out.relative_to(ROOT)} / {METRICS_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
