from datetime import datetime, timedelta

import pytest

import config
import server
from scripts import collect
from utils import api, demo_data, history, logic

T0 = datetime(2026, 10, 7, 0, 0, tzinfo=logic.KST)


def rows_at(region, when):
    return demo_data.generate(region, now=when.replace(tzinfo=None))


def test_only_changes_are_appended_and_state_is_restored(tmp_path):
    first = T0.replace(hour=19, minute=0)
    history.append("증평군", rows_at("증평군", first), first, tmp_path)
    history.append("증평군", rows_at("증평군", first), first + timedelta(minutes=10), tmp_path)   # 변화 없음
    df = history.load_day("증평군", "2026-10-07", tmp_path)
    marks = df[df["statId"] == history.RUN_MARK]
    assert list(marks["t"]) == ["1900", "1910"]                     # 변화가 없어도 수집 시각은 남음
    assert (df["t"] == "1910").sum() == 1                           # 표시 행만 추가됨

    later = T0.replace(hour=8, minute=0) + timedelta(days=1)          # 다른 시간대 상태로 바뀐 경우
    history.append("증평군", rows_at("증평군", later), first + timedelta(minutes=20), tmp_path)
    per = history.latest_per_hour(history.load_day("증평군", "2026-10-07", tmp_path))
    truth, _ = history.station_counts(rows_at("증평군", later))
    got = per[19].set_index("statId").sort_index()[list(history.COUNT_COLS)]
    assert (truth.set_index("statId").sort_index()[list(history.COUNT_COLS)] == got).all().all()
    assert per[19]["t"].iat[0] == "1920"


def test_station_meta_and_counts(tmp_path):
    rows = rows_at("단양군", T0.replace(hour=12))
    df, meta = history.station_counts(rows)
    assert set(df.columns) == {"statId", *history.COUNT_COLS}
    sid = df["statId"].iat[0]
    assert {"n", "la", "ln"} <= set(meta[sid]) and ("fb" in meta[sid] or "sb" in meta[sid])
    assert len(df) == len(logic.summarize_stations(logic.prepare_chargers(rows)))


def test_pattern_averages_previous_days_only(tmp_path):
    for d in (1, 2):
        for h in (8, 19):
            when = T0 - timedelta(days=d) + timedelta(hours=h)
            history.append("괴산군", rows_at("괴산군", when), when, tmp_path)
    today = T0 + timedelta(hours=8)
    history.append("괴산군", rows_at("괴산군", today), today, tmp_path)
    p = history.pattern("괴산군", "2026-10-07", base=tmp_path)
    assert p["days"] == 2 and p["congested"][8] is not None and p["congested"][19] is not None
    assert p["congested"][3] is None


def test_region_of_maps_cheongju_districts():
    assert api.region_of({"zscode": "43113"}) == "청주시"
    assert api.region_of({"zscode": "43800"}) == "단양군"
    assert api.region_of({"zscode": "", "addr": "충청북도 옥천군 옥천읍"}) == "옥천군"
    assert api.region_of({"zscode": "11110"}) is None


def test_collect_once_splits_province(tmp_path, monkeypatch):
    fake = {r: rows_at(r, T0.replace(hour=9)) for r in ("청주시", "음성군")}
    monkeypatch.setattr(api, "fetch_province", lambda **_: fake)
    counts, by_region, when = collect.collect_once(tmp_path)
    assert set(counts) == {"청주시", "음성군"} and all(v > 0 for v in counts.values())
    assert (tmp_path / when.strftime("%Y-%m-%d") / f"{config.REGIONS['청주시']['code']}.csv").exists()


@pytest.fixture
def client(monkeypatch, tmp_path):
    server._hist_cache.clear()
    monkeypatch.setattr(config, "HISTORY_DIR", tmp_path)
    monkeypatch.delenv("HISTORY_URL", raising=False)
    return server.app.test_client()


def test_history_endpoint(client, tmp_path):
    for h in (7, 8):
        when = T0 + timedelta(hours=h, minutes=50)
        history.append("충주시", rows_at("충주시", when), when, tmp_path)
    body = client.get("/api/history?region=충주시&date=2026-10-07").get_json()
    assert sorted(body["hours"], key=int) == ["7", "8"]
    slot = body["hours"]["8"]
    assert slot["t"] == "0850" and all(len(v) == 6 for v in slot["s"].values())
    assert set(slot["s"]) <= set(body["stations"])
    assert client.get("/api/history?region=충주시&date=10-07").status_code == 400
    empty = client.get("/api/history?region=제천시&date=2026-10-07").get_json()
    assert empty["hours"] == {} and empty["pattern"]["days"] == 0


def test_publish_static_builds_site_and_data(tmp_path, monkeypatch):
    import json
    from scripts import publish_static
    fake = {r: rows_at(r, T0.replace(hour=19)) for r in ("청주시", "단양군")}
    monkeypatch.setattr(api, "fetch_province", lambda **_: fake)
    monkeypatch.setattr(api, "get_api_key", lambda: "k")
    out = tmp_path / "_site"
    assert publish_static.main(["--history", str(tmp_path / "h"), "--out", str(out)]) == 0
    status = json.loads((out / "data" / "status.json").read_text())
    assert status["ok"] and status["collected"] and status["collect_error"] is None
    assert set(status["regions"]) == {config.REGIONS["청주시"]["code"], config.REGIONS["단양군"]["code"]}
    day = json.loads((out / "data" / "history" / f"{config.REGIONS['청주시']['code']}.json").read_text())
    assert day["hours"] and day["stations"]
    assert "충북 EV 요금 관제" in (out / "index.html").read_text()


def test_publish_static_without_key_still_builds(tmp_path, monkeypatch):
    import json
    from scripts import publish_static
    monkeypatch.setattr(api, "get_api_key", lambda: None)
    out = tmp_path / "_site"
    assert publish_static.main(["--history", str(tmp_path / "h"), "--out", str(out)]) == 0
    status = json.loads((out / "data" / "status.json").read_text())
    assert status["ok"] is False and "EV_API_KEY" in status["collect_error"]
    assert (out / "index.html").exists()
