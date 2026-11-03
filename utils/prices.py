"""
운영사별 실제 충전요금(원/kWh) — data/operator_prices.csv

공공 API(getChargerInfo)에는 요금이 없으므로, 무공해차 통합누리집(ev.or.kr) '사업자별 요금 매트릭스'의
운영사 × 출력 구간 단가를 충전기의 운영기관(busiNm)·출력(output)과 연결한다.
통합누리집도 '충전소 요금은 운영 사업자의 용량 구간별 단가'로 안내하므로, 이 연결이 곧 충전소별 요금이다.

찾는 순서
  1) 운영사의 해당 출력 구간 단가             → source "운영사"
  2) 그 구간이 비어 있으면 같은 운영사의 가장 가까운 구간 → source "운영사(인접 구간)"
  3) 운영사가 표에 없거나 단가가 하나도 없으면 공공(기후에너지환경부) 단가 → source "공공 기준(미등재)"
"""
from __future__ import annotations

import csv
import re
from functools import lru_cache

import config

PRICE_FILE = config.ROOT / "data" / "operator_prices.csv"
TIER_UPPER = (30, 50, 100, 200, float("inf"))          # 완속 / 중속 / 급속50 / 급속100 / 초급속200
TIER_LABELS = ("완속(30kW 미만)", "중속(30~49kW)", "급속(50~99kW)", "급속(100~199kW)", "초급속(200kW 이상)")
PUBLIC = "기후에너지환경부"

# API의 운영기관명 → 요금표 운영사명 (정규화 후 기준). 표기만 다른 같은 회사.
ALIASES = {
    "기후부": PUBLIC, "환경부": PUBLIC, "한국환경공단": PUBLIC,
    "한국전력": "한국전력공사",
    "차지비": "GS차지비",
    "LG유플러스": "LG유플러스볼트업", "엘지유플러스": "LG유플러스볼트업",
    "파워큐브코리아": "파워큐브",
    "HUMAXEV": "휴맥스이브이", "휴맥스EV": "휴맥스이브이",
    "이브이시": "이브이시스",
    "파킹클라우드": "아이파킹",          # 아이파킹 앱·브랜드 운영사 (SK E&S 자회사)
    "스타코프스타코프": "스타코프",
}


def normalize(name: str) -> str:
    s = str(name or "")
    s = re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사", "", s)
    s = re.sub(r"\([^)]*\)", "", s)          # 파워큐브(SP), 타디스테크놀로지(evPlug)
    s = re.sub(r"\s+", "", s).upper()
    return ALIASES.get(s, s)


def _num(v: str):
    v = (v or "").strip()
    return float(v) if v and v != "-" else None


@lru_cache(maxsize=1)
def table() -> dict:
    """{정규화 운영사명: {"name", "member": [5], "nonmember": [5], "updated", "source"}}"""
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
    # 별칭 대상이 표에 있는지 확인 (오타 방지)
    for target in set(ALIASES.values()):
        assert normalize(target) in out, f"별칭 대상 '{target}'이 요금표에 없습니다"
    return out


def tier_index(kw: float) -> int:
    for i, up in enumerate(TIER_UPPER):
        if kw < up:
            return i
    return len(TIER_UPPER) - 1


def _pick(values: list, i: int):
    """i 구간 단가. 비어 있으면 가장 가까운 구간(같은 거리면 낮은 구간)."""
    if values[i] is not None:
        return values[i], True
    for d in range(1, len(values)):
        for j in (i - d, i + d):
            if 0 <= j < len(values) and values[j] is not None:
                return values[j], False
    return None, False


def lookup(busi: str, kw: float, member: bool = True) -> tuple[float | None, str, str]:
    """(단가, 출처 구분, 요금표 운영사명). 비회원가는 미등재면 None."""
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
    """웹 페이지에 넣을 요금표 (JS에서 같은 규칙으로 찾도록 별칭·구간도 함께)."""
    return {
        "tiers": [u if u != float("inf") else None for u in TIER_UPPER],
        "aliases": ALIASES,
        "public": normalize(PUBLIC),
        "ops": {k: {"n": v["name"], "m": v["member"], "x": v["nonmember"], "u": v["updated"],
                "s": "채비 홈페이지" if v["source"].startswith("채비") else "무공해차 통합누리집"} for k, v in table().items()},
    }
