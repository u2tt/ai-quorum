"""配置解析：读取 .env、发现 provider、枚举可用模型。

设计目标：
1. 直接兼容 ~/.quorum/.env 的字段命名（DEEPSEEK_API_KEY / OPENAI_MODELS / LMSTUDIO_BASE_URL ...）
2. 只把「真的填了 key」的 provider 当可用，占位符（sk-... / ...）自动忽略
3. 本地 LM Studio / Ollama 自动探测，无需配置
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import dotenv_values

from .types import ModelSpec

_BRACKETED_IPV6 = re.compile(r"\[([0-9A-Fa-f:]+)\]")


def sanitize_proxy_env() -> None:
    """去掉 no_proxy / NO_PROXY 里 IPv6 字面量的方括号。

    httpx 会把 ``[::1]`` 当成 ``all://*[::1]`` 解析，随后抛
    ``InvalidURL: Invalid port: ':1]'``；DSH 等工具会把 ``::1`` 与 ``[::1]``
    两种写法同时写进 no_proxy，于是所有走 trust_env 的请求全部失败。
    这里统一改成裸 IPv6（对 undici 与 httpx 都正确），其余条目原样保留。
    """

    for name in ("no_proxy", "NO_PROXY"):
        raw = os.environ.get(name)
        if not raw or "[" not in raw:
            continue
        os.environ[name] = _BRACKETED_IPV6.sub(r"\1", raw)


# provider -> 端点 / key 变量 / 模型变量。
# name=展示名，cn=国产厂商（前端分组用），efforts=支持的思考强度档位，
# alt_key_env=同一家常见的备选环境变量名，docs=申请地址。
PROVIDER_DEFS: dict[str, dict] = {
    # ---------------------------------------------------------- 国产
    "deepseek": {
        "name": "DeepSeek 深度求索",
        "base_url": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY",
        "models_env": "DEEPSEEK_MODELS",
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "efforts": ["low", "high", "max"],
        "default_effort": "high",
        "cn": True,
        "docs": "https://platform.deepseek.com/api_keys",
    },
    "glm": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "key_env": "GLM_API_KEY",
        "alt_key_env": "ZHIPU_API_KEY",
        "models_env": "GLM_MODELS",
        "models": ["glm-4.6", "glm-4.5", "glm-4.5-air", "glm-4.5-flash"],
        "cn": True,
        "docs": "https://open.bigmodel.cn/usercenter/apikeys",
    },
    "minimax": {
        "name": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "key_env": "MINIMAX_API_KEY",
        "models_env": "MINIMAX_MODELS",
        "models": ["MiniMax-M2", "MiniMax-Text-01", "abab6.5s-chat"],
        "cn": True,
        "docs": "https://platform.minimaxi.com/user-center/basic-information/interface-key",
    },
    "kimi": {
        "name": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "key_env": "MOONSHOT_API_KEY",
        "alt_key_env": "KIMI_API_KEY",
        "models_env": "MOONSHOT_MODELS",
        "models": ["kimi-k2-0905-preview", "kimi-k2-turbo-preview", "moonshot-v1-128k"],
        "cn": True,
        "docs": "https://platform.moonshot.cn/console/api-keys",
    },
    "qwen": {
        "name": "阿里通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_env": "DASHSCOPE_API_KEY",
        "alt_key_env": "QWEN_API_KEY",
        "models_env": "QWEN_MODELS",
        "models": ["qwen3-max", "qwen-plus", "qwen-turbo", "qwen3-235b-a22b"],
        "cn": True,
        "docs": "https://bailian.console.aliyun.com/?tab=model#/api-key",
    },
    "siliconflow": {
        "name": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "key_env": "SILICONFLOW_API_KEY",
        "models_env": "SILICONFLOW_MODELS",
        "models": [
            "deepseek-ai/DeepSeek-V3.2-Exp",
            "Qwen/Qwen3-235B-A22B-Instruct-2507",
            "moonshotai/Kimi-K2-Instruct-0905",
        ],
        "cn": True,
        "docs": "https://cloud.siliconflow.cn/account/ak",
    },
    "doubao": {
        "name": "火山方舟（豆包）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "key_env": "ARK_API_KEY",
        "alt_key_env": "DOUBAO_API_KEY",
        "models_env": "ARK_MODELS",
        "models": ["doubao-seed-1.6", "doubao-1.5-pro-32k"],
        "cn": True,
        "docs": "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
    },
    "stepfun": {
        "name": "阶跃星辰 StepFun",
        "base_url": "https://api.stepfun.com/v1",
        "key_env": "STEPFUN_API_KEY",
        "models_env": "STEPFUN_MODELS",
        "models": ["step-2-16k", "step-1v-8k"],
        "cn": True,
        "docs": "https://platform.stepfun.com/interface-key",
    },
    "baichuan": {
        "name": "百川智能",
        "base_url": "https://api.baichuan-ai.com/v1",
        "key_env": "BAICHUAN_API_KEY",
        "models_env": "BAICHUAN_MODELS",
        "models": ["Baichuan4-Turbo", "Baichuan4"],
        "cn": True,
        "docs": "https://platform.baichuan-ai.com/console/apikey",
    },
    # ---------------------------------------------------------- 海外
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
        "models_env": "OPENAI_MODELS",
        "models": ["gpt-5.2", "gpt-5.1", "gpt-5"],
        "efforts": ["low", "medium", "high"],
        "default_effort": "medium",
        "docs": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "base_url": "https://api.anthropic.com/v1",
        "key_env": "ANTHROPIC_API_KEY",
        "models_env": "ANTHROPIC_MODELS",
        "models": ["claude-opus-4-5-20251124", "claude-sonnet-4-5-20250929"],
        "docs": "https://console.anthropic.com/settings/keys",
    },
    "google": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": "GOOGLE_API_KEY",
        "models_env": "GOOGLE_MODELS",
        "models": ["gemini-3-pro", "gemini-2.5-flash"],
        "docs": "https://aistudio.google.com/apikey",
    },
    "xai": {
        "name": "xAI Grok",
        "base_url": "https://api.x.ai/v1",
        "key_env": "XAI_API_KEY",
        "models_env": "XAI_MODELS",
        "models": ["grok-4.1", "grok-4"],
        "efforts": ["low", "medium", "high"],
        "default_effort": "medium",
        "docs": "https://console.x.ai/",
    },
    "openrouter": {
        "name": "OpenRouter 聚合",
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "models_env": "OPENROUTER_MODELS",
        "models": ["anthropic/claude-sonnet-4.5", "openai/gpt-5"],
        "base_url_env": "OPENROUTER_BASE_URL",
        "docs": "https://openrouter.ai/keys",
    },
    # ---------------------------------------------------------- 本地
    "lmstudio": {
        "name": "LM Studio（本地）",
        "base_url": "http://localhost:1234/v1",
        "key_env": "LMSTUDIO_API_KEY",
        "models_env": "LMSTUDIO_MODELS",
        "base_url_env": "LMSTUDIO_BASE_URL",
        "local": "1",
    },
    "llamaswap": {
        "name": "llama-swap（本地）",
        "base_url": "http://localhost:8080/v1",
        "key_env": "LLAMASWAP_API_KEY",
        "models_env": "LLAMASWAP_MODELS",
        "base_url_env": "LLAMASWAP_BASE_URL",
        "local": "1",
    },
    "ollama": {
        "name": "Ollama（本地）",
        "base_url": "http://localhost:11434/v1",
        "key_env": "OLLAMA_API_KEY",
        "models_env": "OLLAMA_MODELS",
        "base_url_env": "OLLAMA_BASE_URL",
        "local": "1",
    },
    "custom": {
        "name": "自定义 OpenAI 兼容",
        "base_url": "http://localhost:5000/v1",
        "key_env": "CUSTOM_API_KEY",
        "models_env": "CUSTOM_MODELS",
        "base_url_env": "CUSTOM_BASE_URL",
    },
}

PLACEHOLDERS = {"", "...", "sk-...", "sk-ant-...", "xai-...", "sk-or-v1-...", "your-key-here"}


def _is_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    v = value.strip().strip('"').strip("'")
    if v in PLACEHOLDERS:
        return True
    # sk-...suffix 之类未替换的模板
    return v.endswith("...") or v.startswith("<") or "your_" in v.lower()


@dataclass
class Settings:
    """全局配置快照。"""

    env: dict[str, str]
    env_path: Path | None
    language: str = ""
    rounds_per_agent: int = 2
    synthesizer: str = "first"      # first | random | rotate
    model_timeout: float = 120.0
    execution_mode: str = "auto"    # auto | parallel | sequential
    report_dir: Path = Path("~/reports")
    export_dir: Path = Path("~/.quorum/exports")
    export_format: str = "md"
    max_tokens: int = 4096
    temperature: float = 0.8
    max_history_chars: int = 24000   # 喂给模型的历史窗口上限（按字符）

    def get(self, key: str, default: str = "") -> str:
        return self.env.get(key, default) or default


def load_settings(env_path: str | Path | None = None) -> Settings:
    """按优先级加载 env：显式路径 > 当前目录 .env > ~/.quorum/.env"""

    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.append(Path.cwd() / ".env")
    candidates.append(Path("~/.quorum/.env").expanduser())

    chosen: Path | None = None
    raw: dict[str, str] = {}
    for cand in candidates:
        if cand.is_file():
            chosen = cand
            raw = {k: v for k, v in dotenv_values(cand).items() if v is not None}
            break

    # 进程环境变量优先级最高（key_env 与 alt_key_env 都要合并，否则
    # shell 里 export ZHIPU_API_KEY 这类同厂别名会完全无效）
    env = dict(raw)
    wanted = list(raw)
    for meta in PROVIDER_DEFS.values():
        for field in ("key_env", "alt_key_env"):
            value = meta.get(field)
            if value:
                wanted.append(value)
    for key in wanted:
        if key in os.environ:
            env[key] = os.environ[key]

    s = Settings(env=env, env_path=chosen)
    s.language = env.get("QUORUM_DEFAULT_LANGUAGE", "").strip()
    s.rounds_per_agent = _int(env.get("QUORUM_ROUNDS_PER_AGENT"), 2)
    s.synthesizer = (env.get("QUORUM_SYNTHESIZER") or "first").strip().lower()
    s.model_timeout = _float(env.get("QUORUM_MODEL_TIMEOUT"), 120.0)
    s.execution_mode = (env.get("QUORUM_EXECUTION_MODE") or "auto").strip().lower()
    s.report_dir = Path(env.get("QUORUM_REPORT_DIR") or "~/reports").expanduser()
    s.export_dir = Path(env.get("QUORUM_EXPORT_DIR") or "~/.quorum/exports").expanduser()
    s.export_format = (env.get("QUORUM_EXPORT_FORMAT") or "md").strip().lower()
    s.max_tokens = _int(env.get("QUORUM_MAX_TOKENS"), 4096)
    s.temperature = _float(env.get("QUORUM_TEMPERATURE"), 0.8)
    s.max_history_chars = _int(env.get("QUORUM_MAX_HISTORY_CHARS"), 24000)
    return s


def _int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _float(value: str | None, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 本地探测

def _probe_models(base_url: str, api_key: str = "", timeout: float = 5.0) -> list[str]:
    """探测 OpenAI 兼容 /v1/models。

    注意：本地服务必须 trust_env=False，否则会被 HTTP(S)_PROXY 劫持导致探测失败。
    """
    sanitize_proxy_env()
    if not _port_open(base_url):
        return []
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(timeout=timeout, trust_env=not is_local_url(base_url)) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                return []
            payload = resp.json()
    except Exception:
        return []
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            mid = item.get("id")
            if isinstance(mid, str) and mid:
                out.append(mid)
    return out


def _port_open(url: str, timeout: float = 0.4) -> bool:
    """本地服务预检：连不上就立刻放弃，避免每次页面加载都等满超时。"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        # localhost:abc / 99999 这类非法端口，直接判定不可用，不要抛给调用方
        return False
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_local_url(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local")


def _probe_ollama(base_url: str, timeout: float = 3.0) -> list[str]:
    """Ollama 原生 /api/tags，比 /v1/models 更全。"""
    sanitize_proxy_env()
    if not _port_open(base_url):
        return []
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    try:
        with httpx.Client(timeout=timeout, trust_env=not is_local_url(root)) as client:
            resp = client.get(root + "/api/tags")
            if resp.status_code != 200:
                return []
            payload = resp.json()
    except Exception:
        return []
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []
    return [m["name"] for m in models if isinstance(m, dict) and m.get("name")]


# ---------------------------------------------------------------- 模型清单

def resolve_key(settings: Settings, meta: dict) -> str:
    """按 key_env → alt_key_env 顺序取第一个真填了的 key。"""
    for field in ("key_env", "alt_key_env"):
        name = meta.get(field)
        if not name:
            continue
        value = (settings.get(name) or "").strip()
        if value and not _is_placeholder(value):
            return value
    return ""


def provider_catalog(settings: Settings) -> list[dict]:
    """全部厂商预设（含未配置 key 的），供前端「厂商 → 模型」两级选择。"""
    out: list[dict] = []
    for provider, meta in PROVIDER_DEFS.items():
        if provider == "custom":
            continue
        key = resolve_key(settings, meta)
        base_url = (settings.get(meta.get("base_url_env", "")) or meta["base_url"]).strip()
        declared = [
            m.strip()
            for m in (settings.get(meta["models_env"]) or "").split(",")
            if m.strip()
        ]
        models = _dedupe(declared + list(meta.get("models") or []))
        out.append(
            {
                "key": provider,
                "name": meta.get("name") or provider,
                "base_url": base_url,
                "models": [m for m in models if not _is_embedding(m)],
                "key_env": meta.get("key_env", ""),
                "alt_key_env": meta.get("alt_key_env", ""),
                "has_key": bool(key),
                "local": bool(meta.get("local")),
                "cn": bool(meta.get("cn")),
                "efforts": list(meta.get("efforts") or []),
                "default_effort": meta.get("default_effort", ""),
                "docs": meta.get("docs", ""),
            }
        )
    # 已配置 key 的厂商排前面，国产品牌优先
    out.sort(key=lambda p: (not p["has_key"], not p["cn"], p["key"]))
    return out


def default_slots(settings: Settings, specs: list[ModelSpec] | None = None) -> dict:
    """首屏默认槽位：已配置的模型里挑两个做正反，第三个（或复用）做裁判。"""
    specs = specs if specs is not None else discover_models(settings)
    remote = [s for s in specs if s.provider not in ("lmstudio", "ollama", "llamaswap")]
    pool = remote or specs
    if not pool:
        return {"pro": [], "con": [], "judge": None}

    pro = pool[0]
    con = pool[1] if len(pool) > 1 else pool[0]
    judge = pool[2] if len(pool) > 2 else con

    def card(spec: ModelSpec, effort: str) -> dict:
        return {"model_key": spec.key, "effort": effort}

    pro_effort = PROVIDER_DEFS.get(pro.provider, {}).get("default_effort", "")
    con_effort = PROVIDER_DEFS.get(con.provider, {}).get("default_effort", "")
    judge_effort = PROVIDER_DEFS.get(judge.provider, {}).get("default_effort", "")
    return {
        "pro": [card(pro, pro_effort)],
        "con": [card(con, con_effort)],
        "judge": card(judge, judge_effort),
    }


def discover_models(settings: Settings, include_local: bool = True) -> list[ModelSpec]:
    """枚举所有可用模型。云端需有 key；本地自动探测。"""

    specs: list[ModelSpec] = []
    seen: set[str] = set()

    for provider, meta in PROVIDER_DEFS.items():
        key = resolve_key(settings, meta)
        is_local = bool(meta.get("local"))

        if not is_local and not key:
            continue
        if is_local and not include_local:
            continue

        base_url = (settings.get(meta.get("base_url_env", "")) or meta["base_url"]).strip()

        declared = [
            m.strip()
            for m in (settings.get(meta["models_env"]) or "").split(",")
            if m.strip()
        ]

        if is_local:
            probed: list[str] = []
            if provider == "ollama":
                probed = _probe_ollama(base_url)
            if not probed:
                probed = _probe_models(base_url, api_key=key)
            models = _dedupe(declared + probed)
            if not models:
                continue
        else:
            # .env 里没写 *_MODELS 时用厂商预置清单
            models = _dedupe(declared + list(meta.get("models") or []))
            if not models:
                continue

        for model in models:
            if _is_embedding(model):
                continue  # embedding 模型不能对话
            spec = ModelSpec(
                provider=provider,
                model=model,
                label=_pretty_label(provider, model),
                base_url=base_url,
                api_key=key,
            )
            if spec.key in seen:
                continue
            # 同一 base_url + 同一模型（如 custom 指向 deepseek）只保留命名更明确的那个
            dup = next(
                (
                    s
                    for s in specs
                    if s.base_url.rstrip("/") == spec.base_url.rstrip("/")
                    and s.model == spec.model
                ),
                None,
            )
            if dup is not None:
                if dup.provider == "custom" and provider != "custom":
                    specs.remove(dup)
                    seen.discard(dup.key)
                    seen.add(spec.key)
                    specs.append(spec)
                continue
            seen.add(spec.key)
            specs.append(spec)

    return specs


def _is_embedding(model: str) -> bool:
    low = model.lower()
    return "embed" in low or "rerank" in low


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def _pretty_label(provider: str, model: str) -> str:
    short = model.rsplit("/", 1)[-1]
    if short.endswith(".gguf"):
        short = short[:-5]
    for suffix in ("-uncensored-hauhaucs-aggressive", "-uncensored-hauhaucs-balanced-mtp"):
        short = short.replace(suffix, "")
    return short


def resolve_model(token: str, specs: list[ModelSpec]) -> ModelSpec | None:
    """把用户输入（序号外的字符串）解析成模型。支持 provider:model / model / 唯一前缀。"""

    token = token.strip()
    if not token:
        return None
    low = token.lower()

    for spec in specs:
        if spec.key.lower() == low:
            return spec
    for spec in specs:
        if spec.model.lower() == low or spec.label.lower() == low:
            return spec
    matches = [s for s in specs if low in s.model.lower() or low in s.label.lower()]
    if len(matches) == 1:
        return matches[0]
    return None
