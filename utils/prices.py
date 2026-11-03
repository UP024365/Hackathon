"""
운영사별 실제 충전요금(원/kWh) 및 AI 모델 기반 리워드 산정 모듈

1. data/operator_prices.csv에서 운영사 × 출력 구간 단가를 조회합니다.
2. models/lgbm_congestion_model.pkl을 로드하여 1시간 뒤 예상 이용률을 예측하고,
   예상 혼잡도가 낮을수록 더 큰 리워드(할인율)를 부여합니다. (할증 없음)
"""
from __future__ import annotations

import csv
import os
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import config

PRICE_FILE = config.ROOT / "data" / "operator_prices.csv"
TIER_UPPER = (30, 50, 100, 200, float("inf"))          # 완속 / 중속 / 급속50 / 급속100 / 초급속200
TIER_LABELS = ("완속(30kW 미만)", "중속(30~49kW)", "급속(50~99kW)", "급속(100~199kW)", "초급속(200kW 이상)")
PUBLIC = "기후에너지환경부"

# API의 운영기관명 → 요금표 운영사명 (정규화 후 기준)
ALIASES = {
    "기후부": PUBLIC, "환경부": PUBLIC, "한국환경공단": PUBLIC,
    "한국전력": "한국전력공사",
    "차지비": "GS차지비",
    "LG유플러스": "LG유플러스볼트업", "엘지유플러스": "LG유플러스볼트업",
    "파워큐브코리아": "파워큐브",
    "HUMAXEV": "휴맥스이브이", "휴맥스EV": "휴맥스이브이",
    "이브이시": "이브이시스",
    "파킹클라우드": "아이파킹",
    "스타코프스타코프": "스타코프",
}

# ─────────────────────────────────────────────────────────────
# 1. AI 혼잡도 예측 모델 로딩 및 리워드 계산 함수
# ─────────────────────────────────────────────────────────────
MODEL_PATH = config.ROOT / "models" / "lgbm_congestion_model2.pkl"   # training/train_model.py가 만듦
_MODEL = None
LAGS = (1, 2, 24, 168)   # 학습 때와 같은 '몇 시간 전 이용률' 피처


def get_model():
    """모델이 로컬에 있으면 로드하여 캐시 반환"""
    global _MODEL
    if _MODEL is None and MODEL_PATH.exists():
        try:
            _MODEL = joblib.load(MODEL_PATH)
        except Exception:
            _MODEL = None
    return _MODEL


def predict_utilization_1h_batch(current_util, when: datetime, lags: dict | None = None) -> np.ndarray:
    """
    여러 충전소의 1시간 뒤 예상 이용률(0~1)을 한 번에 예측.
    - current_util: 충전소별 현재 이용률 = 충전중 / (대기 + 충전중)
    - when: 현재 시각 (KST)
    - lags: {1: [...], 2: [...], 24: [...], 168: [...]} 몇 시간 전 이용률. 모르면 생략 (NaN 처리)
    모델 파일이 없으면 현재 이용률을 그대로 돌려준다.
    """
    cur = np.asarray(current_util, dtype=float)
    model = get_model()
    if model is None or cur.size == 0:
        return cur
    n = cur.size
    lags = lags or {}
    df = pd.DataFrame({
        "hour": when.hour,
        "hour_sin": np.sin(2 * np.pi * when.hour / 24),
        "hour_cos": np.cos(2 * np.pi * when.hour / 24),
        "day_of_week": when.weekday(),
        "is_weekend": int(when.weekday() >= 5),
        "is_rush_hour": int(when.hour in (8, 9, 18, 19)),
        "util_now": cur,
    }, index=range(n))
    for h in LAGS:
        df[f"util_lag_{h}"] = np.asarray(lags.get(h, np.full(n, np.nan)), dtype=float)
    df["util_mean_3h"] = df[["util_now", "util_lag_1", "util_lag_2"]].mean(axis=1, skipna=True)
    return np.clip(model.predict(df[list(model.feature_name_)]), 0.0, 1.0)


def predict_utilization_1h(current_util: float, when: datetime, lags: dict | None = None) -> float:
    """충전소 한 곳의 1시간 뒤 예상 이용률."""
    one = {h: [v] for h, v in (lags or {}).items()}
    return float(predict_utilization_1h_batch([current_util], when, one)[0])


def calculate_reward_rate(pred_util: float, default_discount: float = 0.20) -> float:
    """
    1시간 후 예상 이용률에 따른 리워드(할인율) 산정
    - 이용률이 낮아 한산할수록 높은 리워드(할인) 부여
    - 혼잡할 경우 할증 없이 0.0 (기본요금 유지)
    """
    if pred_util < 0.3:
        return 0.30  # 매우 한산: 30% 리워드
    elif pred_util < 0.6:
        return default_discount  # 보통/여유: 기본 리워드 (예: 20%)
    else:
        return 0.0  # 혼잡 예상: 리워드 없음 (할증 배제)


# ─────────────────────────────────────────────────────────────
# 2. 운영사별 요금표 조회 로직
# ─────────────────────────────────────────────────────────────
def normalize(name: str) -> str:
    s = str(name or "")
    s = re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사", "", s)
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"\s+", "", s).upper()
    return ALIASES.get(s, s)


def _num(v: str):
    v = (v or "").strip()
    return float(v) if v and v != "-" else None


@lru_cache(maxsize=1)
def table() -> dict:
    out = {}
    with open(PRICE_FILE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = normalize(r["운영사"])
            out[key] = {
                "name": r["운영사"],
                "member": [_num(r[c]) for c in ("회원_완속", "회원_중속", "회원_급속50", "회원_급속100", "회원_초급속200")],
                "nonmember": [_num(r[c]) for c in ("비회원_완속", "비회원_중속", "비회원_급속50", "비회원_급속100", "비회원_초급속200")],
                "updated": r["갱신일"], "source": r.get("출처", ""),
            }
    for target in set(ALIASES.values()):
        assert normalize(target) in out, f"별칭 대상 '{target}'이 요금표에 없습니다"
    return out


def tier_index(kw: float) -> int:
    for i, up in enumerate(TIER_UPPER):
        if kw < up:
            return i
    return len(TIER_UPPER) - 1


def _pick(values: list, i: int):
    if values[i] is not None:
        return values[i], True
    for d in range(1, len(values)):
        for j in (i - d, i + d):
            if 0 <= j < len(values) and values[j] is not None:
                return values[j], False
    return None, False


def lookup(busi: str, kw: float, member: bool = True) -> tuple[float | None, str, str]:
    t = table()
    i = tier_index(kw if kw and kw > 0 else 7.0)
    kind = "member" if member else "nonmember"
    row = t.get(normalize(busi))
    if row:
        v, exact = _pick(row[kind], i)
        if v is not None:
            return v, ("운영사" if exact else "운영사(인접 구간)"), row["name"]
    if not member:
        return None, "미등재", ""
    return t[normalize(PUBLIC)]["member"][i], "공공 기준(미등재)", PUBLIC


def member_rate(busi: str, kw: float) -> float:
    return lookup(busi, kw, True)[0]


def web_payload() -> dict:
    return {
        "tiers": [u if u != float("inf") else None for u in TIER_UPPER],
        "aliases": ALIASES,
        "public": normalize(PUBLIC),
        "ops": {k: {"n": v["name"], "m": v["member"], "x": v["nonmember"], "u": v["updated"],
                "s": "채비 홈페이지" if v["source"].startswith("채비") else "무공해차 통합누리집"} for k, v in table().items()},
    }