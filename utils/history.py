"""
상태 기록: 수집할 때마다 충전소별 급속·완속의 대기/충전 중/고장 대수를 쌓아 둡니다.

저장 형식 (config.HISTORY_DIR 아래, 시각은 한국 시간)
  {YYYY-MM-DD}/{zscode}.csv   t,statId,fa,fc,ff,sa,sc,sf
                              t=HHMM, f*=급속 a(대기)/c(충전 중)/f(고장), s*=완속
                              그날 첫 수집은 모든 충전소를, 이후에는 상태가 바뀐 충전소만 덧붙입니다.
                              수집할 때마다 statId가 "__run__"인 표시 행을 하나 남겨, 변화가 없던 수집도 시각이 기록됩니다.
                              (일반 텍스트에 덧붙이므로 git이 차이만 저장해 저장소가 크게 불어나지 않음)
  stations/{zscode}.json      statId → 이름·주소·좌표·운영기관·시설·주차·기본 단가·최대 출력

이용자 제한(limitYn=Y)과 삭제된 충전기는 기록하지 않습니다 (logic.prepare_chargers 기본값과 같음).
기록은 로컬 폴더에서 읽고, 없으면 환경변수 HISTORY_URL(예: GitHub history 브랜치의 raw 주소)에서 읽습니다.
"""
from __future__ import annotations

import io
import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

import config
from utils import logic

COUNT_COLS = ("fa", "fc", "ff", "sa", "sc", "sf")
RUN_MARK = "__run__"
_lock = threading.Lock()
_remote_cache: dict[str, tuple[float, bytes | None]] = {}
REMOTE_TTL_SEC = 300


def _base(base: Path | str | None) -> Path:
    return Path(base) if base else config.HISTORY_DIR


# ─────────────────────────────────────────────────────────────
# 쓰기
# ─────────────────────────────────────────────────────────────
def station_counts(rows: list[dict]) -> tuple[pd.DataFrame, dict]:
    """API 행 → (충전소별 대수 표, 충전소 정보)."""
    ch = logic.prepare_chargers(rows)
    if ch.empty:
        return pd.DataFrame(columns=["statId", *COUNT_COLS]), {}
    ch = ch[ch["speed"].isin(["급속", "완속"])]
    g = ch.groupby(["statId", "speed"]).agg(
        a=("is_avail", "sum"), c=("is_charging", "sum"), f=("is_fault", "sum"),
        base=("base_rate", "max"), kw=("output_kw", "max"),
    ).unstack("speed")
    out = pd.DataFrame(index=g.index)
    for speed, p in (("급속", "f"), ("완속", "s")):
        for k in "acf":
            out[p + k] = g[(k, speed)].fillna(0).astype(int) if (k, speed) in g.columns else 0
    info = ch.groupby("statId").agg(n=("statNm", "first"), a=("full_addr", "first"), la=("lat", "first"),
                                    ln=("lng", "first"), b=("busiNm", "first"), f=("facility", "first"),
                                    p=("parking", "first"))
    meta = {}
    for sid, r in info.iterrows():
        m = {"n": r["n"], "a": r["a"], "la": round(float(r["la"]), 6), "ln": round(float(r["ln"]), 6),
             "b": r["b"], "f": r["f"], "p": r["p"]}
        for speed, p in (("급속", "f"), ("완속", "s")):
            if ("base", speed) in g.columns and pd.notna(g.at[sid, ("base", speed)]):
                m[p + "b"] = float(g.at[sid, ("base", speed)])
                m[p + "k"] = float(g.at[sid, ("kw", speed)])
        meta[sid] = m
    return out.reset_index(), meta


def _read_day_csv(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw), dtype={"t": str, "statId": str})
    df["t"] = df["t"].str.zfill(4)
    return df


def _state_at(data: pd.DataFrame, t: str) -> pd.DataFrame:
    """t 시각까지의 기록을 이어 붙여 충전소별 마지막 상태를 복원."""
    upto = data[data["t"] <= t]
    return upto.groupby("statId", sort=False)[list(COUNT_COLS)].last().reset_index()


