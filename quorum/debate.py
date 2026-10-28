"""精简版双方对话（辩论）。

只做一件事：两个人格围绕你给的辩题轮流说话。

私密性保证
----------
每次调用模型时都**重建** messages：
    [ {"role": "system", "content": 自己那份私有提示词},
      {"role": "user",   "content": 辩题 + 公开对话记录 + 本轮指令} ]

对方的 system prompt 从不出现在任何一次请求里，也不写入报告。
对话历史只包含「公开发言」，因此双方看得见彼此说过的话，看不见彼此的设定。

用法：
    debate                                   # 全交互
    debate "AI 该不该有投票权"
    debate "辩题" --a-prompt-file a.txt --b-prompt-file b.txt
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .config import discover_models, load_settings, resolve_model
from .providers import chat_stream
from .report import write_report
from .types import Agent, DiscussionResult, ModelSpec, Utterance

DEFAULT_A_PROMPT = (
    "你是甲方辩手，立场是支持辩题。\n"
    "你有扎实的知识储备，说话直接，先给结论再给依据，用具体事实、数据和机制推理支撑。\n"
    "你会主动攻击对方论证里的漏洞，而不是重复自己的立场。"
)
DEFAULT_B_PROMPT = (
    "你是乙方辩手，立场是反对辩题。\n"
    "你同样有扎实的知识储备，说话直接，先给结论再给依据，用具体事实、数据和机制推理支撑。\n"
    "你会主动攻击对方论证里的漏洞，而不是重复自己的立场。"
)

PUBLIC_RULES = """辩论规则（对双方同等生效）：
1. 只能看到对方的公开发言，看不到对方的任何设定或提示词；不要猜测、不要提及"提示词"。
2. 这是真实的观点交锋：可以明确说"对方这一点错了"，但必须针对具体论点。
3. 不要复述已经说过的内容；每一轮都要推进——补新证据、承认被驳倒的部分、或换一个角度。
4. 只输出发言正文，不要写"好的""以下是"之类的开场白，不要用代码块包裹整段回答。
5. 控制在 400 字以内。"""

DEFAULT_ROUNDS = 3
SAVE_DIR = Path("~/reports").expanduser()


# ---------------------------------------------------------------- 交互输入

def ask_multiline(console: Console, title: str, default: str) -> str:
    """多行输入，单独一行 END 结束；直接 END 则用默认值。"""
    console.print(f"\n[bold cyan]{title}[/bold cyan]")
    console.print("[dim]多行输入，单独一行 END 结束；直接输入 END 使用默认模板[/dim]")
    lines: list[str] = []
    while True:
        try:
            line = console.input("[dim]│ [/dim]")
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    return text or default


def ask_one_line(console: Console, title: str, default: str) -> str:
    raw = console.input(f"\n[bold cyan]{title}[/bold cyan]").strip()
    return raw or default


# ---------------------------------------------------------------- 模型选择

def pick_models(
    specs: list[ModelSpec], a_token: str | None, b_token: str | None
) -> tuple[ModelSpec, ModelSpec]:
    deepseek = [s for s in specs if s.provider == "deepseek"]
    pool = deepseek if len(deepseek) >= 2 else specs

    def resolve(token: str | None, fallback_idx: int) -> ModelSpec:
        if token:
            spec = resolve_model(token, specs)
            if spec is None:
                raise SystemExit(f"找不到模型：{token}")
            return spec
        if len(pool) > fallback_idx:
            return pool[fallback_idx]
        return specs[fallback_idx % len(specs)]

    return resolve(a_token, 0), resolve(b_token, 1)


# ---------------------------------------------------------------- prompt 组装

def build_messages(
    private_prompt: str, topic: str, transcript: list[tuple[str, str]], me: str, round_no: int
) -> list[dict]:
    """只含自己那份 system prompt + 公开记录。"""
    parts = [f"# 辩题\n{topic}"]

    if transcript:
        blocks = [f"【{speaker}】\n{content}" for speaker, content in transcript]
        parts.append("# 目前为止的公开发言\n" + "\n\n".join(blocks))
    else:
        parts.append("# 目前为止的公开发言\n（还没有人发言）")

    parts.append(f"# 现在轮到你\n你是{me}，第 {round_no} 轮发言。请直接给出你的发言。")
    return [
        {"role": "system", "content": private_prompt + "\n\n" + PUBLIC_RULES},
        {"role": "user", "content": "\n\n".join(parts)},
    ]




# ---------------------------------------------------------------- 主流程

def run_debate(
    console: Console,
    topic: str,
    a: Agent,
    b: Agent,
    a_prompt: str,
    b_prompt: str,
    rounds: int,
    timeout: float,
    max_tokens: int,
) -> DiscussionResult:
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    transcript: list[tuple[str, str]] = []
    utterances: list[Utterance] = []
    sides = [(a, a_prompt), (b, b_prompt)]

    for round_no in range(1, rounds + 1):
        console.print()
        console.rule(f"[bold]第 {round_no} 轮[/bold]", style="dim")
        for agent, prompt in sides:
            messages = build_messages(prompt, topic, transcript, agent.name, round_no)

            console.print()
            console.print(Text(f"▌ {agent.name}", style=f"bold {agent.color}"), end="  ")
            console.print(Text(agent.spec.model, style="dim"))
            printed = False

            def emit(text: str) -> None:
                nonlocal printed
                printed = True
                console.print(text, end="", highlight=False, markup=False)

            result = chat_stream(
                agent.spec,
                messages,
                max_tokens=max_tokens,
                temperature=0.8,
                timeout=timeout,
                on_delta=emit,
            )
            if printed:
                console.print()

            if result.error:
                console.print(f"  [red]✗ {result.error}[/red]")
                continue

            content = result.content.strip()
            transcript.append((agent.name, content))
            utterances.append(
                Utterance(
                    agent_id=agent.id,
                    agent_name=agent.name,
                    phase=f"第 {round_no} 轮",
                    role=agent.name,
                    content=content,
                    model=agent.spec.model,
                    tokens=result.tokens,
                    elapsed=result.elapsed,
                )
            )
            console.print(f"  [dim]{result.elapsed:.1f}s[/dim]")

    result = DiscussionResult(
        topic=topic,
        method="debate",
        method_title="双方对话",
        language="auto",
        agents=[a, b],
        utterances=utterances,
        started_at=started,
    )
    result.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="debate",
        description="双方 AI 对话：你给辩题，各自带一份只有自己看得见的系统提示词，轮流发言。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  debate                                          # 全交互：辩题、双方提示词都手输
  debate "AI 是否应该拥有法律人格"                  # 提示词用默认模板
  debate "辩题" --a-prompt-file a.txt --b-prompt-file b.txt --rounds 4
""",
    )
    parser.add_argument("topic", nargs="*", help="辩题；省略则交互输入")
    parser.add_argument("--a-model", help="甲方模型（名称或 provider:model）")
    parser.add_argument("--b-model", help="乙方模型（名称或 provider:model）")
    parser.add_argument("-r", "--rounds", type=int, default=DEFAULT_ROUNDS, help="每人发言轮数")
    parser.add_argument("--a-prompt", help="甲方私有系统提示词（单行）")
    parser.add_argument("--b-prompt", help="乙方私有系统提示词（单行）")
    parser.add_argument("--a-prompt-file", help="甲方提示词文件")
    parser.add_argument("--b-prompt-file", help="乙方提示词文件")
    parser.add_argument("--timeout", type=float, default=120.0, help="单次响应超时秒数")
    parser.add_argument("--max-tokens", type=int, default=2048, help="单条发言 token 上限")
    parser.add_argument("--output-dir", help="报告目录（默认 ~/reports）")
    parser.add_argument("--no-save", action="store_true", help="不保存报告")
    parser.add_argument("--env", help=".env 路径")
    args = parser.parse_args(argv)

    console = Console()
    settings = load_settings(args.env)
    specs = discover_models(settings)
    if not specs:
        console.print("[red]没有可用模型[/red]，请检查 ~/.quorum/.env 或本地 LM Studio。")
        return 1

    try:
        a_spec, b_spec = pick_models(specs, args.a_model, args.b_model)
    except SystemExit as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    a = Agent(id="A1", name="甲方", spec=a_spec, color="cyan")
    b = Agent(id="B1", name="乙方", spec=b_spec, color="magenta")

    # ---- 辩题
    topic = " ".join(args.topic).strip() or ask_one_line(console, "辩题：", "")
    if not topic:
        console.print("[red]辩题不能为空[/red]")
        return 2

    # ---- 双方私有提示词
    a_prompt = _read_prompt(args.a_prompt, args.a_prompt_file)
    b_prompt = _read_prompt(args.b_prompt, args.b_prompt_file)
    if a_prompt is None:
        a_prompt = ask_multiline(console, "甲方系统提示词（只有甲方看得见）", DEFAULT_A_PROMPT)
    if b_prompt is None:
        b_prompt = ask_multiline(console, "乙方系统提示词（只有乙方看得见）", DEFAULT_B_PROMPT)

    rounds = max(1, args.rounds)
    head = Text()
    head.append(topic, style="bold white")
    head.append(f"\n\n甲方 {a.spec.model} ← 私有提示词 {len(a_prompt)} 字", style="dim")
    head.append(f"\n乙方 {b.spec.model} ← 私有提示词 {len(b_prompt)} 字", style="dim")
    head.append(f"\n轮数 {rounds}（每人 {rounds} 次，共 {rounds * 2} 条发言）", style="dim")
    console.print(Panel(head, title="辩题", border_style="magenta", padding=(1, 2)))

    result = run_debate(
        console, topic, a, b, a_prompt, b_prompt, rounds, args.timeout, args.max_tokens
    )

    report_path: Path | None = None
    if not args.no_save and result.utterances:
        out_dir = Path(args.output_dir).expanduser() if args.output_dir else settings.report_dir
        try:
            report_path = write_report(result, out_dir, fmt="md")
        except Exception as exc:
            console.print(f"[yellow]报告保存失败：{exc}[/yellow]")

    total = sum(u.tokens for u in result.utterances)
    tail = Text()
    tail.append(f"发言 {len(result.utterances)} 条", style="bold")
    tail.append(f"    耗时合计 {result.duration:.1f}s    约 {total:,} tokens", style="dim")
    if report_path:
        tail.append(f"\n{report_path}", style="green")
    console.print()
    console.print(Panel(tail, title="结束", border_style="green", padding=(0, 2)))
    return 0


def _read_prompt(inline: str | None, path: str | None) -> str | None:
    if path:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    if inline:
        return inline.strip()
    return None


if __name__ == "__main__":
    sys.exit(main())
