"""厂商预设与默认槽位的测试。

覆盖：
- provider_catalog 必须包含国产预设，且未配置 key 时 has_key=False 但仍可见
- default_slots 在只有两个模型时正反不同、裁判可回退
- /api/providers 契约
- POST /api/models 传 provider_key 时自动补 base_url / key
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorum.config import (  # noqa: E402
    PROVIDER_DEFS,
    Settings,
    default_slots,
    provider_catalog,
)
from quorum.providers import ChatResult  # noqa: E402
from quorum.types import ModelSpec  # noqa: E402
from quorum.web import app as web_app  # noqa: E402


def _settings(**env: str) -> Settings:
    return Settings(env=dict(env), env_path=None)


def test_provider_catalog_has_cn_vendors() -> None:
    catalog = provider_catalog(_settings())
    keys = {p["key"] for p in catalog}
    for key in ("deepseek", "glm", "minimax", "kimi", "qwen", "siliconflow", "doubao", "stepfun", "baichuan"):
        assert key in keys
    glm = next(p for p in catalog if p["key"] == "glm")
    assert glm["has_key"] is False
    assert glm["cn"] is True
    assert "glm-4.6" in glm["models"]
    assert glm["base_url"].startswith("https://open.bigmodel.cn")


def test_provider_catalog_marks_has_key() -> None:
    catalog = provider_catalog(_settings(GLM_API_KEY="glm-real-key"))
    glm = next(p for p in catalog if p["key"] == "glm")
    assert glm["has_key"] is True
    # 有 key 的排在前面
    assert catalog[0]["has_key"] is True


def test_alt_key_env_works() -> None:
    catalog = provider_catalog(_settings(ZHIPU_API_KEY="zhipu-key"))
    glm = next(p for p in catalog if p["key"] == "glm")
    assert glm["has_key"] is True


def test_default_slots_distinct_sides() -> None:
    specs = [
        ModelSpec("deepseek", "deepseek-flash", "deepseek-flash", "https://api.deepseek.com/v1", "k"),
        ModelSpec("deepseek", "deepseek-v4-pro", "deepseek-v4-pro", "https://api.deepseek.com/v1", "k"),
    ]
    slots = default_slots(_settings(), specs)
    assert slots["pro"][0]["model_key"] == "deepseek:deepseek-flash"
    assert slots["con"][0]["model_key"] == "deepseek:deepseek-v4-pro"
    assert slots["judge"]["model_key"] == "deepseek:deepseek-v4-pro"
    assert slots["pro"][0]["effort"] == "high"  # 来自预设 default_effort


def test_default_slots_empty() -> None:
    slots = default_slots(_settings(), [])
    assert slots == {"pro": [], "con": [], "judge": None}


def test_api_providers_and_bootstrap(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web_app, "discover_models", lambda *a, **k: [])
    app = web_app.create_app(db_path=tmp_path / "p.db", env_path=tmp_path / ".env")
    client = TestClient(app)

    providers = client.get("/api/providers").json()["providers"]
    assert len(providers) >= 10
    assert all({"key", "name", "base_url", "models", "has_key"} <= set(p) for p in providers)

    boot = client.get("/api/bootstrap").json()
    assert "providers" in boot and boot["providers"]
    assert boot["default_slots"] == {"pro": [], "con": [], "judge": None}


def test_add_model_with_provider_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web_app, "discover_models", lambda *a, **k: [])
    monkeypatch.setattr(
        web_app,
        "probe_model",
        lambda spec, timeout=45.0: ChatResult(content="就绪", model=spec.model, completion_tokens=1),
    )
    env_file = tmp_path / ".env"
    env_file.write_text("GLM_API_KEY=glm-secret\n", encoding="utf-8")
    app = web_app.create_app(db_path=tmp_path / "m.db", env_path=env_file)
    client = TestClient(app)

    # 只给厂商别名与模型名，base_url 与 key 自动补齐
    resp = client.post("/api/models", json={"provider_key": "glm", "model": "glm-4.6"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model"]["key"] == "glm:glm-4.6"

    card = next(m for m in client.get("/api/models").json()["models"] if m["key"] == "glm:glm-4.6")
    assert card["custom"] is True
    assert card["base_url"] == PROVIDER_DEFS["glm"]["base_url"]


def test_add_model_without_base_url_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web_app, "discover_models", lambda *a, **k: [])
    app = web_app.create_app(db_path=tmp_path / "x.db", env_path=tmp_path / ".env")
    client = TestClient(app)
    resp = client.post("/api/models", json={"provider": "unknown-vendor", "model": "m1"})
    assert resp.status_code == 400
    assert "base_url" in resp.json()["detail"]
