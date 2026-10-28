"""代理环境变量清洗测试。

DSH 等工具会把 ``::1`` 与 ``[::1]`` 同时写进 no_proxy；httpx 解析方括号形式时
抛 ``InvalidURL: Invalid port: ':1]'``，导致所有走 trust_env 的云端请求失败。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorum.config import sanitize_proxy_env  # noqa: E402
from quorum.providers import _client  # noqa: E402


def test_bracketed_ipv6_is_unwrapped(monkeypatch):
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1,::1,[::1]")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1,[::1]")

    sanitize_proxy_env()

    assert os.environ["no_proxy"] == "localhost,127.0.0.1,::1,::1"
    assert os.environ["NO_PROXY"] == "localhost,127.0.0.1,::1,::1"


def test_plain_values_are_untouched(monkeypatch):
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1,::1")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1")

    sanitize_proxy_env()

    assert os.environ["no_proxy"] == "localhost,127.0.0.1,::1"


def test_sanitizer_is_idempotent(monkeypatch):
    monkeypatch.setenv("no_proxy", "localhost,[::1]")

    sanitize_proxy_env()
    sanitize_proxy_env()

    assert os.environ["no_proxy"] == "localhost,::1"


def test_client_creation_sanitizes_env(monkeypatch):
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1,[::1]")

    client = _client(5.0, "https://api.deepseek.com/v1")
    client.close()

    assert os.environ["no_proxy"] == "localhost,127.0.0.1,::1"


def test_httpx_rejects_bracketed_ipv6(monkeypatch):
    """锁定上游行为：no_proxy 里带方括号的 IPv6，httpx 在建客户端时就抛 InvalidURL。"""

    monkeypatch.setenv("no_proxy", "localhost,[::1]")

    with pytest.raises(httpx.InvalidURL):
        httpx.Client(trust_env=True, timeout=1.0)
