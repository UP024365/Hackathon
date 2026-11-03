"""
핵심 로직: 충전기 데이터 정제 → 충전소 단위 집계 → 혼잡도 판정 → 리워드 동적 요금 → 대체 거점 추천.
(혼잡 시 할증은 배제하고, AI 예측 모델 및 여유 상태에 따른 리워드/할인만 적용)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import config
from config import PricingPolicy
from utils import prices

KST = timezone(timedelta(hours=9))
SPEEDS = ("급속", "완속")


# ─────────────────────────────────────────────────────────────
# 1. 단건 규칙 함수
# ─────────────────────────────────────────────────────────────
def base_rate_for_output(kw: float) -> float:
    """충전기 출력(kW) 구간별 공공 단가(원/kWh). 운영사 요금표에 없는 충전기의 기준값."""
    for upper, rate in config.BASE_RATE_TIERS:
        if kw < upper:
            return rate
    return config.BASE_RATE_TIERS[-1][1]


def classify_speed(chger_type: str, output_kw: float | None) -> str:
    if chger_type in config.SLOW_TYPES:
        return "완속"
    if chger_type in config.FAST_TYPES:
        return "급속"
    if output_kw and output_kw > 0:
        return "급속" if output_kw >= config.FAST_OUTPUT_KW else "완속"
    return "기타"


def classify_status(avail: int, charging: int, fault: int, threshold: float) -> str:
    """대기/충전중/고장 대수로 혼잡 상태 판정. 분모는 '실제 사용 가능한(대기+충전중)' 충전기."""
    usable = avail + charging
    if usable == 0:
        return "불가" if fault > 0 else "미확인"
    if avail / usable >= threshold:
        return "여유"
    if avail > 0:
        return "보통"
    return "혼잡"


def congestion_multiplier(status: str, policy: PricingPolicy, predicted_util: float | None = None) -> float:
    """
    할증 없는 리워드(할인) 전용 배수 계산:
    - AI 예측 이용률이 주어지면 prices.calculate_reward_rate 기반 할인 적용
    - 없을 경우 상태 판정("여유")에 따라 할인 적용, "혼잡"이어도 할증 없이 1.0(기본 요금) 유지
    """
    if predicted_util is not None:
        discount = prices.calculate_reward_rate(predicted_util, policy.discount_rate)
        return 1.0 - discount

    return {
        "여유": 1.0 - policy.discount_rate,
        "보통": 1.0,
        "혼잡": 1.0,  # 할증 배제: 만차/혼잡 시에도 기본 단가(1.0) 유지
    }.get(status, np.nan)


def time_multiplier(hour: int, policy: PricingPolicy) -> tuple[float, str]:
    if not policy.use_time_of_day:
        return 1.0, "시간대 가중치 미적용"
    for band in policy.time_bands:
        in_band = (band.start_hour <= hour < band.end_hour) if band.start_hour < band.end_hour \
            else (hour >= band.start_hour or hour < band.end_hour)
        if in_band:
            return band.multiplier, band.label
    return 1.0, "기본 시간대"


# ─────────────────────────────────────────────────────────────
# 2. 충전기(행) 정제
# ─────────────────────────────────────────────────────────────
_TEXT_COLS = ["statId", "chgerId", "statNm", "addr", "addrDetail", "busiNm", "chgerType", "stat",
              "kind", "kindDetail", "parkingFree", "limitYn", "delYn", "statUpdDt", "useTime"]


def _clean_text(s: pd.Series) -> pd.Series:
    s = s.fillna("").astype(str).str.strip()
    return s.mask(s.str.lower().isin(["null", "none", "nan"]), "")


def prepare_chargers(rows: list[dict], *, exclude_limited: bool = config.EXCLUDE_LIMITED_DEFAULT) -> pd.DataFrame:
    """API 원본 행 → 분석용 충전기 DataFrame."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in _TEXT_COLS:
        df[col] = _clean_text(df[col]) if col in df else ""
    for col in ("lat", "lng", "output"):
        df[col] = pd.to_numeric(df[col], errors="coerce") if col in df else np.nan

    df = df[df["delYn"].str.upper() != "Y"]                                   # 삭제된 충전기 제외
    df = df[df["lat"].between(33, 39) & df["lng"].between(124, 132)]          # 좌표 이상치 제외
    df = df.drop_duplicates(subset=["statId", "chgerId"])
    df["limited"] = df["limitYn"].str.upper() == "Y"
    if exclude_limited:
        df = df[~df["limited"]]

    df["chgerType"] = df["chgerType"].str.zfill(2).where(df["chgerType"] != "", "")
    df["speed"] = [classify_speed(t, o) for t, o in zip(df["chgerType"], df["output"])]
    df["output_kw"] = df["output"].where(df["output"] > 0, df["speed"].map(config.DEFAULT_OUTPUT_KW))

    df["is_avail"] = df["stat"].isin(config.STAT_AVAILABLE)
    df["is_charging"] = df["stat"].isin(config.STAT_CHARGING)
    df["is_fault"] = df["stat"].isin(config.STAT_FAULT)

    kind_code = df["kind"].where(df["kind"] != "", df["kindDetail"].str[:2])
    df["facility"] = kind_code.map(config.KIND_GROUPS).fillna("기타시설")
    df["parking"] = df["parkingFree"].str.upper().map({"Y": "무료", "N": "유료"}).fillna("현장확인")
    df["statNm"] = df["statNm"].replace("", "이름 없음")
    df["busiNm"] = df["busiNm"].replace("", "운영기관 미표기")

    found = [prices.lookup(b, kw) for b, kw in zip(df["busiNm"], df["output_kw"])]
    df["base_rate"] = [f[0] for f in found]
    df["price_src"] = [f[1] for f in found]
    df["nonmember_rate"] = [prices.lookup(b, kw, member=False)[0] for b, kw in zip(df["busiNm"], df["output_kw"])]
    df["full_addr"] = (df["addr"] + " " + df["addrDetail"]).str.strip()
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# 3. 충전소 단위 집계 + 동적 요금
# ─────────────────────────────────────────────────────────────
def _status_vec(avail: pd.Series, charging: pd.Series, fault: pd.Series, threshold: float) -> np.ndarray:
    usable = avail + charging
    ratio = np.where(usable > 0, avail / usable.where(usable > 0, 1), 0.0)
    return np.select(
        [(usable == 0) & (fault > 0), usable == 0, ratio >= threshold, avail > 0],
        ["불가", "미확인", "여유", "보통"],
        default="혼잡",
    )


