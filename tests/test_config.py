"""config.py 的单元测试：占位符判断、模型解析、.env 加载。"""

from __future__ import annotations

import sys
from pathlib import Path

# 项目根目录加入 sys.path，让根目录下的 quorum 命名空间包可直接导入
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from quorum.config import _is_placeholder, load_settings, resolve_model
from quorum.types import ModelSpec

# ---------------------------------------------------------------- 占位符

@pytest.mark.parametrize("value", [None, "", "...", "sk-...", "sk-ant-...", "your-key-here"])
def test_placeholder_truthy(value: str | None) -> None:
    assert _is_placeholder(value) is True


@pytest.mark.parametrize("value", ["sk-real-123", "xai-abcdef", "  sk-real-123  "])
def test_placeholder_falsy(value: str) -> None:
    assert _is_placeholder(value) is False


# ---------------------------------------------------------------- 模型解析

def make_specs() -> list[ModelSpec]:
    return [
        ModelSpec(provider="deepseek", model="deepseek-flash", label="deepseek-flash"),
        ModelSpec(provider="openai", model="gpt-4o", label="gpt-4o"),
        ModelSpec(provider="openai", model="gpt-4o-mini", label="gpt-4o-mini"),
        ModelSpec(provider="ollama", model="qwen2.5:14b", label="qwen2.5:14b"),
    ]


def test_resolve_exact_provider_model() -> None:
    specs = make_specs()
    spec = resolve_model("deepseek:deepseek-flash", specs)
    assert spec is not None
    assert spec.key == "deepseek:deepseek-flash"


def test_resolve_by_model_name() -> None:
    specs = make_specs()
    spec = resolve_model("gpt-4o-mini", specs)
    assert spec is not None
    assert spec.model == "gpt-4o-mini"
    # 大小写不敏感
    assert resolve_model("GPT-4O-MINI", specs) is not None


def test_resolve_unique_prefix() -> None:
    specs = make_specs()
    spec = resolve_model("deepseek-fl", specs)
    assert spec is not None
    assert spec.model == "deepseek-flash"


def test_resolve_ambiguous_returns_none() -> None:
    specs = make_specs()
    assert resolve_model("gpt", specs) is None
    assert resolve_model("4o", specs) is None


def test_resolve_empty_or_unknown() -> None:
    specs = make_specs()
    assert resolve_model("", specs) is None
    assert resolve_model("   ", specs) is None
    assert resolve_model("no-such-model", specs) is None


# ---------------------------------------------------------------- .env 加载

def write_env(directory: Path, content: str) -> Path:
    path = directory / ".env"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_settings_from_local_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_env(
        tmp_path,
        "\n".join(
            [
                "DEEPSEEK_API_KEY=sk-real-123",
                "DEEPSEEK_MODELS=deepseek-flash,deepseek-v4-pro",
                "QUORUM_ROUNDS_PER_AGENT=3",
                "QUORUM_MAX_TOKENS=2048",
                "QUORUM_TEMPERATURE=0.35",
                "QUORUM_MODEL_TIMEOUT=45",
                "QUORUM_SYNTHESIZER=Random",
                "QUORUM_DEFAULT_LANGUAGE=zh",
            ]
        ),
    )
    monkeypatch.chdir(tmp_path)
    for key in (
        "QUORUM_ROUNDS_PER_AGENT",
        "QUORUM_MAX_TOKENS",
        "QUORUM_TEMPERATURE",
        "QUORUM_MODEL_TIMEOUT",
        "QUORUM_SYNTHESIZER",
        "QUORUM_DEFAULT_LANGUAGE",
        "QUORUM_REPORT_DIR",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = load_settings()

    assert settings.env_path == tmp_path / ".env"
    assert settings.rounds_per_agent == 3
    assert settings.max_tokens == 2048
    assert settings.temperature == pytest.approx(0.35)
    assert settings.model_timeout == 45.0
    assert settings.synthesizer == "random"
    assert settings.language == "zh"
    assert settings.get("DEEPSEEK_API_KEY") == "sk-real-123"


def test_load_settings_defaults_without_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))  # 隔离 ~/.quorum/.env
    settings = load_settings()
    assert settings.env_path is None
    assert settings.rounds_per_agent == 2
    assert settings.max_tokens == 4096
    assert settings.synthesizer == "first"


def test_load_settings_explicit_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "custom.env"
    other.write_text("QUORUM_ROUNDS_PER_AGENT=5\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QUORUM_ROUNDS_PER_AGENT", raising=False)

    settings = load_settings(env_path=other)

    assert settings.env_path == other
    assert settings.rounds_per_agent == 5
