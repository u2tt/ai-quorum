"""Web 辩论执行器的生命周期测试：删除会话不得留下孤儿写入。

此前 delete_session 只 join 5 秒，而单次模型调用最长 120 秒；join 超时后仍会删表，
而 runner 线程还会继续 add_message，产生查不到的孤儿行。这里用假 store 直接验证
close() 之后不再有任何写入。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorum.config import Settings  # noqa: E402
from quorum.providers import ChatResult  # noqa: E402
from quorum.types import ModelSpec  # noqa: E402
from quorum.web import runner as runner_mod  # noqa: E402
from quorum.web.runner import DebateRunner, Seat, _effort_extra  # noqa: E402


class _FakeStore:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.finished: list[tuple] = []

    def add_message(self, sid: str, payload: dict) -> int:
        self.messages.append(payload)
        return len(self.messages)

    def finish_session(self, *args, **kwargs) -> None:
        self.finished.append(args)


def _seat(side: str, name: str) -> Seat:
    return Seat(
        side=side,
        name=name,
        spec=ModelSpec(provider="deepseek", model=f"m-{side}", label=name),
        prompt="你是辩手",
    )


def _runner(store: _FakeStore, rounds: int = 1) -> DebateRunner:
    return DebateRunner(
        store=store,
        session_id="sess-1",
        topic="测试辩题",
        rounds=rounds,
        seats=[_seat("pro", "正方一号"), _seat("con", "反方一号")],
        judge=None,
        settings=Settings(env={}, env_path=None),
    )


def test_close_before_reply_blocks_all_writes(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_chat(spec, messages, **kwargs):
        started.set()
        release.wait(5)
        return ChatResult(content="迟到的发言", model=spec.model)

    monkeypatch.setattr(runner_mod, "chat_stream", slow_chat)
    store = _FakeStore()
    runner = _runner(store)
    runner.start()
    assert started.wait(5)

    runner.close()          # 相当于删除会话
    release.set()
    runner._thread.join(timeout=5)

    assert store.messages == []
    assert store.finished == []


def test_normal_run_writes_messages_and_stamps_finish_time(monkeypatch):
    def quick_chat(spec, messages, **kwargs):
        return ChatResult(content="发言", model=spec.model)

    monkeypatch.setattr(runner_mod, "chat_stream", quick_chat)
    store = _FakeStore()
    runner = _runner(store)
    runner.start()
    runner._thread.join(timeout=5)

    assert len(store.messages) == 2
    assert store.finished
    assert runner.finished_at > 0


def test_stop_marks_session_stopped(monkeypatch):
    def quick_chat(spec, messages, **kwargs):
        return ChatResult(content="发言", model=spec.model)

    monkeypatch.setattr(runner_mod, "chat_stream", quick_chat)
    store = _FakeStore()
    runner = _runner(store, rounds=4)
    runner.stop()
    runner.start()
    runner._thread.join(timeout=5)
    assert ("sess-1", "stopped") in store.finished


def test_effort_extra_never_sends_null():
    assert _effort_extra("openai", "banana") is None
    assert _effort_extra("deepseek", "medium") is None
    assert _effort_extra("openai", "high") == {"reasoning_effort": "high"}
    assert _effort_extra("deepseek", "max") == {"reasoning_effort": "max"}
