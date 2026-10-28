"""「获取远端模型」端点测试。

- 传 provider（预设厂商）时 key 从 .env 回退
- mock _probe_models 返回清单
- 缺 base_url 报 400
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorum.web import app as web_app  # noqa: E402


def test_fetch_models_uses_preset_key_and_base(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web_app, "discover_models", lambda *a, **k: [])
    seen = {}

    def fake_probe(base_url, api_key="", timeout=5.0):
        seen["url"] = base_url
        seen["key"] = api_key
        return ["glm-4.6", "glm-4.5"]

    monkeypatch.setattr(web_app, "_probe_models", fake_probe)
    env_file = tmp_path / ".env"
    env_file.write_text("GLM_API_KEY=glm-secret\n", encoding="utf-8")
    client = TestClient(web_app.create_app(db_path=tmp_path / "f.db", env_path=env_file))

    resp = client.get("/api/fetch-models", params={"provider": "glm", "api_key": ""})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["models"] == ["glm-4.6", "glm-4.5"]
    assert body["base_url"].startswith("https://open.bigmodel.cn")
    assert seen["key"] == "glm-secret"


def test_fetch_models_needs_base_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web_app, "discover_models", lambda *a, **k: [])
    client = TestClient(web_app.create_app(db_path=tmp_path / "f2.db", env_path=tmp_path / ".env"))
    resp = client.get("/api/fetch-models")
    assert resp.status_code == 400
    assert "Base URL" in resp.json()["detail"]


def test_fetch_models_empty_result_is_400(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web_app, "discover_models", lambda *a, **k: [])
    monkeypatch.setattr(web_app, "_probe_models", lambda *a, **k: [])
    client = TestClient(web_app.create_app(db_path=tmp_path / "f3.db", env_path=tmp_path / ".env"))
    resp = client.get("/api/fetch-models", params={"base_url": "http://127.0.0.1:9/v1", "api_key": "x"})
    assert resp.status_code == 400
