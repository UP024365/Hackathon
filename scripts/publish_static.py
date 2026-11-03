"""
GitHub Pages용 정적 사이트 만들기: (수집) → 기록 → _site/ 에 사이트와 데이터 JSON을 씀.

GitHub Actions(.github/workflows/deploy.yml)가 15분마다 실행합니다. 서버 없이도 사이트가
실제 데이터를 보여주도록, 서버의 /api/history 응답과 같은 내용을 미리 파일로 만들어 둡니다.

  _site/index.html                 사이트 (docs/index.html)
  _site/data/status.json           마지막 수집 시각, 수집 성공 여부, 기록이 있는 시·군
  _site/data/history/{zscode}.json 오늘(없으면 어제) 시간대별 상태 + 충전소 정보 + 지난 7일 평균

사용법:
    python -m scripts.publish_static --history hist/history --out _site            # 수집 + 빌드
    python -m scripts.publish_static --history hist/history --out _site --no-collect # 빌드만
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import config
from scripts import build_web, collect
from utils import api, history
from utils.logic import KST


def build_site(history_dir: Path, out: Path, *, collected: dict | None, error: str | None, now: datetime) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(build_web.build(), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    data_dir = out / "data" / "history"
    data_dir.mkdir(parents=True, exist_ok=True)

    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    regions = {}
    for name, r in config.REGIONS.items():
        payload = history.day_payload(name, today, history_dir)
        if not payload["hours"]:                      # 자정 직후처럼 오늘 기록이 아직 없으면 어제 것
            prev = history.day_payload(name, yesterday, history_dir)
            if prev["hours"]:
                payload = prev
        if not payload["hours"]:
            continue
        last_h = max(payload["hours"], key=int)
        regions[r["code"]] = {"date": payload["date"], "t": payload["hours"][last_h]["t"],
                              "stations": len(payload["hours"][last_h]["s"])}
        (data_dir / f"{r['code']}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    status = {"ok": bool(regions), "mode": "static", "built_at": now.strftime("%Y-%m-%d %H:%M"),
              "collected": collected is not None, "collect_error": error, "regions": regions,
              "refresh_sec": 300}
    (out / "data" / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    return status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GitHub Pages용 정적 사이트 빌드")
    ap.add_argument("--history", default=str(config.HISTORY_DIR), help="기록 폴더")
    ap.add_argument("--out", default="_site", help="출력 폴더")
    ap.add_argument("--no-collect", action="store_true", help="수집 없이 기존 기록으로만 빌드")
    args = ap.parse_args(argv)

    history_dir, out = Path(args.history), Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    collected, error = None, None
    if not args.no_collect:
        if not api.get_api_key():
            error = "EV_API_KEY가 설정되지 않았습니다 (저장소 Settings > Secrets > Actions)."
        else:
            try:
                collected, _, when = collect.collect_once(history_dir)
                print(f"✔ {when:%Y-%m-%d %H:%M} 수집: " + ", ".join(f"{k} {v}" for k, v in collected.items()))
            except api.ApiError as e:
                error = str(e)
        if error:
            print(f"✘ 수집 실패: {error}")

    status = build_site(history_dir, out, collected=collected, error=error, now=datetime.now(KST))
    print(f"✔ {out}/ 빌드: 기록이 있는 시·군 {len(status['regions'])}곳")
    # 수집이 실패해도 사이트는 이전 기록(없으면 가상 데이터)으로 배포합니다.
    # 실패 표시는 워크플로 마지막 단계가 status.json의 collect_error를 보고 합니다.
    if error and os.getenv("GITHUB_ACTIONS"):
        print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
