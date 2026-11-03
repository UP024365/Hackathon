from datetime import datetime

import numpy as np
import pytest

import config
from config import PricingPolicy, TimeBand
from utils import api, demo_data, logic

NOON = datetime(2026, 10, 7, 10, 0, tzinfo=logic.KST)


def charger(stat_id, chger_id, ctype="04", stat="2", output="50", **kw):
    row = {"statId": stat_id, "chgerId": chger_id, "statNm": f"st-{stat_id}", "chgerType": ctype, "stat": stat,
           "output": output, "lat": "36.64", "lng": "127.49", "busiNm": "op", "limitYn": "N", "delYn": "N"}
    row.update(kw)
    return row


# ── 단건 규칙 ──────────────────────────────────────────────
@pytest.mark.parametrize("kw,rate", [(7, 295.0), (30, 307.2), (49.9, 307.2), (50, 325.6), (100, 348.4), (350, 393.1)])
def test_base_rate_tiers(kw, rate):
    assert logic.base_rate_for_output(kw) == rate


@pytest.mark.parametrize("ctype,out,speed", [("02", None, "완속"), ("08", 11, "완속"), ("07", 43, "급속"),
                                             ("04", 100, "급속"), ("", 7, "완속"), ("", 60, "급속"), ("99", None, "기타")])
def test_classify_speed(ctype, out, speed):
    assert logic.classify_speed(ctype, out) == speed


@pytest.mark.parametrize("avail,charging,fault,expected", [
    (2, 2, 0, "여유"),   # 50% → 임계값 이상
    (1, 3, 0, "보통"),
    (0, 4, 0, "혼잡"),
    (0, 0, 2, "불가"),
    (0, 0, 0, "미확인"),
    (2, 2, 4, "여유"),   # 고장 충전기는 분모에서 제외
])
def test_classify_status(avail, charging, fault, expected):
    assert logic.classify_status(avail, charging, fault, 0.5) == expected


def test_time_multiplier_wraps_midnight():
    p = PricingPolicy(use_time_of_day=True, time_bands=(TimeBand(22, 6, 0.8, "심야"),))
    assert logic.time_multiplier(23, p)[0] == 0.8
    assert logic.time_multiplier(3, p)[0] == 0.8
    assert logic.time_multiplier(12, p)[0] == 1.0
    assert logic.time_multiplier(23, PricingPolicy(use_time_of_day=False))[0] == 1.0


# ── 정제 ──────────────────────────────────────────────────
def test_prepare_drops_deleted_limited_duplicates_and_bad_coords():
    rows = [
        charger("A", "01"), charger("A", "01"),                 # 중복
        charger("B", "01", delYn="Y"),                          # 삭제
        charger("C", "01", limitYn="Y"),                        # 이용자 제한
        charger("D", "01", lat="0", lng="0"),                   # 좌표 이상
        charger("E", "01", chgerType="2", output=""),           # 한 자리 코드, 출력 없음
    ]
    df = logic.prepare_chargers(rows)
    assert sorted(df["statId"]) == ["A", "E"]
    e = df[df["statId"] == "E"].iloc[0]
    assert e["chgerType"] == "02" and e["speed"] == "완속" and e["output_kw"] == config.DEFAULT_OUTPUT_KW["완속"]
    assert len(logic.prepare_chargers(rows, exclude_limited=False)) == 3


# ── 집계 · 요금 ───────────────────────────────────────────
def test_station_summary_prices_fast_and_slow_separately():
    rows = [
        charger("S", "01", ctype="04", stat="3", output="100"),  # 급속: 전부 충전중 → 혼잡
        charger("S", "02", ctype="04", stat="3", output="100"),
        charger("S", "03", ctype="02", stat="2", output="7"),    # 완속: 대기 → 여유
        charger("S", "04", ctype="02", stat="4", output="7"),    # 고장
    ]
    st = logic.summarize_stations(logic.prepare_chargers(rows), config.DEFAULT_POLICY, NOON).iloc[0]
    assert (st["total"], st["fast_total"], st["slow_total"], st["fault"]) == (4, 2, 2, 1)
    assert st["fast_status"] == "혼잡" and st["slow_status"] == "여유"
    assert st["fast_price"] == pytest.approx(round(348.4 * 1.15, 1))
    assert st["slow_price"] == pytest.approx(round(295.0 * 0.80, 1))
    assert st["status"] == "보통"          # 전체: 대기 1 / 사용가능 3
    assert st["price"] == st["fast_price"]  # 대표 단가는 급속 우선


