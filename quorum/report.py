"""报告渲染：把 DiscussionResult 导出为 Markdown / 纯文本 / JSON。

约定：
1. Markdown 顶部带 YAML front matter，便于后续被静态站点或脚本二次消费
2. 正文按 phase 分组，相邻同 phase 自动合并为一个二级标题
3. 正文里的行首 `#` / `---` / ``` 等会破坏文档结构的符号做转义，保证渲染安全
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .types import Agent, DiscussionResult, Utterance

# ---------------------------------------------------------------- 小工具

_SETEXT_OR_RULE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,}|={3,})\s*$")
_BLANK = "\u3000"  # 全角空格，用于空内容占位，避免出现空段落


def _one_line(text: str) -> str:
    """压成单行，用于标题、表格单元格等不允许换行的位置。"""
    return " ".join(str(text).split())


def _yaml_scalar(value: str) -> str:
    """输出安全的 YAML 双引号标量。"""
    text = _one_line(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _cell(value: str) -> str:
    """表格单元格：转义竖线并压成单行。"""
    return _one_line(value).replace("|", "\\|")


def _escape_md_line(line: str) -> str:
    stripped = line.lstrip()
    if not stripped:
        return line
    pad = line[: len(line) - len(stripped)]
    # 行首 # 会被解析成标题、打乱报告的层级，必须转义
    if stripped.startswith("#"):
        return f"{pad}\\{stripped}"
    # 未闭合的代码围栏会吞掉后续所有内容，同样转义
    if stripped.startswith("```") or stripped.startswith("~~~"):
        return f"{pad}\\{stripped}"
    if _SETEXT_OR_RULE.match(stripped):
        # 行首 --- / === 会被解析成分隔线或 setext 标题，转义首字符
        return f"{pad}\\{stripped}"
    # 有序/无序列表是正常正文，保持原样（曾错误转成 "\1." 破坏列表）
    return line


def _escape_md(text: str) -> str:
    """转义正文中可能破坏 Markdown 结构的行首符号。"""
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(_escape_md_line(line) for line in normalized.split("\n"))


def _fmt_elapsed(seconds: float) -> str:
    return f"{float(seconds or 0):.1f}s"


def _fmt_duration(seconds: float) -> str:
    total = float(seconds or 0)
    if total < 60:
        return _fmt_elapsed(total)
    minutes, sec = divmod(int(round(total)), 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{sec:02d}s"


def _phase_key(utterance: Utterance) -> str:
    return (utterance.phase or "发言").strip() or "发言"


def _model_names(agents: list[Agent]) -> list[str]:
    names: list[str] = []
    for agent in agents:
        model = agent.model or agent.spec.label
        if model and model not in names:
            names.append(model)
    return names


def _grouped(utterances: list[Utterance]) -> list[tuple[str, list[Utterance]]]:
    """把按时间顺序排列的发言按相邻同 phase 聚簇。"""
    groups: list[tuple[str, list[Utterance]]] = []
    for utterance in utterances:
        key = _phase_key(utterance)
        if groups and groups[-1][0] == key:
            groups[-1][1].append(utterance)
        else:
            groups.append((key, [utterance]))
    return groups


def _agent_stats(result: DiscussionResult) -> list[tuple[Agent, int, float]]:
    counts: dict[str, int] = {}
    elapsed: dict[str, float] = {}
    for utterance in result.utterances:
        counts[utterance.agent_id] = counts.get(utterance.agent_id, 0) + 1
        elapsed[utterance.agent_id] = elapsed.get(utterance.agent_id, 0.0) + float(
            utterance.elapsed or 0.0
        )
    return [
        (agent, counts.get(agent.id, 0), elapsed.get(agent.id, 0.0)) for agent in result.agents
    ]


# ---------------------------------------------------------------- Markdown

def _front_matter(result: DiscussionResult, meta: dict | None) -> list[str]:
    lines: list[str] = ["---"]
    lines.append(f"topic: {_yaml_scalar(result.topic)}")
    lines.append(f"method: {_yaml_scalar(result.method)}")
    lines.append(f"method_title: {_yaml_scalar(result.method_title)}")
    lines.append(f"started_at: {_yaml_scalar(result.started_at)}")
    lines.append(f"finished_at: {_yaml_scalar(result.finished_at)}")
    lines.append(f"language: {_yaml_scalar(result.language)}")
    lines.append(f"models: {_yaml_scalar(', '.join(_model_names(result.agents)))}")
    lines.append(f"rounds: {len(result.utterances)}")
    for key, value in (meta or {}).items():
        if value is None:
            continue
        lines.append(f"{key}: {_yaml_scalar(str(value))}")
    lines.append("---")
    return lines


def _utterance_md(utterance: Utterance) -> list[str]:
    head = f"### {_one_line(utterance.agent_name)}（{_one_line(utterance.role)}）"
    lines: list[str] = [head, ""]
    body = (utterance.content or "").strip("\n")
    if not utterance.ok:
        lines.append(f"> ⚠️ 调用失败：{_one_line(utterance.error or '未知错误')}")
        lines.append("")
        if body.strip():
            lines.append(_escape_md(body))
            lines.append("")
    else:
        lines.append(_escape_md(body) if body.strip() else _BLANK)
        lines.append("")
    parts = [
        _one_line(utterance.model) or "-",
        _fmt_elapsed(utterance.elapsed),
        f"{int(utterance.tokens or 0)} tokens",
    ]
    lines.append(f"*{' · '.join(parts)}*")
    lines.append("")
    return lines


def to_markdown(result: DiscussionResult, meta: dict | None = None) -> str:
    """渲染为 Markdown（带 YAML front matter）。"""

    lines: list[str] = []
    lines.extend(_front_matter(result, meta))
    lines.append("")
    lines.append(f"# {_one_line(result.topic) or '（无主题）'}")
    lines.append("")

    summary = [
        f"- 方法：{_one_line(result.method_title) or _one_line(result.method)}"
        f"（`{_one_line(result.method)}`）",
        f"- 语言：{_one_line(result.language) or '-'}",
        f"- 时间：{_one_line(result.started_at) or '-'} → {_one_line(result.finished_at) or '-'}",
        f"- 总耗时：{_fmt_duration(result.duration)}",
        f"- 发言总数：{len(result.utterances)}",
    ]
    lines.extend(summary)
    lines.append("")

    lines.append("## 参与者")
    lines.append("")
    lines.append("| 位次 | Agent | 模型 | 发言数 | 累计耗时 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for index, (agent, count, elapsed) in enumerate(_agent_stats(result), start=1):
        lines.append(
            f"| {index} | {_cell(agent.name)}（{_cell(agent.id)}） | {_cell(agent.model)} "
            f"| {count} | {_fmt_elapsed(elapsed)} |"
        )
    lines.append("")

    for phase, utterances in _grouped(result.utterances):
        lines.append(f"## 阶段：{_one_line(phase)}")
        lines.append("")
        for utterance in utterances:
            lines.extend(_utterance_md(utterance))

    lines.append("## 结论")
    lines.append("")
    synthesis = (result.synthesis or "").strip()
    if synthesis:
        lines.append(_escape_md(synthesis))
    else:
        lines.append("（无综合结论）")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 纯文本

def _rule(char: str = "-", width: int = 68) -> str:
    return char * width


def to_text(result: DiscussionResult) -> str:
    """渲染为纯文本，不含任何 Markdown 标记。"""

    lines: list[str] = []
    lines.append(_rule("="))
    lines.append(_one_line(result.topic) or "（无主题）")
    lines.append(_rule("="))
    lines.append("")
    lines.append(f"方法     : {_one_line(result.method_title) or _one_line(result.method)}"
                 f"  [{_one_line(result.method)}]")
    lines.append(f"语言     : {_one_line(result.language) or '-'}")
    lines.append(f"开始时间 : {_one_line(result.started_at) or '-'}")
    lines.append(f"结束时间 : {_one_line(result.finished_at) or '-'}")
    lines.append(f"总耗时   : {_fmt_duration(result.duration)}")
    lines.append(f"模型     : {', '.join(_model_names(result.agents)) or '-'}")
    lines.append(f"发言总数 : {len(result.utterances)}")
    lines.append("")

    lines.append(_rule("-") + " 参与者 " + _rule("-"))
    for index, (agent, count, elapsed) in enumerate(_agent_stats(result), start=1):
        lines.append(
            f"  {index}. {_one_line(agent.name)} ({_one_line(agent.id)}) "
            f"[{_one_line(agent.model)}]  发言 {count} 次  累计 {_fmt_elapsed(elapsed)}"
        )
    lines.append("")

    for phase, utterances in _grouped(result.utterances):
        lines.append(_rule("=") + f" 阶段：{_one_line(phase)} " + _rule("="))
        lines.append("")
        for utterance in utterances:
            lines.append(
                f"[{_one_line(utterance.agent_id)}] {_one_line(utterance.agent_name)}"
                f"（{_one_line(utterance.role)}）"
            )
            lines.append(_rule("-"))
            body = (utterance.content or "").strip("\n")
            if not utterance.ok:
                lines.append(f"[调用失败] {_one_line(utterance.error or '未知错误')}")
                if body.strip():
                    lines.append("")
                    lines.append(body)
            else:
                lines.append(body if body.strip() else "（无内容）")
            lines.append("")
            lines.append(
                f"  {_one_line(utterance.model) or '-'} | {_fmt_elapsed(utterance.elapsed)}"
                f" | {int(utterance.tokens or 0)} tokens"
            )
            lines.append("")

    lines.append(_rule("=") + " 结论 " + _rule("="))
    lines.append("")
    synthesis = (result.synthesis or "").strip()
    lines.append(synthesis if synthesis else "（无综合结论）")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- JSON

def to_json(result: DiscussionResult) -> str:
    """渲染为 JSON 字符串（含全部字段）。"""

    payload = {
        "topic": result.topic,
        "method": result.method,
        "method_title": result.method_title,
        "language": result.language,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration": round(result.duration, 3),
        "synthesis": result.synthesis,
        "agents": [
            {
                "id": agent.id,
                "name": agent.name,
                "color": agent.color,
                "provider": agent.spec.provider,
                "model": agent.spec.model,
                "label": agent.spec.label,
                "base_url": agent.spec.base_url,
            }
            for agent in result.agents
        ],
        "utterances": [
            {
                "agent_id": utterance.agent_id,
                "agent_name": utterance.agent_name,
                "phase": utterance.phase,
                "role": utterance.role,
                "content": utterance.content,
                "model": utterance.model,
                "rounds": utterance.rounds,
                "tokens": utterance.tokens,
                "elapsed": round(float(utterance.elapsed or 0.0), 3),
                "error": utterance.error,
                "ok": utterance.ok,
            }
            for utterance in result.utterances
        ],
    }
    # api_key 属于敏感信息，绝不写入报告
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 落盘

_EXT_BY_FORMAT: dict[str, str] = {
    "md": ".md",
    "markdown": ".md",
    "text": ".txt",
    "txt": ".txt",
    "json": ".json",
}


def slugify(text: str, limit: int = 40) -> str:
    """把任意标题转成文件名安全的短 slug，保留中文。"""

    cleaned = re.sub(r"[^\w\s-]", "", str(text or ""), flags=re.UNICODE)
    cleaned = re.sub(r"[\s_]+", "-", cleaned.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-").lower()
    if limit and len(cleaned) > limit:
        cleaned = cleaned[:limit].strip("-")
    return cleaned or "report"


def _parse_timestamp(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d-%H%M%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def write_report(
    result: DiscussionResult,
    out_dir: str | Path,
    fmt: str = "md",
    filename: str | None = None,
) -> Path:
    """把报告写入磁盘，返回真实路径。fmt 支持 md / text / json。"""

    key = (fmt or "").strip().lower()
    if key not in _EXT_BY_FORMAT:
        raise ValueError(f"不支持的报告格式：{fmt!r}（可选：md / text / json）")
    ext = _EXT_BY_FORMAT[key]

    if key in ("text", "txt"):
        content = to_text(result)
    elif key == "json":
        content = to_json(result)
    else:
        content = to_markdown(result)

    directory = Path(out_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)

    if filename:
        name = Path(filename).name
        if not Path(name).suffix:
            name = f"{name}{ext}"
    else:
        stamp = _parse_timestamp(result.started_at) or datetime.now()
        name = f"{stamp.strftime('%Y%m%d-%H%M%S')}-{slugify(result.topic)}{ext}"

    path = directory / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    return path