def _price(base: pd.Series, status: pd.Series, policy: PricingPolicy, tod: float) -> pd.Series:
    mult = status.map(lambda s: congestion_multiplier(s, policy))
    return (base * mult * tod).round(1)


def summarize_stations(chargers: pd.DataFrame, policy: PricingPolicy = config.DEFAULT_POLICY,
                       now: datetime | None = None) -> pd.DataFrame:
    """충전기 DataFrame → 충전소(statId) 1행 요약 + 상태·리워드 적용 요금."""
    if chargers.empty:
        return pd.DataFrame()
    now = now or datetime.now(KST)
    tod, tod_label = time_multiplier(now.hour, policy)

    g = chargers.groupby("statId", sort=False)
    st = g.agg(
        statNm=("statNm", "first"), addr=("full_addr", "first"), lat=("lat", "first"), lng=("lng", "first"),
        busiNm=("busiNm", "first"), facility=("facility", "first"), parking=("parking", "first"),
        useTime=("useTime", "first"), limited=("limited", "max"),
        total=("stat", "size"), avail=("is_avail", "sum"), charging=("is_charging", "sum"),
        fault=("is_fault", "sum"), max_kw=("output_kw", "max"), updated=("statUpdDt", "max"),
    )

    per = chargers.groupby(["statId", "speed"]).agg(
        total=("stat", "size"), avail=("is_avail", "sum"), charging=("is_charging", "sum"),
        fault=("is_fault", "sum"), base=("base_rate", "max"),
    ).unstack("speed")

    for speed, prefix in (("급속", "fast"), ("완속", "slow")):
        for col in ("total", "avail", "charging", "fault"):
            st[f"{prefix}_{col}"] = per[(col, speed)].reindex(st.index).fillna(0).astype(int) \
                if (col, speed) in per.columns else 0
        base = per[("base", speed)].reindex(st.index) if ("base", speed) in per.columns else np.nan
        status = pd.Series(_status_vec(st[f"{prefix}_avail"], st[f"{prefix}_charging"],
                                       st[f"{prefix}_fault"], policy.discount_threshold), index=st.index)
        status = status.where(st[f"{prefix}_total"] > 0, "")
        st[f"{prefix}_status"] = status
        st[f"{prefix}_base"] = base
        st[f"{prefix}_price"] = _price(pd.Series(base, index=st.index), status, policy, tod)

    for col in ("total", "avail", "charging", "fault"):
        st[col] = st[col].astype(int)
    st["status"] = _status_vec(st["avail"], st["charging"], st["fault"], policy.discount_threshold)
    st["avail_ratio"] = (st["avail"] / (st["avail"] + st["charging"]).replace(0, np.nan)).round(3)
    st["price"] = st["fast_price"].fillna(st["slow_price"])
    st["tod_multiplier"] = tod
    st["tod_label"] = tod_label
    return st.reset_index()


