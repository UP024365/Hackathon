import gzip
import json

import pytest

import server
from utils import api, demo_data


@pytest.fixture
def client(monkeypatch, tmp_path):
    server._cache.clear()
    monkeypatch.setattr(api.config, "SNAPSHOT_DIR", tmp_path)   # 실제 스냅샷 폴더를 건드리지 않도록
    return server.app.test_client()


def fake_fetch(calls):
    def _fetch(region, **_):
        calls.append(region)
        return demo_data.generate(region)
    return _fetch


def test_index_serves_site(client):
    r = client.get("/")
    assert r.status_code == 200 and "충북 EV 요금 관제".encode() in r.data


def test_health(client):
    body = client.get("/api/health").get_json()
    assert body["ok"] is True and "청주시" in body["regions"]


def test_chargers_live_and_cached(client, monkeypatch):
    calls = []
    monkeypatch.setattr(api, "fetch_region", fake_fetch(calls))
    a = client.get("/api/chargers?region=충주시").get_json()
    b = client.get("/api/chargers?region=충주시").get_json()
    assert a["source"] == "live" and len(a["rows"]) > 50 and a == b
    assert calls == ["충주시"]                       # 두 번째는 캐시
    assert set(a["rows"][0]) <= set(server.FIELDS)   # 필요한 필드만 전송


def test_refresh_is_rate_limited(client, monkeypatch):
    calls = []
    monkeypatch.setattr(api, "fetch_region", fake_fetch(calls))
    client.get("/api/chargers?region=단양군")
    client.get("/api/chargers?region=단양군&refresh=1")   # 30초 안이면 다시 부르지 않음
    assert calls == ["단양군"]


def test_falls_back_to_snapshot_then_errors(client, monkeypatch):
    def boom(region, **_):
        raise api.ApiError("네트워크 오류: ConnectTimeout")
    monkeypatch.setattr(api, "fetch_region", boom)
    r = client.get("/api/chargers?region=증평군")
    assert r.status_code == 502 and "스냅샷도 없습니다" in r.get_json()["error"]

    server._cache.clear()
    api.save_snapshot("증평군", demo_data.generate("증평군"))
    body = client.get("/api/chargers?region=증평군").get_json()
    assert body["source"] == "snapshot" and body["warning"] and body["rows"]


def test_unknown_region(client):
    r = client.get("/api/chargers?region=서울시")
    assert r.status_code == 400


def test_gzip_when_accepted(client, monkeypatch):
    monkeypatch.setattr(api, "fetch_region", fake_fetch([]))
    r = client.get("/api/chargers?region=청주시", headers={"Accept-Encoding": "gzip"})
    assert r.headers["Content-Encoding"] == "gzip"
    assert json.loads(gzip.decompress(r.data))["region"] == "청주시"


def test_set_key_once(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "ENV_FILE", tmp_path / ".env")
    monkeypatch.delenv("EV_API_KEY", raising=False)
    monkeypatch.setattr(server, "start_collector", lambda: None)
    assert client.post("/api/key", json={"key": "짧음"}).status_code == 400

    def bad(*a, **k):
        raise api.ApiError("API 오류 [30] SERVICE_KEY_IS_NOT_REGISTERED_ERROR")
    monkeypatch.setattr(api, "fetch_page", bad)
    r = client.post("/api/key", json={"key": "x" * 40})
    assert r.status_code == 400 and "조회하지 못했습니다" in r.get_json()["error"]

    monkeypatch.setattr(api, "fetch_page", lambda *a, **k: ([], 0))
    assert client.post("/api/key", json={"key": "k" * 40}).get_json()["ok"] is True
    assert (tmp_path / ".env").read_text().startswith("EV_API_KEY=kkkk")
    assert client.get("/api/health").get_json()["live"] is True
    assert client.post("/api/key", json={"key": "z" * 40}).status_code == 409   # 한 번 정하면 못 바꿈
    monkeypatch.delenv("EV_API_KEY", raising=False)
