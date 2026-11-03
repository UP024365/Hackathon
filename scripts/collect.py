"""
충전기 상태 기록 수집기: 충북 전체를 한 번에 받아 시·군별로 data/history/에 덧붙입니다.

사용법 (프로젝트 루트에서):
    python -m scripts.collect                 # 한 번 수집
    python -m scripts.collect --loop 10       # 10분마다 계속 수집 (Ctrl+C로 중지)
    python -m scripts.collect --out 경로       # 다른 폴더에 저장 (GitHub Actions에서 사용)

server.py를 켜 두면 서버가 같은 수집을 자동으로 돌리므로 따로 실행할 필요가 없습니다.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from utils import api, history
from utils.logic import KST


def collect_once(base=None) -> tuple[dict[str, int], dict[str, list[dict]], datetime]:
    """(시·군별 기록한 충전소 수, 시·군별 원본 행, 수집 시각)."""
    by_region = api.fetch_province()
    now = datetime.now(KST)
    counts = {region: history.append(region, rows, now, base) for region, rows in by_region.items() if rows}
    return counts, by_region, now


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="충북 충전기 상태 기록 수집")
    ap.add_argument("--loop", type=float, default=0, help="이 간격(분)으로 계속 수집")
    ap.add_argument("--out", default=None, help="저장 폴더 (기본: data/history)")
    args = ap.parse_args(argv)
    while True:
        try:
            counts, _, now = collect_once(args.out)
            total = sum(counts.values())
            print(f"✔ {now:%Y-%m-%d %H:%M} 충전소 {total:,}곳 기록 ({', '.join(f'{k} {v}' for k, v in counts.items())})", flush=True)
        except api.ApiError as e:
            print(f"✘ {datetime.now(KST):%H:%M} 수집 실패: {e}", flush=True)
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    sys.exit(main())