def test_unavailable_station_has_no_price():
    rows = [charger("X", "01", stat="5"), charger("X", "02", stat="1")]
    st = logic.summarize_stations(logic.prepare_chargers(rows), config.DEFAULT_POLICY, NOON).iloc[0]
    assert st["status"] == "불가" and np.isnan(st["price"])


def test_policy_variables_change_price():
    rows = [charger("A", "01", stat="2")]
    ch = logic.prepare_chargers(rows)
    p1 = logic.summarize_stations(ch, PricingPolicy(discount_rate=0.1), NOON).iloc[0]["price"]
    p2 = logic.summarize_stations(ch, PricingPolicy(discount_rate=0.3), NOON).iloc[0]["price"]
    assert p1 == pytest.approx(325.6 * 0.9, abs=0.05) and p2 == pytest.approx(325.6 * 0.7, abs=0.05)


# ── 대체 거점 추천 ────────────────────────────────────────
def test_recommend_nearest_free_station_within_radius():
    rows = [
        charger("BUSY", "01", stat="3", lat="36.6400", lng="127.4900"),
        charger("NEAR", "01", stat="2", lat="36.6500", lng="127.4900"),   # 약 1.1km
        charger("FAR", "01", stat="2", lat="36.7400", lng="127.4900"),    # 약 11km
    ]
    st = logic.summarize_stations(logic.prepare_chargers(rows), config.DEFAULT_POLICY, NOON)
    out = logic.recommend_alternatives(st, radius_km=3).set_index("statId")
    assert out.loc["BUSY", "alt_statId"] == "NEAR"
    assert out.loc["BUSY", "alt_dist_km"] == pytest.approx(1.11, abs=0.02)
    out2 = logic.recommend_alternatives(st, radius_km=0.5).set_index("statId")
    assert out2.loc["BUSY", "alt_statId"] == ""
    k = logic.region_kpis(logic.recommend_alternatives(st, radius_km=3))
    assert k["congested"] == 1 and k["redirectable"] == 1


# ── 데모 데이터 · API 파서 ─────────────────────────────────
def test_demo_data_runs_through_pipeline_for_all_regions():
    for region in config.REGIONS:
        st = logic.summarize_stations(logic.prepare_chargers(demo_data.generate(region)), config.DEFAULT_POLICY, NOON)
        assert len(st) > 10 and st["statNm"].str.startswith("[데모]").all()


class FakeResp:
    def __init__(self, payload=None, text=""):
        self._payload, self.text = payload, text

    def json(self):
        if self._payload is None:
            raise ValueError
        return self._payload

    def raise_for_status(self):
        pass


class FakeSession:
    """totalCount=5, 페이지당 2행 → 3페이지를 요청해야 함."""
    def __init__(self):
        self.calls = 0

    def get(self, url, params, timeout):
        self.calls += 1
        start = (params["pageNo"] - 1) * params["numOfRows"]
        items = [{"statId": str(i)} for i in range(start, min(start + params["numOfRows"], 5))]
        return FakeResp({"resultCode": "00", "totalCount": 5, "items": {"item": items}})


def test_fetch_all_paginates_until_total():
    s = FakeSession()
    rows = api.fetch_all("43110", api_key="k", per_page=2, session=s)
    assert len(rows) == 5 and s.calls == 3


def test_parse_single_item_dict_and_xml_error():
    rows, total = api._parse(FakeResp({"resultCode": "00", "totalCount": 1, "items": {"item": {"statId": "A"}}}))
    assert rows == [{"statId": "A"}] and total == 1
    with pytest.raises(api.ApiError):
        api._parse(FakeResp(None, text="<OpenAPI_ServiceResponse>SERVICE_KEY_IS_NOT_REGISTERED_ERROR"))


def test_fast_seekers_are_not_sent_to_slow_only_station():
    rows = [
        charger("BUSY", "01", ctype="04", stat="3", lat="36.6400", lng="127.4900"),
        charger("SLOW", "01", ctype="02", stat="2", output="7", lat="36.6410", lng="127.4900"),   # 0.1km, 완속만
        charger("FAST", "01", ctype="04", stat="2", lat="36.6550", lng="127.4900"),              # 1.7km, 급속 여유
    ]
    st = logic.summarize_stations(logic.prepare_chargers(rows), config.DEFAULT_POLICY, NOON)
    r = logic.recommend_alternatives(st, radius_km=3).set_index("statId").loc["BUSY"]
    assert r["alt_statId"] == "FAST" and r["alt_speed"] == "급속"
    assert r["alt_price"] == pytest.approx(round(325.6 * 0.8, 1))
