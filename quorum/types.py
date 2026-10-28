"""共享数据结构。所有模块只依赖本文件的数据类，避免循环导入。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ModelSpec:
    """一个可调用的具体模型。"""

    provider: str          # deepseek / openai / anthropic / lmstudio / ollama / ...
    model: str             # 传给 API 的 model 字段
    label: str             # 终端展示名
    base_url: str = ""
    api_key: str = ""

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(slots=True)
class Agent:
    """参与讨论的一位发言者。"""

    id: str                # A1 / A2 ...
    name: str              # 展示名，默认取模型短名
    spec: ModelSpec
    color: str = "cyan"

    @property
    def model(self) -> str:
        return self.spec.model


@dataclass(slots=True)
class Utterance:
    """一次发言记录。"""

    agent_id: str
    agent_name: str
    phase: str
    role: str
    content: str
    model: str = ""
    rounds: int = 0
    tokens: int = 0
    elapsed: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(slots=True)
class Step:
    """讨论计划中的一步：谁、在什么阶段、以什么角色、做什么。"""

    agent_index: int
    phase: str
    role: str
    instruction: str
    visibility: str = "all"     # all=可见全部历史 / self=只看自己发言 / none=完全独立
    group: int = 0              # 同一 group 且 visibility=none 的步可并行执行
    meta: dict = field(default_factory=dict)


@dataclass(slots=True)
class DiscussionResult:
    """一场讨论的完整结果。"""

    topic: str
    method: str
    method_title: str
    language: str
    agents: list[Agent]
    utterances: list[Utterance]
    started_at: str
    finished_at: str = ""
    synthesis: str = ""

    @property
    def duration(self) -> float:
        return sum(u.elapsed for u in self.utterances)
