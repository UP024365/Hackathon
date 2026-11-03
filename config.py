"""
⚙️ 프로젝트 설정값 — 변수 튜닝은 이 파일 한 곳에서 합니다.

대회마다 정책 가정(할인율·임계값·시간대 가중치 등)을 바꿔 실험할 수 있도록
코드 곳곳에 흩어져 있던 숫자를 모두 이곳으로 모았습니다.
사이드바 슬라이더의 '초기값'도 여기서 읽어 갑니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
BOUNDARY_FILE = DATA_DIR / "chungbuk_sigungu.geojson"

# ─────────────────────────────────────────────────────────────
# 1. 데이터 소스 (한국환경공단 전기자동차 충전소 정보 API)
# ─────────────────────────────────────────────────────────────
API_URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerInfo"
SIDO_CODE = "43"          # 충청북도
PAGE_SIZE = 2000          # 1회 요청 행 수 (API 허용 범위 10~9999)
MAX_PAGES = 30            # 무한 루프 방지용 상한 (2000 × 30 = 6만 행)
REQUEST_TIMEOUT = 15      # 초
CACHE_TTL_SEC = 300       # 실시간 API 결과 캐시 시간(초)

# 상태 기록(시간대별 실측). 서버를 켜 두거나 scripts/collect.py, GitHub Actions로 쌓습니다.
HISTORY_DIR = DATA_DIR / "history"
COLLECT_INTERVAL_MIN = 10     # 서버 자동 수집 주기(분). 0이면 끔. 환경변수 COLLECT_INTERVAL_MIN로 덮어씀
COLLECT_PAGE_SIZE = 9999      # 충북 전체를 적은 호출로 받기 위한 큰 페이지 (API 최대 9999)
HISTORY_PATTERN_DAYS = 7      # 시간대별 평균 혼잡 패턴에 쓸 지난 날 수

# 충북 11개 시·군. code: API zscode / fallback: code로 조회가 비면 시도할 하위 구 코드
# center: 지도·데모데이터용 시청/군청 인근 좌표
REGIONS: dict[str, dict] = {
    "청주시": {"code": "43110", "fallback": ["43111", "43112", "43113", "43114"], "center": (36.6424, 127.4890)},
    "충주시": {"code": "43130", "fallback": [], "center": (36.9910, 127.9259)},
    "제천시": {"code": "43150", "fallback": [], "center": (37.1326, 128.1910)},
    "보은군": {"code": "43720", "fallback": [], "center": (36.4894, 127.7295)},
    "옥천군": {"code": "43730", "fallback": [], "center": (36.3064, 127.5713)},
    "영동군": {"code": "43740", "fallback": [], "center": (36.1750, 127.7834)},
    "증평군": {"code": "43745", "fallback": [], "center": (36.7853, 127.5815)},
    "진천군": {"code": "43750", "fallback": [], "center": (36.8554, 127.4356)},
    "괴산군": {"code": "43760", "fallback": [], "center": (36.8153, 127.7866)},
    "음성군": {"code": "43770", "fallback": [], "center": (36.9401, 127.6905)},
    "단양군": {"code": "43800", "fallback": [], "center": (36.9845, 128.3655)},
}

# ─────────────────────────────────────────────────────────────
# 2. API 코드표 (※ 공공데이터포털 활용가이드 최신본으로 최종 확인 권장)
# ─────────────────────────────────────────────────────────────
CHARGER_TYPES = {
    "01": "DC차데모", "02": "AC완속", "03": "DC차데모+AC3상", "04": "DC콤보",
    "05": "DC차데모+DC콤보", "06": "DC차데모+AC3상+DC콤보", "07": "AC3상",
    "08": "DC콤보(완속)", "09": "NACS", "10": "DC콤보+NACS",
}
SLOW_TYPES = {"02", "08"}
FAST_TYPES = set(CHARGER_TYPES) - SLOW_TYPES
FAST_OUTPUT_KW = 30.0     # chgerType이 미상일 때 출력(kW)으로 급속 여부 판정

STAT_CODES = {"1": "통신이상", "2": "충전대기", "3": "충전중", "4": "운영중지", "5": "점검중", "9": "상태미확인"}
STAT_AVAILABLE = {"2"}
STAT_CHARGING = {"3"}
STAT_FAULT = {"1", "4", "5"}

# 시설 대분류(kind). kind가 비어 있으면 kindDetail 앞 2자리로 판정
KIND_GROUPS = {
    "A0": "공공시설", "B0": "주차시설", "C0": "휴게시설", "D0": "관광시설", "E0": "상업시설",
    "F0": "차량정비시설", "G0": "기타시설", "H0": "공동주택시설", "I0": "근린생활시설", "J0": "교육문화시설",
}

# ─────────────────────────────────────────────────────────────
# 3. 공공 충전요금 — 충전기 출력(kW) 구간별 (원/kWh)
#    기후에너지환경부 고시, 2026.8.1 시행 (무공해차 통합누리집 사업자별 요금 매트릭스 2026-07-31 갱신).
#    실제 기본 단가는 운영사별 요금표(data/operator_prices.csv, utils/prices.py)를 쓰고,
#    이 표는 운영사 요금이 등재되지 않은 충전기에만 씁니다. (상한 kW 미만, 단가)
# ─────────────────────────────────────────────────────────────
BASE_RATE_TIERS: list[tuple[float, float]] = [
    (30, 295.0),
    (50, 307.2),
    (100, 325.6),
    (200, 348.4),
    (float("inf"), 393.1),
]
DEFAULT_OUTPUT_KW = {"급속": 50.0, "완속": 7.0, "기타": 7.0}  # output 값이 없을 때 가정


# ─────────────────────────────────────────────────────────────
# 4. 동적 요금 정책 (사이드바에서 실시간 조정 가능 — 여기 값은 초기값)
# ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TimeBand:
    start_hour: int       # 포함
    end_hour: int         # 미포함 (start > end 이면 자정을 넘는 구간)
    multiplier: float
    label: str


@dataclass(frozen=True)
class PricingPolicy:
    discount_threshold: float = 0.5   # 대기 충전기 비율이 이 값 이상이면 '여유' → 할인
    discount_rate: float = 0.20       # 여유 거점 할인율
    surcharge_rate: float = 0.15      # 만차(대기 0대) 거점 할증율
    use_time_of_day: bool = False     # 시간대 가중치 적용 여부
    # ⚠️ 아래 시간대·배수는 예시 가정값입니다 (태양광 발전 피크 할인 / 저녁 수요 피크 할증 시나리오).
    time_bands: tuple[TimeBand, ...] = field(default_factory=lambda: (
        TimeBand(11, 14, 0.90, "주간 태양광 피크 할인"),
        TimeBand(18, 22, 1.05, "저녁 수요 피크 할증"),
    ))


DEFAULT_POLICY = PricingPolicy()

# 혼잡 거점 → 대체 거점 추천 반경(km)
REDIRECT_RADIUS_KM = 3.0

# 이용자 제한(limitYn=Y, 예: 아파트 입주민 전용) 충전소를 기본으로 제외할지
EXCLUDE_LIMITED_DEFAULT = True

# ─────────────────────────────────────────────────────────────
# 5. 화면 표시용 상태 정의 (순서 = 범례·차트 순서)
# ─────────────────────────────────────────────────────────────
STATUS_META = {
    # 발산형 팔레트: 남는 용량(차가운 청색) → 중간(모래색) → 압력(주홍). 불가·미확인은 색이 아니라 빈 링으로 구분
    "여유": {"color": "#22678a", "label": "여유", "desc": "대기 충전기 비율이 기준 이상, 할인"},
    "보통": {"color": "#c8b27a", "label": "보통", "desc": "대기 충전기가 있으나 기준 미만, 기본 요금"},
    "혼잡": {"color": "#d24d27", "label": "혼잡", "desc": "사용 가능한 충전기가 모두 충전 중, 할증"},
    "불가": {"color": "#33403a", "label": "이용 불가", "desc": "통신이상·운영중지·점검중"},
    "미확인": {"color": "#a7b0ab", "label": "상태 미확인", "desc": "상태 정보 없음"},
}
