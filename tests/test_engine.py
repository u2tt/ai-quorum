"""Engine 的回归测试：结论选择、历史截断、匿名、数值汇总、并行判定。

这些路径此前完全没有测试（engine.py 覆盖率 0%），而它们决定了报告里的结论是否正确。
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorum import engine as engine_mod  # noqa: E402
from quorum.config import Settings  # noqa: E402
from quorum.engine import Engine  # noqa: E402
from quorum.methods import get_method  # noqa: E402
from quorum.providers import ChatResult  # noqa: E402
from quorum.types import Agent, ModelSpec, Step, Utterance  # noqa: E402


_NAMES = {1: "甲", 2: "乙", 3: "丙"}


def _agent(index: int, provider: str = "deepseek") -> Agent:
    return Agent(
        id=f"A{index}",
        name=_NAMES.get(index, f"席位{index}"),
        spec=ModelSpec(provider=provider, model=f"m{index}", label=f"m{index}"),
    )


def _settings(**overrides) -> Settings:
    base = Settings(env={}, env_path=None)
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _stub_chat(final_reply: str = "最终结论", fail_final: bool = False):
    """按 system prompt 里的席位角色决定返回什么。"""

    def fake(spec, messages, **kwargs):
        system = messages[0]["content"]
        if "总结者" in system or "汇总者" in system or "提问者" in system:
            if fail_final:
                return ChatResult(content="", model=spec.model, error="模拟失败")
            return ChatResult(content=final_reply, model=spec.model, elapsed=0.01)
        return ChatResult(content="普通发言", model=spec.model, elapsed=0.01)

    return fake


# ---------------------------------------------------------------- 结论选择


def test_synthesis_uses_final_step_not_last_speaker(monkeypatch):
    """收尾步骤由 2 号执行时，不能把 1 号的最后一条发言当结论。"""

    monkeypatch.setattr(engine_mod, "chat_stream", _stub_chat("最终结论"))
    agents = [_agent(1), _agent(2)]
    eng = Engine(
        settings=_settings(),
        topic="测试",
        method=get_method("standard"),
        agents=agents,
        rounds=1,
        synthesizer_index=1,
    )
    result = eng.run()
    assert result.synthesis == "最终结论"


def test_synthesis_is_empty_when_final_step_fails(monkeypatch):
    """收尾失败时必须留空，而不是拿中途发言顶替。"""

    notes: list[str] = []
    monkeypatch.setattr(engine_mod, "chat_stream", _stub_chat(fail_final=True))
    eng = Engine(
        settings=_settings(),
        topic="测试",
        method=get_method("standard"),
        agents=[_agent(1), _agent(2)],
        rounds=1,
        synthesizer_index=0,
    )
    eng.sink.on_note = notes.append
    result = eng.run()
    assert result.synthesis == ""
    assert notes and "收尾步骤失败" in notes[0]


def test_synthesis_ignores_out_of_range_synthesizer_index(monkeypatch):
    """synthesizer_index 越界不再抛 IndexError。"""

    monkeypatch.setattr(engine_mod, "chat_stream", _stub_chat("最终结论"))
    eng = Engine(
        settings=_settings(),
        topic="测试",
        method=get_method("socratic"),
        agents=[_agent(1), _agent(2)],
        rounds=2,
        synthesizer_index=99,
    )
    result = eng.run()
    assert result.synthesis == "最终结论"


# ---------------------------------------------------------------- 历史截断


def test_history_limit_is_respected_for_oversized_single_turn():
    """单条发言自身超限时截断它，而不是无视上限整条塞进去。"""

    eng = Engine(
        settings=_settings(),
        topic="测试",
        method=get_method("standard"),
        agents=[_agent(1), _agent(2)],
        max_history_chars=300,
    )
    history = [
        Utterance(agent_id="A1", agent_name="甲", phase="p", role="r", content="长" * 2000)
    ]
    step = Step(0, "讨论", "讨论者", "继续", visibility="all")
    text = eng._visible_history(eng.agents[0], step, history)
    assert len(text) <= 320
    assert "已截断" in text


def test_history_limit_falls_back_to_settings():
    eng = Engine(
        settings=_settings(max_history_chars=120),
        topic="测试",
        method=get_method("standard"),
        agents=[_agent(1), _agent(2)],
    )
    history = [
        Utterance(agent_id="A1", agent_name="甲", phase="p", role="r", content="字" * 500)
    ]
    step = Step(0, "讨论", "讨论者", "继续", visibility="all")
    text = eng._visible_history(eng.agents[0], step, history)
    assert len(text) <= 140


# ---------------------------------------------------------------- 匿名


def test_anonymous_step_hides_speaker_names():
    eng = Engine(
        settings=_settings(),
        topic="测试",
        method=get_method("delphi"),
        agents=[_agent(1), _agent(2), _agent(3)],
    )
    history = [
        Utterance(agent_id="A2", agent_name="乙", phase="p", role="评估者", content="估计 30")
    ]
    step = Step(0, "匿名意见评估", "评估者", "评估", visibility="all", meta={"anonymous": True})
    text = eng._visible_history(eng.agents[0], step, history)
    assert "乙" not in text               # 真实姓名必须消失
    assert "参与者2" in text              # 换成稳定的匿名编号


def test_delphi_marks_review_and_aggregate_as_anonymous():
    method = get_method("delphi")
    steps = method.build("测试", [_agent(1), _agent(2), _agent(3)], rounds=2, synthesizer_index=0)
    review = [s for s in steps if s.meta.get("kind") == "review"]
    reestimate = [s for s in steps if s.meta.get("kind") == "reestimate"]
    aggregate = [s for s in steps if s.meta.get("kind") == "aggregate"]
    assert review and all(s.meta.get("anonymous") for s in review)
    assert reestimate and all(s.meta.get("anonymous") for s in reestimate)
    assert aggregate and aggregate[0].meta.get("aggregate") == "median"
    assert aggregate[0].meta.get("anonymous") is True


# ---------------------------------------------------------------- 数值汇总


def test_numeric_summary_computes_median():
    history = [
        Utterance(agent_id="A1", agent_name="甲", phase="p", role="r", content="估计：10 万元"),
        Utterance(agent_id="A2", agent_name="乙", phase="p", role="r", content="估计：30 万元"),
        Utterance(agent_id="A3", agent_name="丙", phase="p", role="r", content="估计：20 万元"),
    ]
    text = engine_mod._numeric_summary(history)
    assert "中位数：20" in text
    assert "样本数：3" in text
    assert "最小值：10" in text
    assert "最大值：30" in text


def test_numeric_summary_ignores_failed_turns_and_needs_two_samples():
    history = [
        Utterance(agent_id="A1", agent_name="甲", phase="p", role="r", content="估计：10"),
        Utterance(agent_id="A2", agent_name="乙", phase="p", role="r", content="", error="失败"),
    ]
    assert engine_mod._numeric_summary(history) == ""


def test_aggregate_step_injects_computed_stats():
    eng = Engine(
        settings=_settings(),
        topic="测试",
        method=get_method("delphi"),
        agents=[_agent(1), _agent(2), _agent(3)],
    )
    history = [
        Utterance(agent_id="A1", agent_name="甲", phase="p", role="r", content="估计：10"),
        Utterance(agent_id="A2", agent_name="乙", phase="p", role="r", content="估计：40"),
    ]
    step = Step(0, "汇总", "汇总者", "汇总", visibility="all", meta={"aggregate": "median"})
    messages = eng._build_messages(eng.agents[0], step, history)
    assert "代码计算的统计" in messages[1]["content"]
    assert "中位数：25" in messages[1]["content"]


# ---------------------------------------------------------------- 并行判定


def test_parallel_batch_is_scoped_to_batch_members():
    """编队里有本地模型，但本批次全是云端时仍应并行。"""

    agents = [_agent(1), _agent(2), _agent(3, provider="lmstudio")]
    eng = Engine(
        settings=_settings(execution_mode="auto"),
        topic="测试",
        method=get_method("standard"),
        agents=agents,
    )
    cloud_only = [
        Step(0, "开场", "陈述者", "说", visibility="none", group=0),
        Step(1, "开场", "陈述者", "说", visibility="none", group=0),
    ]
    assert len(eng._parallel_batch(cloud_only, 0)) == 2

    with_local = cloud_only + [Step(2, "开场", "陈述者", "说", visibility="none", group=0)]
    assert eng._parallel_batch(with_local, 0) == []


def test_sequential_mode_disables_parallel():
    eng = Engine(
        settings=_settings(execution_mode="sequential"),
        topic="测试",
        method=get_method("standard"),
        agents=[_agent(1), _agent(2)],
    )
    steps = [
        Step(0, "开场", "陈述者", "说", visibility="none", group=0),
        Step(1, "开场", "陈述者", "说", visibility="none", group=0),
    ]
    assert eng._parallel_batch(steps, 0) == []
