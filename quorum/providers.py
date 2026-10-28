"""统一大模型客户端。

只做一件事：给定 ModelSpec + messages，流式拿到文本，返回结构化结果。

支持两种协议：
- OpenAI 兼容（/chat/completions + SSE）——覆盖 deepseek / openai / google / xai /
  openrouter / lmstudio / ollama / llamaswap / custom
- Anthropic 原生（/messages + SSE）
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from .config import is_local_url, sanitize_proxy_env
from .types import ModelSpec

DeltaCallback = Callable[[str], None]
ReasoningCallback = Callable[[str], None]

# 进程内记忆：哪些模型出现过「思考内容占满额度、正文为空」。
# 记下来之后，同一模型下次直接从 8192 起，省掉一轮注定失败的请求。
_REASONING_HEAVY: set[str] = set()
REASONING_START_TOKENS = 8192
MAX_TOKENS_CEILING = 16384


@dataclass(slots=True)
class ChatResult:
    content: str = ""
    reasoning: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed: float = 0.0
    error: str | None = None
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.content.strip())

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _approx_tokens(text: str) -> int:
    """无 usage 时的粗估：中文按 1.6 字/token，英文按 4 字符/token。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return int(cjk / 1.6 + other / 4) + 1


def chat_stream(
    spec: ModelSpec,
    messages: list[dict],
    *,
    max_tokens: int = 4096,
    temperature: float = 0.8,
    timeout: float = 120.0,
    on_delta: DeltaCallback | None = None,
    on_reasoning: ReasoningCallback | None = None,
    extra: dict | None = None,
) -> ChatResult:
    """同步流式调用。on_delta 每收到一段正文就回调一次（用于终端实时渲染）。"""

    started = time.monotonic()
    result = ChatResult(model=spec.model)
    attempts = 0
    bumps = 0
    last_error: str | None = None

    if spec.key in _REASONING_HEAVY and max_tokens < REASONING_START_TOKENS:
        max_tokens = REASONING_START_TOKENS

    while attempts < 3:
        attempts += 1
        # 每次尝试都从干净状态开始，避免上一次的残文被当成本次结果
        result.content = ""
        result.reasoning = ""
        result.truncated = False
        attempt_error: str | None = None
        try:
            if spec.provider == "anthropic":
                _stream_anthropic(spec, messages, max_tokens, temperature, timeout,
                                  result, on_delta, on_reasoning, extra)
            else:
                _stream_openai(spec, messages, max_tokens, temperature, timeout,
                               result, on_delta, on_reasoning, extra)
            if result.content.strip():
                break
            # 推理模型把额度全用在 reasoning_content 上、正文被挤空：逐级抬预算
            if result.reasoning.strip() and max_tokens < MAX_TOKENS_CEILING and bumps < 2:
                bumps += 1
                _REASONING_HEAVY.add(spec.key)
                max_tokens = min(MAX_TOKENS_CEILING, max(max_tokens * 2, REASONING_START_TOKENS))
                last_error = f"思考内容占满额度，已提升至 max_tokens={max_tokens} 重试"
                time.sleep(0.4)
                continue
            if result.reasoning.strip():
                attempt_error = (
                    f"模型只输出思考内容（reasoning_content）、未产出正文；"
                    f"已试到 max_tokens={max_tokens}。该模型可能无法关闭 thinking，"
                    "建议换模型或在模型设置里降低思考强度"
                )
            else:
                attempt_error = "模型返回空内容（可能是 max_tokens 过小或模型拒答）"
        except httpx.TimeoutException:
            attempt_error = f"超时（>{timeout:.0f}s）"
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            detail = _short(exc.response.text)
            attempt_error = f"HTTP {code}: {detail}"
            if code in (400, 401, 403, 404):
                last_error = attempt_error
                break  # 配置类错误，重试无意义
        except httpx.HTTPError as exc:
            attempt_error = f"网络错误: {exc.__class__.__name__} {exc}"
        except Exception as exc:  # 流解析异常等
            attempt_error = f"{exc.__class__.__name__}: {exc}"

        # 已经收到部分正文、但本次请求异常结束：保留残文，同时标记为失败，
        # 不能让半截回答冒充完整结果
        if result.content.strip():
            if attempt_error:
                result.error = f"回答不完整（{attempt_error}）"
            break
        last_error = attempt_error
        if attempts < 3:
            time.sleep(1.0 * attempts)

    result.elapsed = time.monotonic() - started
    if not result.content.strip() and result.error is None:
        result.error = last_error or "未知错误"
    return result


# ------------------------------------------------------------------ OpenAI 兼容

