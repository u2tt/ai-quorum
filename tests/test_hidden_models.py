"""隐藏列表（hidden_models）端点测试。

- 删除预设厂商模型后不再从发现列表复活
- unhide 后恢复显示
- 删除 custom 模型同时进隐藏表
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorum.providers import ChatResult  # noqa: E402
from quorum.types import ModelSpec  # noqa: E402
from quorum.web import app as web_app  # noqa: E402


def _stub_discover(monkeypatch, specs: list[ModelSpec]) -> None:
    monkeypatch.setattr(web_app, "discover_models", lambda *a, **k: specs)


def _ok_probe(spec, timeout=60.0):
    return ChatResult(content="就绪", model=spec.model, prompt_tokens=3, completion_tokens=2, elapsed=0.01)


def test_delete_preset_model_does_not_revive(tmp_path, monkeypatch) -> None:
    _stub_discover(
        monkeypatch,
        [ModelSpec(provider="deepseek", model="deepseek-flash", label="DeepSeek Flash",
                   base_url="https://api.deepseek.com/v1", api_key="")],
    )
    client = TestClient(web_app.create_app(db_path=tmp_path / "h1.db", env_path=tmp_path / ".env"))

    keys = [m["key"] for m in client.get("/api/models").json()["models"]]
    assert "deepseek:deepseek-flash" in keys

    d = client.delete("/api/models", params={"key": "deepseek:deepseek-flash"})
    assert d.status_code == 200
    assert d.json() == {"ok": True, "hidden": True}

    keys = [m["key"] for m in client.get("/api/models").json()["models"]]
    assert "deepseek:deepseek-flash" not in keys, "删除后不应从发现列表复活"

    hidden = client.get("/api/models/hidden").json()["models"]
    assert "deepseek:deepseek-flash" in [m["key"] for m in hidden]


def test_unhide_restores_model(tmp_path, monkeypatch) -> None:
    _stub_discover(
        monkeypatch,
        [ModelSpec(provider="deepseek", model="deepseek-flash", label="DeepSeek Flash",
                   base_url="https://api.deepseek.com/v1", api_key="")],
    )
    client = TestClient(web_app.create_app(db_path=tmp_path / "h2.db", env_path=tmp_path / ".env"))
    client.delete("/api/models", params={"key": "deepseek:deepseek-flash"})
    assert "deepseek:deepseek-flash" not in [m["key"] for m in client.get("/api/models").json()["models"]]

    r = client.post("/api/models/unhide", json={"key": "deepseek:deepseek-flash"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "unhidden": True}

    keys = [m["key"] for m in client.get("/api/models").json()["models"]]
    assert "deepseek:deepseek-flash" in keys

    hidden = client.get("/api/models/hidden").json()["models"]
    assert hidden == []

    # unhide 不在隐藏表的 key → 404
    assert client.post("/api/models/unhide", json={"key": "nope:missing"}).status_code == 404


def test_delete_custom_model_goes_to_hidden(tmp_path, monkeypatch) -> None:
    _stub_discover(monkeypatch, [])
    monkeypatch.setattr(web_app, "probe_model", _ok_probe)
    db_path = tmp_path / "h3.db"
    client = TestClient(web_app.create_app(db_path=db_path, env_path=tmp_path / ".env"))

    r = client.post(
        "/api/models",
        json={
            "provider": "deepseek",
            "model": "deepseek-extra",
            "base_url": "https://proxy.example.com/v1",
            "api_key": "sk-x",
            "label": "第三方",
        },
    )
    assert r.status_code == 200
    keys = [m["key"] for m in client.get("/api/models").json()["models"]]
    assert "deepseek:deepseek-extra" in keys
    card = next(m for m in client.get("/api/models").json()["models"] if m["key"] == "deepseek:deepseek-extra")
    assert card["custom"] is True

    d = client.delete("/api/models", params={"key": "deepseek:deepseek-extra"})
    assert d.status_code == 200 and d.json() == {"ok": True, "hidden": True}

    assert "deepseek:deepseek-extra" not in [m["key"] for m in client.get("/api/models").json()["models"]]
    assert "deepseek:deepseek-extra" in [m["key"] for m in client.get("/api/models/hidden").json()["models"]]

    import sqlite3
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM custom_models WHERE key = 'deepseek:deepseek-extra'").fetchone()[0]
    conn.close()
    assert count == 0, "DB 记录应被删除"