# ─────────────────────────────────────────────────────────────
# 4. 혼잡·불가 거점 → 반경 내 '여유(할인/리워드)' 거점 추천
# ─────────────────────────────────────────────────────────────
def haversine_km(lat1, lng1, lat2, lng2):
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def recommend_alternatives(stations: pd.DataFrame, radius_km: float = config.REDIRECT_RADIUS_KM,
                           source_status=("혼잡", "불가")) -> pd.DataFrame:
    out = stations.copy()
    for col in ("alt_statId", "alt_name", "alt_speed"):
        out[col] = ""
    for col in ("alt_lat", "alt_lng", "alt_dist_km", "alt_price", "alt_base"):
        out[col] = np.nan
    if out.empty:
        return out

    need = np.where(out["fast_total"] > 0, "fast", "slow")
    for prefix, label in (("fast", "급속"), ("slow", "완속")):
        src = out.index[out["status"].isin(source_status) & (need == prefix)]
        dst = out.index[out[f"{prefix}_status"] == "여유"]
        if len(src) == 0 or len(dst) == 0:
            continue
        d = haversine_km(out.loc[src, "lat"].to_numpy()[:, None], out.loc[src, "lng"].to_numpy()[:, None],
                         out.loc[dst, "lat"].to_numpy()[None, :], out.loc[dst, "lng"].to_numpy()[None, :])
        d[src.to_numpy()[:, None] == dst.to_numpy()[None, :]] = np.inf
        nearest = d.argmin(axis=1)
        dist = d[np.arange(len(src)), nearest]
        ok = dist <= radius_km
        pick, rows = dst[nearest[ok]], src[ok]
        out.loc[rows, "alt_statId"] = out.loc[pick, "statId"].to_numpy()
        out.loc[rows, "alt_name"] = out.loc[pick, "statNm"].to_numpy()
        out.loc[rows, "alt_speed"] = label
        out.loc[rows, "alt_lat"] = out.loc[pick, "lat"].to_numpy()
        out.loc[rows, "alt_lng"] = out.loc[pick, "lng"].to_numpy()
        out.loc[rows, "alt_price"] = out.loc[pick, f"{prefix}_price"].to_numpy()
        out.loc[rows, "alt_base"] = out.loc[pick, f"{prefix}_base"].to_numpy()
        out.loc[rows, "alt_dist_km"] = dist[ok].round(2)
    return out


# ─────────────────────────────────────────────────────────────
# 5. 지역 요약 지표
# ─────────────────────────────────────────────────────────────
def region_kpis(stations: pd.DataFrame) -> dict:
    if stations.empty:
        return {}
    counts = stations["status"].value_counts()
    avail, charging = int(stations["avail"].sum()), int(stations["charging"].sum())
    need = stations["status"].isin(["혼잡", "불가"])
    congested = stations["status"] == "혼잡"
    has_alt = stations.get("alt_statId", pd.Series("", index=stations.index)).fillna("") != ""
    return {
        "stations": len(stations),
        "chargers": int(stations["total"].sum()),
        "fast_chargers": int(stations["fast_total"].sum()),
        "slow_chargers": int(stations["slow_total"].sum()),
        "available": avail,
        "charging": charging,
        "fault": int(stations["fault"].sum()),
        "idle_rate": avail / (avail + charging) if (avail + charging) else np.nan,
        "status_counts": {k: int(counts.get(k, 0)) for k in config.STATUS_META},
        "congested": int(congested.sum()),
        "needs_redirect": int(need.sum()),
        "redirectable": int((need & has_alt).sum()),
        "congested_redirectable": int((congested & has_alt).sum()),
    }