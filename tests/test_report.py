"""report.py 的单元测试：Markdown / 纯文本 / JSON / 落盘。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 项目根目录加入 sys.path，让根目录下的 quorum 命名空间包可直接导入
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from quorum.report import slugify, to_json, to_markdown, to_text, write_report
from quorum.types import Agent, DiscussionResult, ModelSpec, Utterance


def make_agents() -> list[Agent]:
    return [
        Agent(
            id="A1",
            name="深求",
            spec=ModelSpec(
                provider="deepseek",
                model="deepseek-flash",
                label="deepseek-flash",
                base_url="https://api.deepseek.com/v1",
                api_key="sk-should-never-leak",
            ),
            color="cyan",
        ),
        Agent(
            id="A2",
            name="开智",
            spec=ModelSpec(
                provider="openai",
                model="gpt-4o",
                label="gpt-4o",
                base_url="https://api.openai.com/v1",
                api_key="sk-should-never-leak",
            ),
            color="magenta",
        ),
    ]


def make_result() -> DiscussionResult:
    utterances = [
        Utterance(
            agent_id="A1",
            agent_name="深求",
            phase="立论",
            role="支持者",
            content="第一点：应该先做小范围试点。\n# 这不是标题\n---\n第二点：控制成本。",
            model="deepseek-flash",
            rounds=1,
            tokens=856,
            elapsed=12.34,
        ),
        Utterance(
            agent_id="A2",
            agent_name="开智",
            phase="立论",
            role="质疑者",
            content="我不同意，| 表格符号 | 也要安全。",
            model="gpt-4o",
            rounds=1,
            tokens=421,
            elapsed=8.5,
        ),
        Utterance(
            agent_id="A1",
            agent_name="深求",
            phase="反驳",
            role="支持者",
            content="",
            model="deepseek-flash",
            rounds=2,
            tokens=0,
            elapsed=3.0,
            error="HTTP 429 rate limited",
        ),
    ]
    return DiscussionResult(
        topic="要不要用多模型投票来做技术决策",
        method="oxford",
        method_title="牛津式辩论",
        language="zh",
        agents=make_agents(),
        utterances=utterances,
        started_at="2026-10-26T09:30:00",
        finished_at="2026-10-26T09:31:10",
        synthesis="结论：先试点，再扩大。",
    )


# ---------------------------------------------------------------- slugify

def test_slugify_chinese_and_spaces() -> None:
    assert slugify("要不要用 多模型 投票") == "要不要用-多模型-投票"
    # 分隔符与标点被归一
    assert slugify("Hello,   World!!") == "hello-world"
    assert slugify("  --a__b--  ") == "a-b"


def test_slugify_truncate_and_fallback() -> None:
    assert len(slugify("一二三四五六七八九十", limit=4)) == 4
    assert slugify("") == "report"
    assert slugify("！！！") == "report"


# ---------------------------------------------------------------- Markdown

def test_markdown_front_matter_and_structure() -> None:
    md = to_markdown(make_result())

    assert md.startswith("---\n"), "必须以 YAML front matter 开头"
    head, _, body = md.partition("\n---\n")
    assert 'topic: "要不要用多模型投票来做技术决策"' in head
    assert 'method: "oxford"' in head
    assert 'method_title: "牛津式辩论"' in head
    assert 'started_at: "2026-10-26T09:30:00"' in head
    assert 'finished_at: "2026-10-26T09:31:10"' in head
    assert 'language: "zh"' in head
    assert 'models: "deepseek-flash, gpt-4o"' in head
    assert "rounds: 3" in head

    assert "# 要不要用多模型投票来做技术决策" in md
    assert "## 参与者" in md
    assert "| 位次 | Agent | 模型 | 发言数 | 累计耗时 |" in md
    assert "| 1 | 深求（A1） | deepseek-flash | 2 | 15.3s |" in md
    assert "| 2 | 开智（A2） | gpt-4o | 1 | 8.5s |" in md


def test_markdown_phase_grouping() -> None:
    md = to_markdown(make_result())
    # 相邻同 phase 合并：立论只出现一次
    assert md.count("## 阶段：立论") == 1
    assert md.count("## 阶段：反驳") == 1
    assert md.index("## 阶段：立论") < md.index("## 阶段：反驳")
    assert "### 深求（支持者）" in md
    assert "### 开智（质疑者）" in md
    assert md.rstrip().endswith("结论：先试点，再扩大。")


def test_markdown_failure_and_escaping() -> None:
    md = to_markdown(make_result())
    assert "> ⚠️ 调用失败：HTTP 429 rate limited" in md
    # 正文里的行首 # / --- 必须被转义，不能变成真实标题或分隔线
    assert "\\# 这不是标题" in md
    assert "\\---" in md
    # 正文里的竖线不属于表格上下文，原样保留
    assert "| 表格符号 | 也要安全。" in md


def test_markdown_table_cells_are_escaped() -> None:
    result = make_result()
    result.agents[0].name = "深|求"
    md = to_markdown(result)
    assert "| 1 | 深\\|求（A1） |" in md


def test_markdown_footers() -> None:
    md = to_markdown(make_result())
    assert "*deepseek-flash · 12.3s · 856 tokens*" in md
    assert "*gpt-4o · 8.5s · 421 tokens*" in md


def test_markdown_without_synthesis() -> None:
    result = make_result()
    result.synthesis = ""
    md = to_markdown(result)
    assert "## 结论" in md
    assert "（无综合结论）" in md


def test_markdown_meta_extension() -> None:
    md = to_markdown(make_result(), meta={"author": "team", "tag": None})
    assert 'author: "team"' in md
    assert "tag:" not in md


# ---------------------------------------------------------------- 纯文本

def test_text_has_no_front_matter() -> None:
    text = to_text(make_result())
    assert not text.startswith("---")
    assert "## " not in text
    assert "### " not in text
    assert "| 位次 |" not in text
    # 正文原样保留，不做 Markdown 转义
    assert "# 这不是标题" in text
    assert "要不要用多模型投票来做技术决策" in text
    assert "阶段：立论" in text
    assert "阶段：反驳" in text
    assert "[调用失败] HTTP 429 rate limited" in text
    assert "结论：先试点，再扩大。" in text


# ---------------------------------------------------------------- JSON

def test_json_roundtrip() -> None:
    payload = json.loads(to_json(make_result()))
    assert payload["topic"] == "要不要用多模型投票来做技术决策"
    assert payload["method"] == "oxford"
    assert payload["language"] == "zh"
    assert len(payload["agents"]) == 2
    assert payload["agents"][0]["model"] == "deepseek-flash"
    assert len(payload["utterances"]) == 3
    assert payload["utterances"][2]["error"] == "HTTP 429 rate limited"
    assert payload["utterances"][2]["ok"] is False
    assert payload["synthesis"].startswith("结论")
    # 敏感字段不得出现在报告里
    assert "sk-should-never-leak" not in to_json(make_result())


# ---------------------------------------------------------------- 落盘

@pytest.mark.parametrize(
    ("fmt", "suffix"),
    [("md", ".md"), ("text", ".txt"), ("json", ".json")],
)
def test_write_report_formats(tmp_path: Path, fmt: str, suffix: str) -> None:
    out = tmp_path / "nested" / "reports"
    path = write_report(make_result(), out, fmt=fmt)
    assert isinstance(path, Path)
    assert path.exists()
    assert path.parent == out
    assert path.suffix == suffix
    assert path.name.startswith("20261026-093000-")
    assert "要不要用多模型投票来做技术决策" in path.stem
    assert path.read_text(encoding="utf-8").strip() != ""

    if fmt == "json":
        json.loads(path.read_text(encoding="utf-8"))
    if fmt == "md":
        assert path.read_text(encoding="utf-8").startswith("---")


def test_write_report_bad_started_at_falls_back(tmp_path: Path) -> None:
    result = make_result()
    result.started_at = "不是时间"
    path = write_report(result, tmp_path, fmt="md")
    assert path.exists()
    assert len(path.stem.split("-")[0]) == 8


def test_write_report_custom_filename(tmp_path: Path) -> None:
    with_ext = write_report(make_result(), tmp_path, fmt="json", filename="custom.json")
    assert with_ext.name == "custom.json"
    assert with_ext.suffix == ".json"

    without_ext = write_report(make_result(), tmp_path, fmt="text", filename="plain")
    assert without_ext.name == "plain.txt"


def test_write_report_rejects_unknown_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_report(make_result(), tmp_path, fmt="pdf")