def append(region: str, rows: list[dict], when: datetime, base: Path | str | None = None) -> int:
    """한 시·군의 현재 상태를 기록에 덧붙임 (바뀐 충전소만). 현재 충전소 수를 돌려줌."""
    df, meta = station_counts(rows)
    if df.empty:
        return 0
    code = config.REGIONS[region]["code"]
    root = _base(base)
    day_dir = root / when.strftime("%Y-%m-%d")
    st_dir = root / "stations"
    t = when.strftime("%H%M")
    with _lock:
        day_dir.mkdir(parents=True, exist_ok=True)
        st_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{code}.csv"
        header = not path.exists()
        changed = df
        if not header:
            old = _read_day_csv(path.read_bytes())
            prev = _state_at(old[old["statId"] != RUN_MARK], "9999").set_index("statId")
            cur = df.set_index("statId")[list(COUNT_COLS)]
            before = prev.reindex(cur.index)
            diff = before.isna().any(axis=1) | (cur != before.fillna(-1)).any(axis=1)
            changed = cur[diff].reset_index()
        mark = pd.DataFrame([{"statId": RUN_MARK, **{c: 0 for c in COUNT_COLS}}])
        out = pd.concat([mark, changed[["statId", *COUNT_COLS]]], ignore_index=True)
        out.insert(0, "t", t)
        with open(path, "a", encoding="utf-8", newline="") as f:
            out.to_csv(f, index=False, header=header)
        mpath = st_dir / f"{code}.json"
        old = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {}
        old.update(meta)
        mpath.write_text(json.dumps(old, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(df)


# ─────────────────────────────────────────────────────────────
# 읽기 (로컬 → 원격 HISTORY_URL 순)
# ─────────────────────────────────────────────────────────────
def _read_bytes(rel: str, base: Path | str | None = None) -> bytes | None:
    path = _base(base) / rel
    if path.exists():
        return path.read_bytes()
    url = os.getenv("HISTORY_URL", "").rstrip("/")
    if not url or base:
        return None
    hit = _remote_cache.get(rel)
    if hit and time.time() - hit[0] < REMOTE_TTL_SEC:
        return hit[1]
    try:
        r = requests.get(f"{url}/{rel}", timeout=15)
        data = r.content if r.status_code == 200 else None
    except requests.RequestException:
        data = hit[1] if hit else None
    _remote_cache[rel] = (time.time(), data)
    return data


def load_day(region: str, day: str, base: Path | str | None = None) -> pd.DataFrame:
    """그날 기록 원본 (수집 표시 행 포함)."""
    raw = _read_bytes(f"{day}/{config.REGIONS[region]['code']}.csv", base)
    if not raw:
        return pd.DataFrame(columns=["t", "statId", *COUNT_COLS])
    return _read_day_csv(raw)


def load_meta(region: str, base: Path | str | None = None) -> dict:
    raw = _read_bytes(f"stations/{config.REGIONS[region]['code']}.json", base)
    return json.loads(raw) if raw else {}


def latest_per_hour(df: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """시(0~23)마다 그 시간 마지막 수집 시각의 전체 상태 (t, statId, 대수)."""
    if df.empty:
        return {}
    runs = df.loc[df["statId"] == RUN_MARK, "t"]
    data = df[df["statId"] != RUN_MARK]
    if runs.empty:
        runs = data["t"]
    last_t = runs.groupby(runs.str[:2].astype(int)).max()
    out = {}
    for h, t in last_t.items():
        state = _state_at(data, t)
        state.insert(0, "t", t)
        out[int(h)] = state
    return out


def congested_count(slot: pd.DataFrame) -> int:
    """혼잡 = 사용 가능한 충전기가 있고 그중 대기가 0대 (할인 기준값과 무관)."""
    avail = slot["fa"] + slot["sa"]
    charging = slot["fc"] + slot["sc"]
    return int(((charging > 0) & (avail == 0)).sum())


def pattern(region: str, day: str, days: int = config.HISTORY_PATTERN_DAYS, base: Path | str | None = None) -> dict:
    """지난 n일(오늘 제외) 같은 시간대의 평균 혼잡 거점 수."""
    d0 = date.fromisoformat(day)
    sums, cnts, used = [0.0] * 24, [0] * 24, 0
    for i in range(1, days + 1):
        per = latest_per_hour(load_day(region, (d0 - timedelta(days=i)).isoformat(), base))
        if not per:
            continue
        used += 1
        for h, slot in per.items():
            sums[h] += congested_count(slot)
            cnts[h] += 1
    return {"days": used, "congested": [round(sums[h] / cnts[h], 1) if cnts[h] else None for h in range(24)]}


def day_payload(region: str, day: str, base: Path | str | None = None) -> dict:
    """사이트가 쓰는 하루치 기록: 시간대별 마지막 상태 + 충전소 정보 + 지난 며칠 평균."""
    per = latest_per_hour(load_day(region, day, base))
    hours, used = {}, set()
    for h, slot in per.items():
        rows = slot[["statId", *COUNT_COLS]].to_numpy()
        hours[str(h)] = {"t": slot["t"].iat[0], "s": {r[0]: [int(x) for x in r[1:]] for r in rows}}
        used.update(slot["statId"])
    meta = load_meta(region, base)
    return {"region": region, "date": day, "hours": hours,
            "stations": {k: v for k, v in meta.items() if k in used},
            "pattern": pattern(region, day, base=base)}
