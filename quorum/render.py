"""终端渲染层（rich）。流式输出 + 阶段分隔 + 统计卡片。"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import Settings
from .types import Agent, DiscussionResult, ModelSpec, Step, Utterance

AGENT_COLORS = ["cyan", "magenta", "green", "yellow", "blue", "bright_red", "bright_white", "dark_orange"]


class Renderer:
    def __init__(self, console: Console | None = None, verbose: bool = False) -> None:
        self.console = console or Console()
        self.verbose = verbose
        self._streaming = False
        self._first_delta = True
        self._current: Agent | None = None

    # ---------------------------------------------------------- 静态视图

    def banner(self, settings: Settings, specs: list[ModelSpec]) -> None:
        title = Text("AI QUORUM", style="bold cyan")
        title.append("  ·  多模型圆桌讨论", style="dim")
        env_note = str(settings.env_path) if settings.env_path else "未找到 .env"
        self.console.print(Panel(title, subtitle=f"config: {env_note}", border_style="cyan"))

    def models_table(self, specs: list[ModelSpec]) -> None:
        table = Table(title="可用模型", border_style="dim", header_style="bold cyan")
        table.add_column("#", justify="right", style="dim", width=3)
        table.add_column("模型", style="bold")
        table.add_column("Provider", style="cyan")
        table.add_column("类型", style="dim")
        for idx, spec in enumerate(specs, 1):
            kind = "本地" if spec.provider in ("lmstudio", "ollama", "llamaswap") else "云端"
            table.add_row(str(idx), spec.label, spec.provider, kind)
        self.console.print(table)

    def method_table(self, methods: list) -> None:
        table = Table(title="讨论方法", border_style="dim", header_style="bold cyan")
        table.add_column("#", justify="right", style="dim", width=3)
        table.add_column("名称", style="bold")
        table.add_column("最少模型", justify="center", style="dim")
        table.add_column("说明")
        for idx, m in enumerate(methods, 1):
            need = f"{m.min_agents}+" + ("（需偶数）" if m.needs_even else "")
            table.add_row(str(idx), m.title, need, m.description)
        self.console.print(table)

    def roster(self, agents: list[Agent]) -> None:
        table = Table(border_style="dim", show_header=True, header_style="bold cyan")
        table.add_column("席位", style="dim", width=4)
        table.add_column("代号")
        table.add_column("模型")
        for agent in agents:
            table.add_row(agent.id, Text(agent.name, style=f"bold {agent.color}"), agent.spec.model)
        self.console.print(table)

    # ---------------------------------------------------------- 讨论过程

    def topic_header(
        self, topic: str, method_title: str, rounds: int, fixed_rounds: bool = False
    ) -> None:
        body = Text()
        body.append(topic.strip(), style="bold white")
        rounds_text = "固定轮次（不受 --rounds 影响）" if fixed_rounds else f"讨论轮数：{rounds}"
        body.append(f"\n\n方法：{method_title}    {rounds_text}", style="dim")
        self.console.print(Panel(body, title="议题", border_style="magenta", padding=(1, 2)))

    def on_phase(self, title: str) -> None:
        if self._streaming:
            self.console.print()
            self._streaming = False
        self.console.print()
        self.console.rule(f"[bold]{title}[/bold]", style="dim")

    def on_start(self, agent: Agent, step: Step) -> None:
        if self._streaming:
            self.console.print()
        label = Text()
        label.append(f"▌ {agent.name}", style=f"bold {agent.color}")
        label.append(f"  {agent.id} · {step.role}", style="dim")
        self.console.print(label)
        self._current = agent
        self._first_delta = True
        self._streaming = True

    def on_delta(self, agent: Agent, text: str) -> None:
        if self._first_delta:
            self._first_delta = False
        self.console.print(text, end="", highlight=False, markup=False)

    def on_end(self, utt: Utterance) -> None:
        if self._streaming:
            self.console.print()
        self._streaming = False
        if utt.error:
            self.console.print(f"  [red]✗ 调用失败：{escape(str(utt.error))}[/red]")
            return
        if self.verbose:
            self.console.print(
                f"  [dim]{utt.model} · {utt.elapsed:.1f}s · {utt.tokens} tokens[/dim]"
            )
        else:
            self.console.print(f"  [dim]{utt.elapsed:.1f}s[/dim]")

    def on_note(self, text: str) -> None:
        if self._streaming:
            self.console.print()
            self._streaming = False
        self.console.print(f"[dim]{text}[/dim]")

    # ---------------------------------------------------------- 收尾

    def summary(self, result: DiscussionResult, report_path: Path | None = None) -> None:
        total_tokens = sum(u.tokens for u in result.utterances)
        failed = sum(1 for u in result.utterances if not u.ok)
        body = Text()
        body.append(f"发言 {len(result.utterances)} 条", style="bold")
        body.append(
            f"    模型耗时合计 {result.duration:.1f}s    约 {total_tokens:,} tokens", style="dim"
        )
        if failed:
            body.append(f"    失败 {failed} 条", style="red")
        if report_path:
            body.append(f"\n报告：{report_path}", style="green")
        self.console.print()
        self.console.print(Panel(body, title="完成", border_style="green", padding=(0, 2)))

    def synthesis(self, text: str) -> None:
        if not text:
            return
        self.console.print()
        self.console.print(Markdown(text), justify="left")
