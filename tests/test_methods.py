"""讨论方法的结构测试：步数、可见性序列、轮次语义。

methods.py 有 1000+ 行但此前 0 覆盖，方法一旦拼错步骤顺序，引擎与报告都会静默产出错误结果。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorum.methods import METHODS, get_method, method_table  # noqa: E402
from quorum.types import Agent, ModelSpec  # noqa: E402


def _agents(size: int) -> list[Agent]:
    return [
        Agent(
            id=f"A{i + 1}",
            name=f"席位{i + 1}",
            spec=ModelSpec(provider="deepseek", model=f"m{i + 1}", label=f"m{i + 1}"),
        )
        for i in range(size)
    ]


EXPECTED = {
    "standard": lambda n, r: n * (r + 1) + 1,
    "oxford": lambda n, r: 2 * n + 9,
    "advocate": lambda n, r: n * (r + 2) + 1,
    "socratic": lambda n, r: 4 * n + 1,
    "delphi": lambda n, r: 3 * n + 1,
    "brainstorm": lambda n, r: 2 * n + 1,
    "tradeoff": lambda n, r: 2 * n + 1,
}


def test_all_seven_methods_are_registered():
    assert set(METHODS) == {
        "standard",
        "oxford",
        "advocate",
        "socratic",
        "delphi",
        "brainstorm",
        "tradeoff",
    }
    assert [spec.name for spec in method_table()] == list(METHODS)


@pytest.mark.parametrize("name", sorted(EXPECTED))
@pytest.mark.parametrize("size,rounds", [(2, 1), (2, 3), (4, 2)])
def test_step_count_matches_contract(name, size, rounds):
    method = get_method(name)
    steps = method.build("测试议题", _agents(size), rounds=rounds, synthesizer_index=0)
    assert len(steps) == EXPECTED[name](size, rounds)


LEADING_INDEPENDENT = {
    # 值 "n" 表示与参与者数相同；数字表示固定条数（牛津只有双方一辩各一条，苏格拉底只有提问者一条）
    "standard": "n",
    "oxford": 2,
    "advocate": "n",
    "socratic": 1,
    "delphi": "n",
    "brainstorm": "n",
    "tradeoff": "n",
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_method_opens_with_independent_round(name):
    """首阶段必须是 visibility=none 的独立发言，否则并行与信息隔离都会失效。"""

    agents = _agents(3)
    steps = get_method(name).build("测试议题", agents, rounds=2, synthesizer_index=0)
    leading = len(agents) if LEADING_INDEPENDENT[name] == "n" else LEADING_INDEPENDENT[name]
    assert [s.visibility for s in steps[:leading]] == ["none"] * leading
    if len(steps) > leading:
        assert steps[leading].visibility == "all"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_method_ends_with_a_marked_final_step(name):
    agents = _agents(3)
    steps = get_method(name).build("测试议题", agents, rounds=2, synthesizer_index=0)
    assert steps[-1].visibility == "all"
    assert steps[-1].meta.get("kind"), "收尾步骤需要 kind，报告与排错都依赖它"


@pytest.mark.parametrize("name", ["oxford", "socratic", "delphi", "brainstorm", "tradeoff"])
def test_fixed_rounds_methods_ignore_rounds(name):
    agents = _agents(3)
    method = get_method(name)
    one = method.build("测试议题", agents, rounds=1, synthesizer_index=0)
    five = method.build("测试议题", agents, rounds=5, synthesizer_index=0)
    assert len(one) == len(five)


@pytest.mark.parametrize("name", ["standard", "advocate"])
def test_round_scaling_methods_respect_rounds(name):
    agents = _agents(3)
    method = get_method(name)
    one = method.build("测试议题", agents, rounds=1, synthesizer_index=0)
    three = method.build("测试议题", agents, rounds=3, synthesizer_index=0)
    assert len(three) > len(one)


def test_standard_rejects_single_agent():
    with pytest.raises(ValueError):
        get_method("standard").build("测试议题", _agents(1), rounds=2, synthesizer_index=0)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_single_agent_degrades_to_solo_steps(name):
    """除 standard 外，单模型时统一退化成三步，而不是崩溃。"""

    if name == "standard":
        pytest.skip("standard 显式拒绝单模型")
    steps = get_method(name).build("测试议题", _agents(1), rounds=2, synthesizer_index=0)
    assert len(steps) == 3
