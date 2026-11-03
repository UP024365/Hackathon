import pytest

from utils import logic, prices
from tests.test_logic import NOON, charger
import config


@pytest.mark.parametrize("raw,norm", [("파워큐브(SP)", "파워큐브"), ("스타코프(주)스타코프", "스타코프"), ("SK 일렉링크", "SK일렉링크"),
                                      ("주식회사 에버온", "에버온"), ("LG유플러스 볼트업", "LG유플러스볼트업"), ("엘지유플러스", "LG유플러스볼트업"),
                                      ("기후부", "기후에너지환경부"), ("한국전력", "한국전력공사"), ("파킹클라우드", "아이파킹")])
def test_normalize(raw, norm):
    assert prices.normalize(raw) == norm


def test_operator_tier_lookup():
    assert prices.lookup("채비", 100) == (430.0, "운영사", "채비")
    assert prices.lookup("채비", 50)[0] == 410.0
    assert prices.lookup("SK일렉링크", 7)[0] == 295.0
    assert prices.lookup("SK일렉링크", 100, member=False)[0] == 590.0
    assert prices.lookup("에버온", 7)[0] == 296.0


def test_missing_tier_uses_nearest_tier_of_same_operator():
    v, src, _ = prices.lookup("아이파킹", 150)          # 100kW 구간 미등재 → 50~99kW 345원
    assert (v, src) == (345.0, "운영사(인접 구간)")


def test_unknown_operator_falls_back_to_public_rate():
    v, src, op = prices.lookup("없는회사", 100)
    assert (v, src, op) == (348.4, "공공 기준(미등재)", "기후에너지환경부")
    assert prices.lookup("없는회사", 100, member=False)[0] is None


def test_station_price_uses_operator_rate():
    rows = [charger("C", "01", stat="2", output="100", busiNm="채비"),
            charger("C", "02", stat="3", output="100", busiNm="채비")]
    st = logic.summarize_stations(logic.prepare_chargers(rows), config.DEFAULT_POLICY, NOON).iloc[0]
    assert st["fast_base"] == 430.0 and st["fast_status"] == "여유"
    assert st["fast_price"] == pytest.approx(round(430 * 0.8, 1))
