"""命令行入口：交互向导 + 一次性执行两种模式。"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from rich.console import Console

from .config import Settings, discover_models, load_settings, resolve_model
from .engine import Engine, EventSink
from .methods import get_method, method_table
from .render import AGENT_COLORS, Renderer
from .report import write_report
from .types import Agent, DiscussionResult, ModelSpec


# ---------------------------------------------------------------- 参数

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aiq",
        description="多模型 AI 圆桌讨论：让不同模型就同一议题互相辩论并收敛出结论。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  aiq                                      # 交互向导
  aiq "该不该读博" -m 1,2 -M oxford
  aiq "重构还是重写" -m deepseek-flash,deepseek-v4-pro -M tradeoff --rounds 2
  aiq --list-models
""",
    )
    p.add_argument("topic", nargs="*", help="讨论议题（省略则进入交互向导）")
    p.add_argument("-m", "--models", help="模型：序号(1,2) / 名称(逗号分隔) / all")
    p.add_argument("-M", "--method", default="standard", help="讨论方法（默认 standard）")
    p.add_argument("-r", "--rounds", type=int, help="讨论轮数（默认读 .env）")
    p.add_argument("-s", "--synth", help="综合者：模型序号 / 模型名 / first / random / rotate")
    p.add_argument("--env", help="指定 .env 路径（默认 ~/.quorum/.env）")
    p.add_argument("--timeout", type=float, help="单模型超时秒数")
    p.add_argument("--max-tokens", type=int, help="单条发言最大 token")
    p.add_argument("--output-dir", help="报告输出目录")
    p.add_argument("--format", dest="fmt", choices=["md", "text", "json"], help="报告格式")
    p.add_argument("--sequential", action="store_true", help="强制串行执行")
    p.add_argument("--parallel", action="store_true", help="强制并行执行")
    p.add_argument("--no-save", action="store_true", help="不落盘报告")
    p.add_argument("--list-models", action="store_true", help="列出可用模型后退出")
    p.add_argument("--list-methods", action="store_true", help="列出讨论方法后退出")
    p.add_argument("--dry-run", action="store_true", help="只打印讨论计划，不调用模型")
    p.add_argument("-v", "--verbose", action="store_true", help="显示每条发言的 token 与耗时")
    return p


# ---------------------------------------------------------------- 选择逻辑

def parse_selection(raw: str, specs: list[ModelSpec]) -> list[ModelSpec]:
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.lower() in ("all", "*"):
        return list(specs)

    picked: list[ModelSpec] = []
    for token in raw.replace("，", ",").split(","):
        token = token.strip()
        if not token:
            continue
        # 区间 1-3
        if "-" in token and token.replace("-", "").isdigit():
            lo, hi = token.split("-", 1)
            for idx in range(int(lo), int(hi) + 1):
                if 1 <= idx <= len(specs):
                    _push(picked, specs[idx - 1])
            continue
        if token.isdigit():
            idx = int(token)
            if 1 <= idx <= len(specs):
                _push(picked, specs[idx - 1])
            continue
        spec = resolve_model(token, specs)
        if spec:
            _push(picked, spec)
        else:
            raise ValueError(f"无法识别的模型：{token}")
    return picked


def _push(items: list[ModelSpec], spec: ModelSpec) -> None:
    if spec not in items:
        items.append(spec)


def make_agents(specs: list[ModelSpec]) -> list[Agent]:
    agents: list[Agent] = []
    used: dict[str, int] = {}
    for idx, spec in enumerate(specs, 1):
        base = spec.label
        used[base] = used.get(base, 0) + 1
        name = base if used[base] == 1 else f"{base}#{used[base]}"
        agents.append(
            Agent(
                id=f"A{idx}",
                name=name,
                spec=spec,
                color=AGENT_COLORS[(idx - 1) % len(AGENT_COLORS)],
            )
        )
    return agents


def pick_synth_index(value: str | None, agents: list[Agent], settings: Settings) -> int:
    if value:
        v = value.strip().lower()
        if v == "first":
            return 0
        if v == "random":
            return random.randrange(len(agents))
        if v == "rotate":
            return 0
        if v.isdigit() and 1 <= int(v) <= len(agents):
            return int(v) - 1
        for idx, agent in enumerate(agents):
            if v in agent.spec.model.lower() or v == agent.name.lower():
                return idx
        raise ValueError(f"无法识别的综合者：{value}")

    mode = (settings.synthesizer or "first").lower()
    if mode == "random":
        return random.randrange(len(agents))
    return 0


# ---------------------------------------------------------------- 交互向导

def wizard(console: Console, renderer: Renderer, settings: Settings,
           specs: list[ModelSpec]) -> tuple[list[Agent], str, str, int]:
    renderer.models_table(specs)
    default_models = [s for s in specs if s.provider == "deepseek"][:2] or specs[:2]
    hint = ",".join(str(specs.index(s) + 1) for s in default_models) or "1"
    raw = console.input(
        f"\n[bold cyan]选择参与模型[/bold cyan]（序号，逗号分隔，回车默认 {hint}）："
    ).strip()
    chosen = parse_selection(raw or hint, specs)
    if len(chosen) < 1:
        raise SystemExit("至少选择一个模型")

    renderer.method_table(method_table())
    methods = method_table()
    default_method = "standard"
    raw_method = console.input(
        f"\n[bold cyan]选择讨论方法[/bold cyan]（序号或名称，回车默认 {default_method}）："
    ).strip()
    method_name = _resolve_method(raw_method or default_method, methods)

    rounds = settings.rounds_per_agent
    raw_rounds = console.input(
        f"[bold cyan]讨论轮数[/bold cyan]（回车默认 {rounds}）："
    ).strip()
    if raw_rounds.isdigit() and int(raw_rounds) > 0:
        rounds = int(raw_rounds)

    topic = console.input("\n[bold cyan]讨论议题[/bold cyan]：").strip()
    if not topic:
        raise SystemExit("议题不能为空")

    return make_agents(chosen), method_name, topic, rounds