def _stream_openai(
    spec: ModelSpec,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    timeout: float,
    result: ChatResult,
    on_delta: DeltaCallback | None,
    on_reasoning: ReasoningCallback | None,
    extra: dict | None,
) -> None:
    url = spec.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if spec.api_key:
        headers["Authorization"] = f"Bearer {spec.api_key}"

    body: dict = {
        "model": spec.model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # 云端 OpenAI 兼容服务支持流式 usage 统计；本地服务会忽略该字段
    if spec.provider in ("deepseek", "openai", "openrouter", "custom", "xai", "google"):
        body["stream_options"] = {"include_usage": True}
    if extra:
        body.update(extra)

    truncated = False
    result.content = ""
    with _client(timeout, url) as client:
        with client.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise httpx.HTTPStatusError(
                    f"{resp.status_code}", request=resp.request, response=resp
                )
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("data:"):
                    data = line[5:].strip()
                elif line.startswith("{"):
                    data = line.strip()
                else:
                    continue
                if data in ("", "[DONE]"):
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue

                # 部分服务用 200 + 错误事件的方式报错，不能当成「模型返回空内容」
                if payload.get("error"):
                    detail = _short(json.dumps(payload["error"], ensure_ascii=False))
                    raise RuntimeError(f"上游返回错误：{detail}")

                usage = payload.get("usage")
                if isinstance(usage, dict) and usage:
                    result.prompt_tokens = usage.get("prompt_tokens") or result.prompt_tokens
                    result.completion_tokens = (
                        usage.get("completion_tokens") or result.completion_tokens
                    )

                choices = payload.get("choices") or []
                if not choices:
                    continue
                if choices[0].get("finish_reason") == "length":
                    truncated = True
                delta = choices[0].get("delta") or {}
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    result.reasoning += reasoning
                    if on_reasoning:
                        on_reasoning(reasoning)
                piece = delta.get("content")
                if piece:
                    # 增量写入 result：流中途异常时已收到的正文仍然保留
                    result.content += piece
                    if on_delta:
                        on_delta(piece)

    result.content = result.content.strip()
    if truncated and result.content:
        result.truncated = True
        result.content += "\n\n（注：达到 max_tokens 上限，回答可能被截断）"
    if not result.completion_tokens:
        result.completion_tokens = _approx_tokens(result.content + result.reasoning)
    if not result.prompt_tokens:
        result.prompt_tokens = _approx_tokens(
            "".join(str(m.get("content", "")) for m in messages)
        )


# ------------------------------------------------------------------ Anthropic

def _stream_anthropic(
    spec: ModelSpec,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    timeout: float,
    result: ChatResult,
    on_delta: DeltaCallback | None,
    on_reasoning: ReasoningCallback | None,
    extra: dict | None,
) -> None:
    url = spec.base_url.rstrip("/") + "/messages"
    if "/v1" not in url:
        url = spec.base_url.rstrip("/") + "/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": spec.api_key,
        "anthropic-version": "2023-06-01",
    }
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    chat = [m for m in messages if m.get("role") != "system"]
    body = {
        "model": spec.model,
        "messages": chat,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    if system_parts:
        body["system"] = "\n\n".join(str(p) for p in system_parts)
    if extra:
        body.update(extra)

    truncated = False
    result.content = ""
    with _client(timeout, url) as client:
        with client.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise httpx.HTTPStatusError(
                    f"{resp.status_code}", request=resp.request, response=resp
                )
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                kind = payload.get("type")

                # Anthropic 用流内 error 事件报错（overloaded / invalid_request 等）
                if kind == "error":
                    detail = _short(
                        json.dumps(payload.get("error") or payload, ensure_ascii=False)
                    )
                    raise RuntimeError(f"上游返回错误：{detail}")

                if kind == "message_start":
                    usage = (payload.get("message") or {}).get("usage") or {}
                    result.prompt_tokens = usage.get("input_tokens") or 0
                elif kind == "content_block_delta":
                    delta = payload.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        result.content += delta["text"]
                        if on_delta:
                            on_delta(delta["text"])
                    elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                        result.reasoning += delta["thinking"]
                        if on_reasoning:
                            on_reasoning(delta["thinking"])
                elif kind == "message_delta":
                    if (payload.get("delta") or {}).get("stop_reason") == "max_tokens":
                        truncated = True
                    usage = payload.get("usage") or {}
                    if usage.get("output_tokens"):
                        result.completion_tokens = usage["output_tokens"]

    result.content = result.content.strip()
    if truncated and result.content:
        result.truncated = True
        result.content += "\n\n（注：达到 max_tokens 上限，回答可能被截断）"
    if not result.completion_tokens:
        result.completion_tokens = _approx_tokens(result.content)


def probe_model(spec: ModelSpec, timeout: float = 60.0) -> ChatResult:
    """连通性自检：发一句最短请求。"""
    return chat_stream(
        spec,
        [{"role": "user", "content": "回复两个字：就绪"}],
        max_tokens=24,
        temperature=0.0,
        timeout=timeout,
    )


def _client(timeout: float, url: str) -> httpx.Client:
    """本地端点必须绕开环境代理，否则 localhost 会被劫持成 502/timeout。

    trust_env=True 时 httpx 还会解析 no_proxy；其中 ``[::1]`` 这种带方括号的
    IPv6 写法会直接抛 InvalidURL，所以每次建客户端前先清洗一遍。
    """
    sanitize_proxy_env()
    return httpx.Client(
        timeout=httpx.Timeout(timeout, connect=15.0),
        trust_env=not is_local_url(url),
    )


def _short(text: str, limit: int = 220) -> str:
    text = " ".join((text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")
