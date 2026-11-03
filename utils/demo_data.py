"""
오프라인 시연용 '가상' 데이터 생성기.

API 키가 없거나 발표장 네트워크가 끊겼을 때 화면 흐름을 보여주기 위한 용도입니다.
충전소명·운영사·좌표는 모두 임의로 만든 값이며 실제 충전소와 무관합니다.
(실제 데이터로 오프라인 시연하려면 scripts/save_snapshot.py 로 스냅샷을 저장하세요.)
"""
from __future__ import annotations

import json
import zlib
from datetime import datetime, timedelta

import numpy as np

import config

_DEMO_OPERATORS = ["데모운영사A", "데모운영사B", "데모운영사C", "데모운영사D", "데모운영사E"]
_KIND_DETAILS = ["A001", "B001", "B002", "C001", "D001", "E001", "F001", "G001", "I001", "J001"]
_TYPE_POOL = [("02", 7), ("02", 7), ("02", 11), ("04", 50), ("04", 100), ("06", 50), ("04", 200), ("07", 43)]


def _region_polygons(code: str) -> list[list[tuple[float, float]]]:
    """해당 시군 경계의 외곽 링 목록 [(lng, lat), ...]."""
    try:
        gj = json.loads(config.BOUNDARY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    for f in gj["features"]:
        if f["properties"]["zscode"] == code:
            geom = f["geometry"]
            polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
            return [[tuple(p) for p in poly[0]] for poly in polys]
    return []


def _inside(lng: float, lat: float, ring: list[tuple[float, float]]) -> bool:
    hit = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi:
            hit = not hit
        j = i
    return hit


def generate(region_name: str, *, seed: int | None = None, now: datetime | None = None) -> list[dict]:
    region = config.REGIONS[region_name]
    code = region["code"]
    rng = np.random.default_rng(seed if seed is not None else zlib.crc32(code.encode()))
    now = now or datetime.now()
    rings = _region_polygons(code)
    c_lat, c_lng = region["center"]

    is_city = region_name in ("청주시", "충주시", "제천시")
    n_stations = {"청주시": 160, "충주시": 70, "제천시": 55}.get(region_name, 32)
    spread = 0.045 if region_name == "청주시" else (0.035 if is_city else 0.03)

    # 시간대에 따라 이용률을 다르게 (저녁·출근 시간대 혼잡)
    busy = {7: .3, 8: .35, 9: .3, 12: .3, 13: .3, 18: .4, 19: .45, 20: .4, 21: .35}.get(now.hour, .2)

    rows: list[dict] = []
    for s in range(n_stations):
        for _ in range(50):  # 시군 경계 안쪽 좌표가 나올 때까지
            lat = c_lat + rng.normal(0, spread)
            lng = c_lng + rng.normal(0, spread * 1.2)
            if not rings or any(_inside(lng, lat, r) for r in rings):
                break
        stat_id = f"DM{code[-3:]}{s:04d}"
        n_chg = int(rng.choice([1, 2, 2, 3, 4, 4, 6, 8]))
        hotspot = rng.random() < 0.25        # 일부 거점은 수요 집중
        operator = str(rng.choice(_DEMO_OPERATORS))
        kind_detail = str(rng.choice(_KIND_DETAILS))
        parking = str(rng.choice(["Y", "N"]))
        for c in range(n_chg):
            ctype, kw = _TYPE_POOL[int(rng.integers(len(_TYPE_POOL)))]
            p = min(busy + (0.45 if hotspot else 0), 0.95)
            r = rng.random()
            stat = "3" if r < p else ("2" if r < 0.94 else str(rng.choice(["1", "4", "5", "9"])))
            rows.append({
                "statNm": f"[데모] {region_name} 거점 {s + 1:03d}",
                "statId": stat_id, "chgerId": f"{c + 1:02d}", "chgerType": ctype,
                "addr": f"충청북도 {region_name} (가상 주소)", "addrDetail": "",
                "lat": f"{lat:.6f}", "lng": f"{lng:.6f}",
                "useTime": "24시간 이용가능", "busiNm": operator, "stat": stat,
                "statUpdDt": (now - timedelta(minutes=int(rng.integers(1, 240)))).strftime("%Y%m%d%H%M%S"),
                "output": str(kw), "zcode": config.SIDO_CODE, "zscode": code,
                "kind": kind_detail[:2], "kindDetail": kind_detail, "parkingFree": parking,
                "limitYn": "Y" if rng.random() < 0.08 else "N", "delYn": "N",
            })
    return rows
