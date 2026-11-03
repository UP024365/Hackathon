"""
실시간 API 데이터를 data/snapshots/ 에 저장합니다 (발표장 오프라인 시연용).

사용법 (프로젝트 루트에서):
    python -m scripts.save_snapshot            # 충북 11개 시·군 전체
    python -m scripts.save_snapshot 청주시 충주시  # 일부만
"""
from __future__ import annotations

import sys

import config
from utils import api


def main(regions: list[str]) -> int:
    targets = regions or list(config.REGIONS)
    unknown = [r for r in targets if r not in config.REGIONS]
    if unknown:
        print(f"알 수 없는 지역: {unknown}  (가능: {', '.join(config.REGIONS)})")
        return 1
    failed = 0
    for region in targets:
        try:
            rows = api.fetch_region(region)
            path = api.save_snapshot(region, rows)
            print(f"✔ {region}: 충전기 {len(rows):,}행 → {path.relative_to(config.ROOT)}")
        except api.ApiError as e:
            failed += 1
            print(f"✘ {region}: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