def _resolve_method(raw: str, methods: list) -> str:
    if not raw:
        return "standard"
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(methods):
            return methods[idx - 1].name
        raise ValueError(f"方法序号超出范围：{raw}")
    for m in methods:
        if raw.lower() in (m.name.lower(), m.title):
            return m.name
    names = ", ".join(m.name for m in methods)
    raise ValueError(f"未知方法：{raw}（可用：{names}）")


# ---------------------------------------------------------------- 执行

def run_discussion(
    console: Console,
    renderer: Renderer,
    settings: Settings,
    agents: list[Agent],
    method_name: str,
    topic: str,
    rounds: int,
    synth_index: int,
    dry_run: bool = False,
) -> tuple[DiscussionResult, Path | None]:
    method = get_method(method_name)
    if len(agents) < method.min_agents:
        raise SystemExit(
            f"{method.title} 至少需要 {method.min_agents} 个模型，当前只选了 {len(agents)} 个"
        )
    if method.needs_even and len(agents) % 2 != 0:
        raise SystemExit(f"{method.title} 需要偶数个模型（正反方对等），当前 {len(agents)} 个")

    renderer.roster(agents)
    renderer.topic_header(topic, method.title, rounds, method.fixed_rounds)

    if dry_run:
        steps = method.build(topic, agents, rounds, synth_index)
        table_lines = []
        for i, step in enumerate(steps, 1):
            agent = agents[step.agent_index]
            table_lines.append(
                f"{i:>2}. [{step.phase}] {agent.name} — {step.role}"
                f"{'（独立）' if step.visibility == 'none' else ''}"
            )
        console.print("\n".join(table_lines))
        console.print(f"\n[dim]共 {len(steps)} 步（dry-run，未调用模型）[/dim]")
        empty = DiscussionResult(
            topic=topic, method=method.name, method_title=method.title,
            language=settings.language or "auto", agents=agents, utterances=[],
            started_at="",
        )
        return empty, None

    sink = EventSink(
        on_phase=renderer.on_phase,
        on_start=renderer.on_start,
        on_delta=renderer.on_delta,
        on_end=renderer.on_end,
        on_note=renderer.on_note,
    )
    engine = Engine(
        settings=settings,
        topic=topic,
        method=method,
        agents=agents,
        rounds=rounds,
        synthesizer_index=synth_index,
        sink=sink,
    )

    result = engine.run()

    report_path: Path | None = None
    if not dry_run:
        try:
            report_path = write_report(
                result, settings.report_dir, fmt=settings.export_format
            )
        except Exception as exc:  # 报告失败不影响讨论结果
            console.print(f"[yellow]报告写入失败：{exc}[/yellow]")
    renderer.summary(result, report_path)
    return result, report_path


def apply_overrides(settings: Settings, args: argparse.Namespace) -> None:
    if args.timeout:
        settings.model_timeout = args.timeout
    if args.max_tokens:
        settings.max_tokens = args.max_tokens
    if args.output_dir:
        settings.report_dir = Path(args.output_dir).expanduser()
    if args.fmt:
        settings.export_format = args.fmt
    if args.sequential:
        settings.execution_mode = "sequential"
    if args.parallel:
        settings.execution_mode = "parallel"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()
    renderer = Renderer(console, verbose=args.verbose)

    try:
        settings = load_settings(args.env)
    except Exception as exc:
        console.print(f"[red]读取配置失败：{exc}[/red]")
        return 1

    apply_overrides(settings, args)

    if args.list_methods:
        renderer.method_table(method_table())
        return 0

    specs = discover_models(settings)
    if args.list_models:
        renderer.banner(settings, specs)
        if not specs:
            console.print("[yellow]没有发现可用模型，请检查 .env 中的 API key。[/yellow]")
            return 1
        renderer.models_table(specs)
        return 0

    renderer.banner(settings, specs)
    if not specs:
        console.print(
            "[red]未发现可用模型。[/red]请在 .env 中配置至少一个 provider 的 API key，"
            "或启动 LM Studio / Ollama 本地服务。"
        )
        return 1

    topic = " ".join(args.topic).strip()

    try:
        if topic and args.models:
            chosen = parse_selection(args.models, specs)
            agents = make_agents(chosen)
            rounds = args.rounds or settings.rounds_per_agent
            synth_index = pick_synth_index(args.synth, agents, settings)
            method_name = _resolve_method(args.method, method_table())
            run_discussion(
                console, renderer, settings, agents, method_name,
                topic, rounds, synth_index, args.dry_run,
            )
            return 0

        # 交互模式：可以连续开多场
        agents, method_name, topic, rounds = wizard(console, renderer, settings, specs)
        synth_index = pick_synth_index(args.synth, agents, settings)

        while True:
            run_discussion(
                console, renderer, settings, agents, method_name,
                topic, rounds, synth_index, args.dry_run,
            )
            try:
                nxt = console.input(
                    "\n[bold cyan]下一个议题[/bold cyan]（直接回车退出，输入 /models 换模型）："
                ).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not nxt:
                break
            if nxt == "/models":
                agents, method_name, topic, rounds = wizard(console, renderer, settings, specs)
                synth_index = pick_synth_index(args.synth, agents, settings)
                continue
            topic = nxt
    except KeyboardInterrupt:
        console.print("\n[dim]已中断[/dim]")
        return 130
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
