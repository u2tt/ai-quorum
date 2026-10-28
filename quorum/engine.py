"""讨论引擎：把 methods 产出的 Step 计划真正跑成一场对话。

职责边界：
- 组装每一轮的 prompt（system 角色 + 讨论历史 + 本轮任务）
- 控制并发（独立的开场发言可并行，互相引用必须串行）
- 记录 Utterance，处理失败与历史截断
- 不负责终端渲染（见 render.py），只通过 EventSink 抛事件
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .config import Settings
from .methods import MethodSpec
from .providers import ChatResult, chat_stream
from .types import Agent, DiscussionResult, Step, Utterance

LOCAL_PROVIDERS = {"lmstudio", "ollama", "llamaswap"}

SYSTEM_TEMPLATE = """你是 {name}，正在参加一场由多个独立 AI 模型组成的圆桌讨论。

讨论方法：{method_title}
你本轮的席位角色：{role}

硬性规则：
1. 你就是 {name}，不要自称"AI 助手"、不要提及"提示词/系统指令"、不要跳出角色。
2. 只输出你的发言正文，不要写"好的""以下是"之类的开场套话，不要用 Markdown 代码块包裹整段回答。
3. 直接给出观点和论据，用具体事实、数字、例子支撑；不确定就说不确定。
4. 其他参与者是与你平等的独立模型，可以明确同意、反对或修正他们的观点，但必须针对他们的具体论点，不要复读。
5. 全文控制在 500 字以内，除非本轮任务另有要求。
6. {language_rule}"""

INDEPENDENT_NOTE = """本轮是独立发言：你还没有看到其他人的观点（或者本轮刻意不参考他人），请独立完成，不要假设别人会说什么。"""

_NUM_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def _fmt_num(value: float) -> str:
    return f"{value:.4g}"


def _numeric_summary(history: list["Utterance"]) -> str:
    """从每位参与者「第一行给出的数值」里抽样本，用代码算中位数。

    德尔菲法的汇总步骤原本只让模型自己口算，结果不可复现；这里把样本抽出来
    由代码计算，模型只负责解释分歧来源。
    """

    samples: list[float] = []
    for utt in history:
        if not utt.ok:
            continue
        for line in utt.content.splitlines():
            if not line.strip():
                continue
            found = _NUM_RE.findall(line)
            if found:
                try:
                    samples.append(float(found[0]))
                except ValueError:
                    pass
                break
    if len(samples) < 2:
        return ""

    ordered = sorted(samples)
    size = len(ordered)
    middle = size // 2
    median = (
        ordered[middle]
        if size % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )
    return (
        "# 代码计算的统计（以这里为准，不要自行口算）\n"
        f"样本数：{size}；中位数：{_fmt_num(median)}；"
        f"最小值：{_fmt_num(ordered[0])}；最大值：{_fmt_num(ordered[-1])}\n"
        "各样本（升序）：" + "、".join(_fmt_num(v) for v in ordered)
    )


@dataclass
class EventSink:
    """终端渲染钩子。全部可选，engine 不关心实现。"""

    on_phase: Callable[[str], None] | None = None
    on_start: Callable[[Agent, Step], None] | None = None
    on_delta: Callable[[Agent, str], None] | None = None
    on_end: Callable[[Utterance], None] | None = None
    on_note: Callable[[str], None] | None = None

    def phase(self, title: str) -> None:
        if self.on_phase:
            self.on_phase(title)

    def start(self, agent: Agent, step: Step) -> None:
        if self.on_start:
            self.on_start(agent, step)

    def delta(self, agent: Agent, text: str) -> None:
        if self.on_delta:
            self.on_delta(agent, text)

    def end(self, utt: Utterance) -> None:
        if self.on_end:
            self.on_end(utt)

    def note(self, text: str) -> None:
        if self.on_note:
            self.on_note(text)


@dataclass
class Engine:
    settings: Settings
    topic: str
    method: MethodSpec
    agents: list[Agent]
    rounds: int = 2
    synthesizer_index: int = 0
    max_history_chars: int = 0      # 0 表示沿用 Settings.max_history_chars
    sink: EventSink = field(default_factory=EventSink)
    _final_utterance: Utterance | None = field(default=None, repr=False, compare=False)


    # ------------------------------------------------------------ 入口

    def run(self) -> DiscussionResult:
        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        steps = self.method.build(self.topic, self.agents, self.rounds, self.synthesizer_index)
        utterances: list[Utterance] = []
        final_index = len(steps) - 1
        self._final_utterance = None

        index = 0
        while index < len(steps):
            batch = self._parallel_batch(steps, index)
            if batch:
                phase = batch[0].phase
                self.sink.phase(phase)
                produced = self._run_parallel(batch)
                utterances.extend(produced)
                for offset, utt in enumerate(produced):
                    if index + offset == final_index:
                        self._final_utterance = utt
                index += len(batch)
            else:
                step = steps[index]
                self.sink.phase(step.phase)
                utt = self._run_one(step, utterances)
                utterances.append(utt)
                if index == final_index:
                    self._final_utterance = utt
                index += 1

        result = DiscussionResult(
            topic=self.topic,
            method=self.method.name,
            method_title=self.method.title,
            language=self.settings.language or "auto",
            agents=self.agents,
            utterances=utterances,
            started_at=started,
        )
        result.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result.synthesis = self._pick_synthesis(utterances)
        return result

    # ------------------------------------------------------------ 串行一轮

    def _run_one(self, step: Step, history: list[Utterance]) -> Utterance:
        agent = self.agents[step.agent_index]
        messages = self._build_messages(agent, step, history)
        self.sink.start(agent, step)

        def push(text: str) -> None:
            self.sink.delta(agent, text)

        res = chat_stream(
            agent.spec,
            messages,
            max_tokens=self.settings.max_tokens,
            temperature=self.settings.temperature,
            timeout=self.settings.model_timeout,
            on_delta=push,
        )
        utt = self._to_utterance(agent, step, res, len(history))
        self.sink.end(utt)
        return utt

    # ------------------------------------------------------------ 并行批次

    def _parallel_batch(self, steps: list[Step], index: int) -> list[Step]:
        """找出可并行的连续独立步（visibility=none 且同 group）。"""

        head = steps[index]
        if head.visibility != "none":
            return []
        if self.settings.execution_mode == "sequential":
            return []

        batch = [head]
        cursor = index + 1
        while cursor < len(steps):
            nxt = steps[cursor]
            if nxt.visibility == "none" and nxt.group == head.group and nxt.phase == head.phase:
                batch.append(nxt)
                cursor += 1
            else:
                break
        if len(batch) < 2:
            return []
        if self.settings.execution_mode == "auto":
            # 本地模型共享显存，默认串行；只按本批次实际参与者的 provider 判定，
            # 避免「编队里有一个本地模型就把全场首轮都串行化」
            if any(
                self.agents[step.agent_index].spec.provider in LOCAL_PROVIDERS
                for step in batch
            ):
                return []
        return batch

    def _run_parallel(self, batch: list[Step]) -> list[Utterance]:
        # 并行时不做逐字渲染（多路输出会互相打断），完成后按顺序整块输出
        results: dict[int, Utterance] = {}

        def work(pos: int, step: Step) -> None:
            agent = self.agents[step.agent_index]
            messages = self._build_messages(agent, step, [])
            res = chat_stream(
                agent.spec,
                messages,
                max_tokens=self.settings.max_tokens,
                temperature=self.settings.temperature,
                timeout=self.settings.model_timeout,
            )
            results[pos] = self._to_utterance(agent, step, res, 0)

        with ThreadPoolExecutor(max_workers=min(6, len(batch))) as pool:
            futures = [pool.submit(work, pos, step) for pos, step in enumerate(batch)]
            for fut in futures:
                fut.result()

        ordered: list[Utterance] = []
        for pos, step in enumerate(batch):
            utt = results[pos]
            agent = self.agents[step.agent_index]
            self.sink.start(agent, step)
            if utt.ok:
                self.sink.delta(agent, utt.content)
            self.sink.end(utt)
            ordered.append(utt)
        return ordered

    # ------------------------------------------------------------ prompt 组装

    def _build_messages(self, agent: Agent, step: Step, history: list[Utterance]) -> list[dict]:
        language_rule = (
            f"全程使用{self.settings.language}回答。"
            if self.settings.language
            else "使用与讨论话题相同的语言回答；话题是中文就用中文。"
        )
        system = SYSTEM_TEMPLATE.format(
            name=agent.name,
            method_title=self.method.title,
            role=step.role,
            language_rule=language_rule,
        )

        parts = [f"# 讨论话题\n{self.topic}"]

        visible = self._visible_history(agent, step, history)
        if visible:
            parts.append("# 已发生的发言（按时间顺序）\n" + visible)
        if step.meta.get("aggregate") == "median":
            stats = _numeric_summary(history)
            if stats:
                parts.append(stats)
        if step.visibility == "none":
            parts.append(INDEPENDENT_NOTE)

        parts.append(f"# 你现在的任务（{step.role}）\n{step.instruction}")
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n\n".join(parts)},
        ]

    def _visible_history(self, agent: Agent, step: Step, history: list[Utterance]) -> str:
        if step.visibility == "none" or not history:
            return ""
        if step.visibility == "self":
            picked = [u for u in history if u.agent_id == agent.id]
        else:
            picked = history
        if not picked:
            return ""

        limit = self.max_history_chars or getattr(self.settings, "max_history_chars", 0) or 24000
        anonymous = bool(step.meta.get("anonymous"))
        order = {a.id: pos + 1 for pos, a in enumerate(self.agents)}

        blocks: list[str] = []
        total = 0
        dropped = 0
        for utt in reversed(picked):
            if not utt.ok:
                continue
            label = f"参与者{order.get(utt.agent_id, 0)}" if anonymous else utt.agent_name
            head = f"【{label} · {utt.role}】\n"
            block = head + utt.content
            if total + len(block) > limit:
                if not blocks:
                    # 最新一条自身就超限：截断它，而不是整条丢弃或无视上限
                    room = max(0, limit - len(head) - 12)
                    block = head + utt.content[:room] + "…（已截断）"
                    blocks.append(block)
                    total += len(block)
                    continue
                dropped += 1
                continue
            blocks.append(block)
            total += len(block)
        blocks.reverse()
        text = "\n\n".join(blocks)
        if dropped:
            text = f"（历史窗口已满，省略了 {dropped} 条发言）\n\n" + text
        return text

    # ------------------------------------------------------------ 收尾

    def _to_utterance(self, agent: Agent, step: Step, res: ChatResult, rounds: int) -> Utterance:
        return Utterance(
            agent_id=agent.id,
            agent_name=agent.name,
            phase=step.phase,
            role=step.role,
            content=res.content,
            model=agent.spec.model,
            rounds=rounds,
            tokens=res.tokens,
            elapsed=res.elapsed,
            error=res.error,
        )

    def _pick_synthesis(self, utterances: list[Utterance]) -> str:
        """取「收尾步骤」的发言作为结论。

        早先的实现按 synthesizer_index 找该 agent 的最后一条发言，合成步失败时
        会把中途发言当成结论（socratic 还会取到回答者的修正）。现在以计划里的
        最后一步为准，失败就明确报空，不再拿别的发言顶替。
        """

        final = self._final_utterance
        if final is not None:
            if final.ok:
                return final.content
            self.sink.note("收尾步骤失败，未生成结论：" + (final.error or "未知原因"))
            return ""
        for utt in reversed(utterances):
            if utt.ok:
                return utt.content
        return ""
