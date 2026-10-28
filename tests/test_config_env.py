"""配置与终端渲染的边界测试。

覆盖三处曾经会静默出错的地方：
- 同厂别名 key（ZHIPU_API_KEY 等）从进程环境导出后不生效；
- 本地 base_url 端口非法时抛未捕获的 ValueError；
- 错误文本含 rich 标记（如 [/red]）时渲染层抛 MarkupError。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from rich.console import Console

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorum.config import PROVIDER_DEFS, _port_open, load_settings, resolve_key  # noqa: E402
from quorum.render import Renderer  # noqa: E402
from quorum.types import Utterance  # noqa: E402


def test_alt_key_from_process_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GLM_MODELS=glm-4.6\n", encoding="utf-8")
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.setenv("ZHIPU_API_KEY", "sk-test-zhipu")

    settings = load_settings(env_file)

    assert resolve_key(settings, PROVIDER_DEFS["glm"]) == "sk-test-zhipu"


def test_primary_key_still_wins(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GLM_MODELS=glm-4.6\n", encoding="utf-8")
    monkeypatch.setenv("GLM_API_KEY", "sk-primary")
    monkeypatch.setenv("ZHIPU_API_KEY", "sk-alias")

    settings = load_settings(env_file)

    assert resolve_key(settings, PROVIDER_DEFS["glm"]) == "sk-primary"


def test_invalid_local_port_returns_false_instead_of_raising():
    assert _port_open("http://localhost:abc/v1") is False
    assert _port_open("http://127.0.0.1:99999/v1") is False


def test_render_error_text_with_markup_does_not_raise():
    buffer = io.StringIO()
    renderer = Renderer(console=Console(file=buffer, width=100))
    utterance = Utterance(
        agent_id="A1",
        agent_name="甲",
        phase="讨论",
        role="讨论者",
        content="",
        error="HTTP 500: [/red] 非法标记 [/bold]",
    )

    renderer.on_end(utterance)

    assert "调用失败" in buffer.getvalue()


def test_max_history_chars_is_configurable(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("QUORUM_MAX_HISTORY_CHARS=5000\n", encoding="utf-8")

    settings = load_settings(env_file)

    assert settings.max_history_chars == 5000


def test_max_history_chars_defaults_to_24000(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("QUORUM_MAX_TOKENS=2048\n", encoding="utf-8")

    settings = load_settings(env_file)

    assert settings.max_history_chars == 24000
