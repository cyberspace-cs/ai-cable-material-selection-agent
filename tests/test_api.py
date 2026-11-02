"""API 冒烟测试：验证无密钥状态下完整链路可跑通。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["materials"] >= 15


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "CableMind" in r.text


def test_select_endpoint_returns_all_sections():
    r = client.post("/api/select", json={"model": "YJV-0.6/1kV 4×35+1×16", "lszh": True, "flame": "ZC"})
    assert r.status_code == 200
    d = r.json()
    for key in ["summary", "layers", "checks", "citations", "assumptions", "questions", "narrative"]:
        assert key in d
    assert len(d["layers"]) == 7
    assert d["narrative"]["mode"].startswith("offline")
    assert d["narrative"]["sections"]


def test_select_rejects_empty_model():
    r = client.post("/api/select", json={"model": "   "})
    assert r.status_code == 400


def test_scenarios_available():
    r = client.get("/api/scenarios")
    assert len(r.json()["scenarios"]) >= 3
