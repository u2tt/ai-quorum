"""流式传输的异常与截断语义测试。

此前两个流解析器在测试中从未执行（chat_stream 一律被 stub 替换），
导致「半截回答被当成成功」「上游 error 事件被当成空回复」这两类问题没有回归保护。
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorum import providers  # noqa: E402
from quorum.types import ModelSpec  # noqa: E402


class _FakeResponse:
    status_code = 200

    def __init__(self, lines: list[str], exc: Exception | None = None):
        self._lines = lines
        self._exc = exc
        self.request = httpx.Request("POST", "http://example.test/v1/chat/completions")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self) -> bytes:
        return b""

    def iter_lines(self):
        for line in self._lines:
            yield line
        if self._exc is not None:
            raise self._exc


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def stream(self, *args, **kwargs):
        return self._response


def _install(monkeypatch, response: _FakeResponse) -> None:
    monkeypatch.setattr(providers, "_client", lambda timeout, url: _FakeClient(response))
    monkeypatch.setattr(providers.time, "sleep", lambda seconds: None)


def _spec(provider: str = "deepseek") -> ModelSpec:
    return ModelSpec(
        provider=provider,
        model="test-model",
        label="test-model",
        base_url="http://example.test/v1",
        api_key="sk-test",
    )


def test_partial_content_then_error_is_marked_failed(monkeypatch):
    lines = ['data: {"choices":[{"delta":{"content":"前半段"}}]}']
    _install(monkeypatch, _FakeResponse(lines, exc=httpx.ReadError("连接中断")))

    result = providers.chat_stream(_spec(), [{"role": "user", "content": "hi"}])

    assert result.content == "前半段"
    assert result.error is not None
    assert "回答不完整" in result.error
    assert result.ok is False


def test_upstream_error_event_is_surfaced(monkeypatch):
    lines = ['data: {"error":{"message":"model overloaded"}}']
    _install(monkeypatch, _FakeResponse(lines))

    result = providers.chat_stream(_spec(), [{"role": "user", "content": "hi"}])

    assert result.content == ""
    assert result.error is not None
    assert "上游返回错误" in result.error


def test_finish_reason_length_marks_truncation(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"content":"正文"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
    ]
    _install(monkeypatch, _FakeResponse(lines))

    result = providers.chat_stream(_spec(), [{"role": "user", "content": "hi"}])

    assert result.truncated is True
    assert "max_tokens 上限" in result.content
    assert result.error is None


def test_anthropic_error_event_is_surfaced(monkeypatch):
    lines = [
        'data: {"type":"message_start","message":{"usage":{"input_tokens":12}}}',
        'data: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
    ]
    _install(monkeypatch, _FakeResponse(lines))

    result = providers.chat_stream(
        _spec("anthropic"), [{"role": "user", "content": "hi"}]
    )

    assert result.error is not None
    assert "上游返回错误" in result.error


def test_anthropic_max_tokens_stop_reason_marks_truncation(monkeypatch):
    lines = [
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"正文"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"},"usage":{"output_tokens":9}}',
    ]
    _install(monkeypatch, _FakeResponse(lines))

    result = providers.chat_stream(
        _spec("anthropic"), [{"role": "user", "content": "hi"}]
    )

    assert result.truncated is True
    assert result.completion_tokens == 9
    assert "max_tokens 上限" in result.content


# ---------------------------------------------------------------- 思考吃光额度

class _BudgetClient:
    """按请求里的 max_tokens 决定返回思考还是正文，用于验证预算阶梯。"""

    def __init__(self, record: list[int]):
        self._record = record

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def stream(self, method, url, headers=None, json=None, **kwargs):
        budget = json["max_tokens"]
        self._record.append(budget)
        if budget < 16384:
            lines = ['data: {"choices":[{"delta":{"reasoning_content":"想很久"}}]}']
        else:
            lines = ['data: {"choices":[{"delta":{"content":"终于有正文"}}]}']
        return _FakeResponse(lines)


@pytest.fixture(autouse=True)
def _clear_reasoning_memory():
    providers._REASONING_HEAVY.clear()
    yield
    providers._REASONING_HEAVY.clear()


def test_budget_ladder_climbs_until_content_appears(monkeypatch):
    record: list[int] = []
    monkeypatch.setattr(providers, "_client", lambda timeout, url: _BudgetClient(record))
    monkeypatch.setattr(providers.time, "sleep", lambda seconds: None)

    result = providers.chat_stream(_spec(), [{"role": "user", "content": "hi"}], max_tokens=4096)

    assert record == [4096, 8192, 16384]
    assert result.content == "终于有正文"
    assert result.error is None


def test_reasoning_heavy_model_is_remembered(monkeypatch):
    record: list[int] = []
    monkeypatch.setattr(providers, "_client", lambda timeout, url: _BudgetClient(record))
    monkeypatch.setattr(providers.time, "sleep", lambda seconds: None)

    providers.chat_stream(_spec(), [{"role": "user", "content": "hi"}], max_tokens=4096)
    record.clear()
    providers.chat_stream(_spec(), [{"role": "user", "content": "hi"}], max_tokens=4096)

    # 第二次直接从 8192 起，省掉一轮注定失败的请求
    assert record == [8192, 16384]


def test_reasoning_only_failure_mentions_tried_budget(monkeypatch):
    class _AlwaysReasoning(_BudgetClient):
        def stream(self, method, url, headers=None, json=None, **kwargs):
            self._record.append(json["max_tokens"])
            return _FakeResponse(
                ['data: {"choices":[{"delta":{"reasoning_content":"只有思考"}}]}']
            )

    record: list[int] = []
    monkeypatch.setattr(providers, "_client", lambda timeout, url: _AlwaysReasoning(record))
    monkeypatch.setattr(providers.time, "sleep", lambda seconds: None)

    result = providers.chat_stream(_spec(), [{"role": "user", "content": "hi"}], max_tokens=4096)

    assert result.content == ""
    assert result.error is not None
    assert "max_tokens=16384" in result.error
