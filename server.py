"""
웹사이트 + 실시간 데이터 서버 (Flask)

- /                    사이트 (docs/index.html)
- /api/health          서버 상태, 실시간 조회 가능 여부
- /api/chargers?region=청주시[&refresh=1]
                       해당 시·군의 충전기 행. API 키는 서버에만 있고 브라우저로 나가지 않습니다.
                       5분 캐시, 실시간 조회가 실패하면 data/snapshots/의 스냅샷으로 대체합니다.
- /api/history?region=청주시[&date=YYYY-MM-DD]
                       그날 시간대별 실측 상태 + 지난 7일 같은 시간대 평균 혼잡 거점 수

서버를 켜 두면 COLLECT_INTERVAL_MIN(기본 10분)마다 충북 전체 상태를 data/history/에 기록합니다.
수집한 결과로 실시간 캐시도 함께 갱신해서, 화면을 여는 사람이 많아도 API 호출은 늘지 않습니다.

로컬 실행:  python server.py          → http://localhost:8000
배포:      gunicorn server:app       (환경변수 EV_API_KEY 설정, render.yaml 참고)
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import threading
import time
from datetime import datetime

from flask import Flask, Response, request, send_from_directory

import config
from scripts import collect
from utils import api, history
from utils.logic import KST

app = Flask(__name__, static_folder=None)

# 사이트가 쓰는 필드만 보내 응답 크기를 줄임
FIELDS = ("statId", "chgerId", "statNm", "addr", "addrDetail", "lat", "lng", "busiNm", "chgerType", "stat",
          "output", "kind", "kindDetail", "parkingFree", "limitYn", "delYn", "useTime", "zscode", "statUpdDt")
MIN_REFRESH_SEC = 30          # '새로 받기'를 연타해도 API 한도를 지키도록
_cache: dict[str, tuple[float, dict]] = {}
_hist_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_lock = threading.Lock()
HIST_TTL_SEC = 120
collector_state = {"interval_min": 0, "last_run": None, "last_error": None, "runs": 0}


def _slim(rows: list[dict]) -> list[dict]:
    return [{k: r[k] for k in FIELDS if r.get(k) not in (None, "")} for r in rows]


def _json(payload: dict, status: int = 200, max_age: int = 0) -> Response:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    resp = Response(body, status=status, mimetype="application/json")
    if len(body) > 2048 and "gzip" in request.headers.get("Accept-Encoding", ""):
        resp.set_data(gzip.compress(body, compresslevel=5))
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Vary"] = "Accept-Encoding"
    resp.headers["Cache-Control"] = f"public, max-age={max_age}" if max_age else "no-store"
    return resp


@app.get("/")
def index():
    resp = send_from_directory(config.ROOT / "docs", "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/data/status.json")
def static_probe():
    """사이트는 먼저 GitHub Pages용 정적 데이터를 찾음. 서버에서는 '없음'으로 답해 api/health로 넘어가게 함."""
    return _json({"ok": False, "mode": "server"})


@app.get("/api/health")
def health():
    pub = config.ROOT / ".public_url"     # run.command가 만든 폰·외부용 주소
    public_url = pub.read_text(encoding="utf-8").strip() if pub.exists() else ""
    return _json({"ok": True, "live": api.get_api_key() is not None, "regions": list(config.REGIONS),
                  "cache_ttl_sec": config.CACHE_TTL_SEC, "collector": collector_state, "public_url": public_url})


@app.get("/api/history")
def history_day():
    region = request.args.get("region", "")
    if region not in config.REGIONS:
        return _json({"error": f"알 수 없는 지역입니다: {region}"}, 400)
    day = request.args.get("date") or datetime.now(KST).strftime("%Y-%m-%d")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return _json({"error": "date는 YYYY-MM-DD 형식이어야 합니다."}, 400)
    key, now = (region, day), time.time()
    with _lock:
        hit = _hist_cache.get(key)
    if hit and now - hit[0] < HIST_TTL_SEC:
        return _json(hit[1])
    payload = history.day_payload(region, day)
    with _lock:
        _hist_cache[key] = (now, payload)
    return _json(payload)


# ─────────────────────────────────────────────────────────────
# 처음 한 번: 사이트(폰 포함)에서 API 키 넣기. 키가 이미 있으면 바꿀 수 없음
# ─────────────────────────────────────────────────────────────
ENV_FILE = config.ROOT / ".env"


@app.post("/api/key")
def set_key():
    if api.get_api_key():
        return _json({"error": "이미 키가 설정돼 있습니다. 바꾸려면 실행 폴더의 .env 파일을 고치고 다시 실행하세요."}, 409)
    key = str((request.get_json(silent=True) or {}).get("key", "")).strip()
    if len(key) < 20 or any(c.isspace() for c in key):
        return _json({"error": "인증키 형식이 아닙니다. 공공데이터포털 마이페이지의 인증키를 그대로 붙여 넣으세요."}, 400)
    try:   # 저장하기 전에 실제로 호출해 키가 맞는지 확인
        api.fetch_page(config.REGIONS["증평군"]["code"], 1, 10, api_key=api.unquote(key) if "%" in key else key)
    except api.ApiError as e:
        return _json({"error": f"이 키로 조회하지 못했습니다. {e}"}, 400)
    ENV_FILE.write_text(f"EV_API_KEY={key}\n", encoding="utf-8")
    os.environ["EV_API_KEY"] = key
    with _lock:
        _cache.clear()
    start_collector()
    return _json({"ok": True})


# ─────────────────────────────────────────────────────────────
# 자동 수집 (서버가 켜져 있는 동안)
# ─────────────────────────────────────────────────────────────
def _collector_loop(interval_min: float) -> None:
    while True:
        try:
            counts, by_region, when = collect.collect_once()
            stamp, now = when.strftime("%Y-%m-%d %H:%M"), time.time()
            with _lock:
                for region, rows in by_region.items():
                    if rows:
                        _cache[region] = (now, {"region": region, "source": "live", "fetched_at": stamp, "rows": _slim(rows)})
                _hist_cache.clear()
            collector_state.update(last_run=stamp, last_error=None, runs=collector_state["runs"] + 1)
            app.logger.info("기록 수집 완료 %s: 충전소 %d곳", stamp, sum(counts.values()))
        except Exception as e:  # 수집 실패가 서버를 멈추지 않도록
            collector_state["last_error"] = f"{datetime.now(KST):%H:%M} {e}"
            app.logger.warning("기록 수집 실패: %s", e)
        time.sleep(interval_min * 60)


def start_collector() -> None:
    interval = float(os.getenv("COLLECT_INTERVAL_MIN", config.COLLECT_INTERVAL_MIN))
    if interval <= 0 or not api.get_api_key() or collector_state["interval_min"]:
        return
    collector_state["interval_min"] = interval
    threading.Thread(target=_collector_loop, args=(interval,), daemon=True, name="collector").start()


# gunicorn으로 띄울 때도 수집이 돌도록 import 시점에 시작 (테스트 중에는 끔)
if "pytest" not in sys.modules:
    start_collector()


@app.get("/api/chargers")
def chargers():
    region = request.args.get("region", "")
    if region not in config.REGIONS:
        return _json({"error": f"알 수 없는 지역입니다: {region}. 가능한 값: {', '.join(config.REGIONS)}"}, 400)

    now = time.time()
    with _lock:
        hit = _cache.get(region)
    age = now - hit[0] if hit else None
    want_refresh = request.args.get("refresh") == "1"
    if hit and (age < config.CACHE_TTL_SEC and not want_refresh or age < MIN_REFRESH_SEC):
        return _json(hit[1])

    try:
        rows = api.fetch_region(region)
        payload = {"region": region, "source": "live",
                   "fetched_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "rows": _slim(rows)}
    except api.ApiError as e:
        app.logger.warning("실시간 조회 실패 (%s): %s", region, e)
        snap = api.load_snapshot(region)
        if not (snap and snap[0]):
            return _json({"error": f"실시간 조회에 실패했고 저장된 스냅샷도 없습니다. ({e})"}, 502)
        payload = {"region": region, "source": "snapshot", "fetched_at": snap[1].replace("T", " ")[:16],
                   "rows": _slim(snap[0]), "warning": "실시간 조회에 실패해 저장된 스냅샷을 보여줍니다."}

    with _lock:
        _cache[region] = (now, payload)
    return _json(payload)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    live = api.get_api_key() is not None
    print(f"충북 EV 요금 관제: http://localhost:{port}")
    print(f"  실시간 조회: {'가능' if live else '불가 (.env에 EV_API_KEY 필요)'}")
    print(f"  기록 수집: {str(collector_state['interval_min']) + '분마다 data/history/에 저장' if collector_state['interval_min'] else '꺼짐'}")
    app.run(host="0.0.0.0", port=port, debug=False)
