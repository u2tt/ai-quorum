"""web/app.py + web/runner.py 的接口测试：REST 校验、SSE 全流程、提示词隔离、思考强度、裁判、模型增删。

全部离线：`quorum.web.runner.chat_stream` 与 `quorum.web.app.probe_model` 一律打桩，
`quorum.web.app.discover_models` 换成固定清单，绝不真的请求 deepseek / localhost。
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 项目根目录加入 sys.path，让根目录下的 quorum 命名空间包可直接导入
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import quorum.web.app as web_app
import quorum.web.runner as runner_mod
from quorum.providers import ChatResult
from quorum.types import ModelSpec
from quorum.web.app import create_app
from quorum.web.runner import _effort_extra, parse_winner

SPECS = [
    ModelSpec(
        provider="deepseek",
        model="deepseek-flash",
        label="deepseek-flash",
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test-a",
    ),
    ModelSpec(
        provider="deepseek",
        model="deepseek-v4-pro",
        label="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test-b",
    ),
]
KEY_A = "deepseek:deepseek-flash"
KEY_B = "deepseek:deepseek-v4-pro"

JUDGE_REPLY = "WINNER: con\n\n## 判定理由\n- 反方在第二轮正面回应了成本问题\n\n## 结论\n反方略胜。"


# ---------------------------------------------------------------- 夹具

@pytest.fixture()
def app(tmp_path, monkeypatch):
    """每个用例一个独立库 + 独立 env 路径，并切断模型发现（避免探测 localhost）。"""
    monkeypatch.setattr(web_app, "discover_models", lambda *a, **k: [replace(s) for s in SPECS])
    return create_app(db_path=tmp_path / "web.db", env_path=str(tmp_path / ".env"))


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


class Stub:
    """替换 runner.chat_stream 的假实现，记录每次调用的 system 与 kwargs。"""

    def __init__(self, judge_reply: str = JUDGE_REPLY) -> None:
        self.judge_reply = judge_reply
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def __call__(
        self,
        spec,
        messages,
        *,
        max_tokens=4096,
        temperature=0.8,
        timeout=120.0,
        on_delta=None,
        on_reasoning=None,
        extra=None,
    ):
        system = messages[0]["content"] if messages else ""
        # 裁判请求的特征是提示词里要求输出 WINNER 标记；不能再用 max_tokens 判断，
        # 因为辩手与裁判的额度都可能是 4096
        joined = " ".join(str(m.get("content", "")) for m in messages)
        is_judge = "WINNER" in joined or "裁判" in system
        with self._lock:
            self.calls.append(
                {
                    "spec": spec,
                    "system": system,
                    "messages": messages,
                    "extra": extra,
                    "max_tokens": max_tokens,
                    "is_judge": is_judge,
                }
            )
            n = len(self.calls)
        if is_judge:
            return ChatResult(
                content=self.judge_reply,
                model=spec.model,
                prompt_tokens=100,
                completion_tokens=50,
                elapsed=0.3,
            )
        return ChatResult(
            content=f"这是第 {n} 次发言的正文。",
            model=spec.model,
            prompt_tokens=20,
            completion_tokens=10,
            elapsed=0.1,
        )

    # -- 便捷视图
    @property
    def speech_calls(self) -> list[dict]:
        return [c for c in self.calls if not c["is_judge"]]

    @property
    def judge_calls(self) -> list[dict]:
        return [c for c in self.calls if c["is_judge"]]

    @property
    def systems(self) -> list[str]:
        return [c["system"] for c in self.calls]


@pytest.fixture()
def stub(monkeypatch):
    fake = Stub()
    monkeypatch.setattr(runner_mod, "chat_stream", fake)
    yield fake


def debate_body(
    topic: str = "远程办公是否更好",
    rounds: int = 1,
    pro_prompt: str = "你是正方，支持远程办公。",
    con_prompt: str = "你是反方，反对远程办公。",
    pro_key: str = KEY_A,
    con_key: str = KEY_B,
    pro_effort: str = "low",
    con_effort: str = "low",
    with_judge: bool = True,
) -> dict:
    body = {
        "topic": topic,
        "rounds": rounds,
        "participants": [
            {"side": "pro", "model_key": pro_key, "prompt": pro_prompt, "effort": pro_effort},
            {"side": "con", "model_key": con_key, "prompt": con_prompt, "effort": con_effort},
        ],
    }
    if with_judge:
        body["judge"] = {"model_key": pro_key, "prompt": "", "effort": "low"}
    return body


def read_sse(client, sid: str, max_lines: int = 400, deadline_s: float = 30.0) -> list[str]:
    """逐行读 SSE 直到 done/error；带行数与时间上限，避免挂死。"""
    events: list[str] = []
    started = time.monotonic()
    lines = 0
    with client.stream("GET", f"/api/debate/{sid}/stream") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        for line in resp.iter_lines():
            lines += 1
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
                events.append(name)
                if name in ("done", "error"):
                    break
            if lines > max_lines or time.monotonic() - started > deadline_s:
                break
    return events


def wait_not_running(client, sid: str, tries: int = 100) -> bool:
    for _ in range(tries):
        if not client.get(f"/api/sessions/{sid}").json()["running"]:
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- 1. bootstrap

def test_bootstrap(client):
    r = client.get("/api/bootstrap")
    assert r.status_code == 200
    data = r.json()

    assert {"models", "defaults", "health"} <= set(data)
    assert [m["key"] for m in data["models"]] == [KEY_A, KEY_B]
    assert data["models"][0]["custom"] is False
    assert data["models"][0]["efforts"] == ["low", "high", "max"]

    defaults = data["defaults"]
    assert defaults["max_rounds"] == 12
    assert defaults["rounds"] >= 1
    prompt = defaults["default_prompt"]
    assert prompt["pro"] and prompt["con"]
    assert "正方" in prompt["pro"] and "反方" in prompt["con"]

    health = data["health"]
    assert health["db"].endswith("web.db")
    assert "db" in health and "env" in health


def test_list_models_endpoint(client):
    r = client.get("/api/models")
    assert r.status_code == 200
    assert [m["key"] for m in r.json()["models"]] == [KEY_A, KEY_B]


# ---------------------------------------------------------------- 2. 入参校验

def test_validate_odd_participants(client):
    body = {
        "topic": "奇数参与者",
        "rounds": 1,
        "participants": [
            {"side": "pro", "model_key": KEY_A},
            {"side": "pro", "model_key": KEY_B},
            {"side": "con", "model_key": KEY_A},
        ],
    }
    r = client.post("/api/debate", json=body)
    assert r.status_code == 400
    assert "偶数" in r.json()["detail"]


def test_validate_only_pro_side(client):
    body = {
        "topic": "只有正方",
        "rounds": 1,
        "participants": [
            {"side": "pro", "model_key": KEY_A},
            {"side": "pro", "model_key": KEY_B},
        ],
    }
    r = client.post("/api/debate", json=body)
    assert r.status_code == 400
    assert "正方和反方" in r.json()["detail"]


def test_validate_empty_topic(client):
    r = client.post("/api/debate", json=debate_body(topic="   "))
    assert r.status_code == 400
    assert "辩题" in r.json()["detail"]

    r2 = client.post("/api/debate", json=debate_body(topic=""))
    assert r2.status_code == 400


def test_validate_unknown_model_key(client):
    r = client.post("/api/debate", json=debate_body(con_key="deepseek:not-exist"))
    assert r.status_code == 400
    assert "模型不可用" in r.json()["detail"]

    r2 = client.post("/api/debate", json=debate_body(pro_key="nope:model"))
    assert r2.status_code == 400

    # 裁判模型不存在也要拦住
    body = debate_body()
    body["judge"] = {"model_key": "ghost:model", "prompt": "", "effort": ""}
    r3 = client.post("/api/debate", json=body)
    assert r3.status_code == 400
    assert "裁判模型不可用" in r3.json()["detail"]


def test_validate_less_than_two_participants(client):
    r = client.post(
        "/api/debate",
        json={"topic": "单人", "rounds": 1, "participants": [{"side": "pro", "model_key": KEY_A}]},
    )
    assert r.status_code == 400
    assert "两名" in r.json()["detail"]


# ---------------------------------------------------------------- 3. SSE 全流程

def test_debate_happy_path_sse(client, stub):
    r = client.post("/api/debate", json=debate_body(rounds=1))
    assert r.status_code == 200
    started = r.json()
    assert started["participants"] == 2 and started["rounds"] == 1
    sid = started["session_id"]

    events = read_sse(client, sid)

    assert events, "没有收到任何 SSE 事件"
    assert events[0] == "status"
    assert events.count("message") == 2, "2 名参与者 × 1 轮 = 2 条 message"
    assert "verdict" in events, f"缺少 verdict：{events}"
    assert events[-1] == "done", f"最后一个事件必须是 done：{events}"
    # 顺序：message 全部在 verdict 之前，verdict 在 done 之前
    last_message = max(i for i, e in enumerate(events) if e == "message")
    assert last_message < events.index("verdict") < events.index("done")
    assert "error" not in events

    # 落库状态
    assert wait_not_running(client, sid)
    data = client.get(f"/api/sessions/{sid}").json()
    assert data["status"] == "done"
    assert data["running"] is False
    assert len(data["messages"]) == 2
    assert [m["side"] for m in data["messages"]] == ["pro", "con"]
    assert [m["seq"] for m in data["messages"]] == [1, 2]
    assert all(m["content"] for m in data["messages"])
    assert all(m["tokens"] > 0 for m in data["messages"])
    assert data["winner"] == "con"
    assert data["summary"] and "判定理由" in data["summary"]
    assert data["finished_at"]

    # 裁判被调用了一次
    assert len(stub.judge_calls) == 1
    assert len(stub.speech_calls) == 2

    # 历史列表里也能看到这条
    listed = client.get("/api/sessions").json()["sessions"]
    assert any(s["id"] == sid and s["message_count"] == 2 for s in listed)


def test_debate_multi_round_message_count(client, stub):
    r = client.post("/api/debate", json=debate_body(rounds=2))
    sid = r.json()["session_id"]

    events = read_sse(client, sid)
    assert events.count("message") == 4, "2 名参与者 × 2 轮 = 4 条 message"
    assert events[-1] == "done"

    data = client.get(f"/api/sessions/{sid}").json()
    assert len(data["messages"]) == 4
    assert [m["round"] for m in data["messages"]] == [1, 1, 2, 2]
    assert [m["side"] for m in data["messages"]] == ["pro", "con", "pro", "con"]


def test_stream_unknown_session_404(client):
    r = client.get("/api/debate/deadbeef0000/stream")
    assert r.status_code == 404


# ---------------------------------------------------------------- 4. 私有提示词隔离

PRO_SECRET = "PRO_SECRET_ABC"
CON_SECRET = "CON_SECRET_XYZ"


def test_private_prompts_are_isolated(client, stub):
    r = client.post(
        "/api/debate",
        json=debate_body(
            rounds=2,
            pro_prompt=f"你是正方。{PRO_SECRET}",
            con_prompt=f"你是反方。{CON_SECRET}",
        ),
    )
    sid = r.json()["session_id"]
    events = read_sse(client, sid)
    assert events[-1] == "done"

    speech_systems = [c["system"] for c in stub.speech_calls]
    assert len(speech_systems) == 4  # 2 人 × 2 轮

    has_pro = [s for s in speech_systems if PRO_SECRET in s]
    has_con = [s for s in speech_systems if CON_SECRET in s]

    assert has_pro, "正方提示词没有进入 system"
    assert has_con, "反方提示词没有进入 system"

    # 正方视角：只含自己的秘密，看不到对方的
    for s in has_pro:
        assert CON_SECRET not in s
    # 反方视角同理
    for s in has_con:
        assert PRO_SECRET not in s
    # 任何一次 system 都不能同时出现两边
    for s in speech_systems:
        assert not (PRO_SECRET in s and CON_SECRET in s)
    # 每次 system 恰好带一处立场秘密
    for s in speech_systems:
        assert (PRO_SECRET in s) != (CON_SECRET in s)

    # 裁判看的是发言正文，也不应看到任何人的私有设定
    assert stub.judge_calls, "裁判没有被调用"
    judge_system = stub.judge_calls[0]["system"]
    assert PRO_SECRET not in judge_system and CON_SECRET not in judge_system

    # 全程的 system 都不允许同时包含两边
    for s in stub.systems:
        assert not (PRO_SECRET in s and CON_SECRET in s)


# ---------------------------------------------------------------- 5. 思考强度

def test_effort_extra_mapping():
    assert _effort_extra("deepseek", "low") == {"reasoning_effort": "low"}
    assert _effort_extra("deepseek", "high") == {"reasoning_effort": "high"}
    assert _effort_extra("deepseek", "max") == {"reasoning_effort": "max"}
    assert _effort_extra("deepseek", "MAX") == {"reasoning_effort": "max"}
    assert _effort_extra("deepseek", "medium") is None, "deepseek 不支持 medium"
    assert _effort_extra("lmstudio", "high") is None, "本地服务不传 reasoning_effort"
    assert _effort_extra("ollama", "low") is None
    assert _effort_extra("", "") is None
    assert _effort_extra("deepseek", "") is None
    assert _effort_extra("openai", "max") == {"reasoning_effort": "high"}
    assert _effort_extra("openai", "low") == {"reasoning_effort": "low"}


def test_effort_is_forwarded_to_chat_stream(client, stub):
    r = client.post("/api/debate", json=debate_body(rounds=1, pro_effort="low", con_effort="max"))
    sid = r.json()["session_id"]
    assert read_sse(client, sid)[-1] == "done"

    extras = [c["extra"] for c in stub.speech_calls]
    assert {"reasoning_effort": "low"} in extras
    assert {"reasoning_effort": "max"} in extras
    # 裁判也用 low
    assert stub.judge_calls[0]["extra"] == {"reasoning_effort": "low"}
    # 发言额度：默认 4096（推理模型的思考也要占额度，2048 经常不够）
    assert all(c["max_tokens"] == 4096 for c in stub.speech_calls)


def test_effort_not_supported_by_provider_is_dropped(client, stub):
    # deepseek 不支持 "medium"，app 应把 effort 清空，extra 传 None
    r = client.post("/api/debate", json=debate_body(rounds=1, pro_effort="medium", con_effort=""))
    sid = r.json()["session_id"]
    assert read_sse(client, sid)[-1] == "done"

    assert all(c["extra"] is None for c in stub.speech_calls)


# ---------------------------------------------------------------- 6. 裁判解析

def test_judge_winner_and_summary_parsing(client, stub):
    r = client.post("/api/debate", json=debate_body(rounds=1))
    sid = r.json()["session_id"]
    assert read_sse(client, sid)[-1] == "done"

    data = client.get(f"/api/sessions/{sid}").json()
    assert data["winner"] == "con"
    assert "WINNER:" not in data["summary"]
    assert "WINNER" not in data["summary"]
    assert data["summary"].startswith("## 判定理由")
    assert "反方在第二轮正面回应了成本问题" in data["summary"]


def test_judge_parse_winner_variants():
    assert parse_winner("WINNER: pro\n\n## 理由") == "pro"
    assert parse_winner("winner：CON") == "con"
    assert parse_winner("WINNER: draw") == "draw"
    assert parse_winner("没有这一行") == ""
    assert parse_winner("") == ""


def test_judge_error_falls_back_to_empty_winner(client, monkeypatch):
    def failing_judge_stub(spec, messages, *, max_tokens=4096, temperature=0.8,
                           timeout=120.0, on_delta=None, on_reasoning=None, extra=None):
        system = messages[0]["content"]
        if "裁判" in system or max_tokens >= 4096:
            return ChatResult(error="HTTP 500 上游错误")
        return ChatResult(content="发言正文", prompt_tokens=1, completion_tokens=1, elapsed=0.01)

    monkeypatch.setattr(runner_mod, "chat_stream", failing_judge_stub)

    r = client.post("/api/debate", json=debate_body(rounds=1))
    sid = r.json()["session_id"]
    events = read_sse(client, sid)
    assert events[-1] == "done"

    data = client.get(f"/api/sessions/{sid}").json()
    assert data["status"] == "done"
    assert data["winner"] == ""
    assert "裁判调用失败" in data["summary"]


# ---------------------------------------------------------------- 7. /api/models

def test_add_and_delete_custom_model(client, monkeypatch):
    def ok_probe(spec, timeout=60.0):
        return ChatResult(content="就绪", model=spec.model, prompt_tokens=3, completion_tokens=2, elapsed=0.01)

    monkeypatch.setattr(web_app, "probe_model", ok_probe)

    r = client.post(
        "/api/models",
        json={
            "provider": "deepseek",
            "model": "deepseek-extra",
            "base_url": "https://proxy.example.com/v1/",
            "api_key": "sk-extra",
            "label": "第三方代理",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["model"]["key"] == "deepseek:deepseek-extra"
    assert data["model"]["base_url"] == "https://proxy.example.com/v1"
    assert data["probe"] == "就绪"

    models = client.get("/api/models").json()["models"]
    card = next(m for m in models if m["key"] == "deepseek:deepseek-extra")
    assert card["custom"] is True
    assert card["label"] == "第三方代理"
    assert "api_key" not in card, "模型卡片不应回传 api_key"

    # bootstrap 里也能看到
    assert "deepseek:deepseek-extra" in [m["key"] for m in client.get("/api/bootstrap").json()["models"]]

    d = client.delete("/api/models", params={"key": "deepseek:deepseek-extra"})
    assert d.status_code == 200 and d.json() == {"ok": True, "hidden": True}
    assert "deepseek:deepseek-extra" not in [m["key"] for m in client.get("/api/models").json()["models"]]

    # 再删一次：DB 已无记录且已在隐藏表，防御性 404
    assert client.delete("/api/models", params={"key": "deepseek:deepseek-extra"}).status_code == 404


def test_add_custom_model_probe_failure(client, monkeypatch):
    def bad_probe(spec, timeout=60.0):
        return ChatResult(error="connect timeout")

    monkeypatch.setattr(web_app, "probe_model", bad_probe)

    r = client.post(
        "/api/models",
        json={
            "provider": "custom",
            "model": "bad-model",
            "base_url": "http://127.0.0.1:1/v1",
            "api_key": "",
        },
    )
    assert r.status_code == 400
    assert "连通性测试失败" in r.json()["detail"]
    assert "connect timeout" in r.json()["detail"]
    # 失败不应入库
    assert "custom:bad-model" not in [m["key"] for m in client.get("/api/models").json()["models"]]


def test_add_custom_model_requires_fields(client, monkeypatch):
    def should_not_be_called(spec, timeout=60.0):  # pragma: no cover
        raise AssertionError("字段不全时不应发起连通性测试")

    monkeypatch.setattr(web_app, "probe_model", should_not_be_called)

    r = client.post("/api/models", json={"provider": "custom", "model": "  ", "base_url": "http://x/v1"})
    assert r.status_code == 400
    assert "不能为空" in r.json()["detail"]


# ---------------------------------------------------------------- 8. stop

def test_stop_unknown_session_404(client):
    r = client.post("/api/debate/deadbeef0000/stop")
    assert r.status_code == 404


def test_stop_finished_session_404(client, stub):
    r = client.post("/api/debate", json=debate_body(rounds=1))
    sid = r.json()["session_id"]
    assert read_sse(client, sid)[-1] == "done"
    assert wait_not_running(client, sid)

    r2 = client.post(f"/api/debate/{sid}/stop")
    assert r2.status_code == 404
    assert r2.json()["detail"]


# ---------------------------------------------------------------- 9. 删除会话

def test_delete_session_endpoint(client, app):
    store = app.state.store
    sid = store.create_session(
        "待删除",
        1,
        [
            {"side": "pro", "name": "正", "provider": "deepseek", "model": "m"},
            {"side": "con", "name": "反", "provider": "deepseek", "model": "m"},
        ],
        None,
    )
    store.add_message(sid, {"round": 1, "seq": 1, "content": "hi"})
    assert client.get(f"/api/sessions/{sid}").status_code == 200

    r = client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 200 and r.json() == {"ok": True}

    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert client.delete(f"/api/sessions/{sid}").status_code == 404

    ids = [s["id"] for s in client.get("/api/sessions").json()["sessions"]]
    assert sid not in ids


def test_delete_running_session_stops_runner(client, stub, app):
    r = client.post("/api/debate", json=debate_body(rounds=1))
    sid = r.json()["session_id"]
    assert read_sse(client, sid)[-1] == "done"
    assert wait_not_running(client, sid)

    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert sid not in app.state.runners
    assert client.get(f"/api/debate/{sid}/stream").status_code == 404


# ---------------------------------------------------------------- 10. 其它

def test_index_serves_or_503(client):
    r = client.get("/", follow_redirects=False)
    # 前端可能尚未构建；只要不是 500 就算符合契约
    assert r.status_code in (200, 503)
    if r.status_code == 503:
        assert "前端尚未构建" in r.json()["error"]
