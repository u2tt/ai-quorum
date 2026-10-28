"""web/store.py 的单元测试：建表、模型 UPSERT、会话/消息读写、级联删除。

全部为纯本地 SQLite 操作，不触网。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

# 项目根目录加入 sys.path，让根目录下的 quorum 命名空间包可直接导入
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorum.web.store import Store


# ---------------------------------------------------------------- 辅助

def make_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "nested" / "dir" / "web.db")


def query(path, sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def execute(path, sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def participants(n_pro: int = 1, n_con: int = 1) -> list[dict]:
    rows: list[dict] = []
    for i in range(n_pro):
        rows.append(
            {
                "side": "pro",
                "name": f"正方{i + 1}号",
                "provider": "deepseek",
                "model": f"pro-model-{i}",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": f"sk-pro-{i}",
                "prompt": f"正方提示词{i}",
                "effort": "low",
            }
        )
    for i in range(n_con):
        rows.append(
            {
                "side": "con",
                "name": f"反方{i + 1}号",
                "provider": "deepseek",
                "model": f"con-model-{i}",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": f"sk-con-{i}",
                "prompt": f"反方提示词{i}",
                "effort": "",
            }
        )
    return rows


JUDGE = {"provider": "deepseek", "model": "judge-model", "prompt": "", "effort": "high"}


# ---------------------------------------------------------------- 1. 建表

def test_init_creates_tables_and_parent_dir(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    assert store.path == tmp_path / "nested" / "dir" / "web.db"
    assert store.path.parent.is_dir()
    assert store.path.is_file()

    names = {
        r["name"]
        for r in query(store.path, "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")
    }
    assert {"custom_models", "sessions", "participants", "messages"} <= names
    # 索引也应建好
    assert {"idx_messages_session", "idx_participants_session", "idx_sessions_created"} <= names

    # 重复打开同一个库不应报错（IF NOT EXISTS）
    Store(store.path)
    # 默认路径不被污染：显式传参时 path 必须就是传入值
    assert Store(tmp_path / "x.db").path == tmp_path / "x.db"


# ---------------------------------------------------------------- 2. 自定义模型

def test_add_and_list_custom_model(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    assert store.list_custom_models() == []

    saved = store.add_custom_model(
        "openai", "gpt-test", "https://api.example.com/v1/", "sk-abc", "测试模型"
    )
    assert saved == {
        "key": "openai:gpt-test",
        "provider": "openai",
        "model": "gpt-test",
        "base_url": "https://api.example.com/v1",  # 末尾斜杠被去掉
        "label": "测试模型",
        "custom": True,
    }

    rows = store.list_custom_models()
    assert len(rows) == 1
    row = rows[0]
    assert row["key"] == "openai:gpt-test"
    assert row["base_url"] == "https://api.example.com/v1"
    assert row["api_key"] == "sk-abc"
    assert row["label"] == "测试模型"
    assert row["created_at"]

    got = store.get_custom_model("openai:gpt-test")
    assert got is not None and got["model"] == "gpt-test"
    assert store.get_custom_model("openai:not-exist") is None


def test_add_custom_model_upsert(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    store.add_custom_model("openai", "gpt-test", "https://old/v1", "sk-old", "旧名")
    store.add_custom_model("openai", "gpt-test", "https://new/v1", "sk-new", "新名")

    rows = store.list_custom_models()
    assert len(rows) == 1, "同一 provider:model 重复添加应 UPSERT，而不是产生两条"
    row = rows[0]
    assert row["key"] == "openai:gpt-test"
    assert row["base_url"] == "https://new/v1"
    assert row["api_key"] == "sk-new"
    assert row["label"] == "新名"
    # provider / model 不变
    assert row["provider"] == "openai" and row["model"] == "gpt-test"


def test_delete_custom_model(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.add_custom_model("openai", "gpt-test", "https://x/v1", "sk", "名")

    assert store.delete_custom_model("openai:gpt-test") is True
    assert store.list_custom_models() == []
    # 再删一次返回 False
    assert store.delete_custom_model("openai:gpt-test") is False


# ---------------------------------------------------------------- 3. 会话

def test_create_session_and_get_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    rows = participants(1, 1)

    sid = store.create_session("远程办公是否更好", 3, rows, JUDGE)

    assert isinstance(sid, str) and len(sid) == 12
    int(sid, 16)  # 必须是合法的 12 位 hex

    data = store.get_session(sid)
    assert data is not None
    assert data["id"] == sid
    assert data["topic"] == "远程办公是否更好"
    assert data["rounds"] == 3
    assert data["status"] == "running"
    assert data["finished_at"] == ""
    assert data["created_at"]

    # judge 能反序列化
    assert isinstance(data["judge"], dict)
    assert data["judge"] == JUDGE
    assert json.loads(json.dumps(data["judge"])) == JUDGE

    # participants 顺序 / 字段脱敏
    assert [p["side"] for p in data["participants"]] == ["pro", "con"]
    assert [p["name"] for p in data["participants"]] == ["正方1号", "反方1号"]
    for p in data["participants"]:
        assert "api_key" not in p, "api_key 不能回传给前端"
        assert p["prompt"]
        assert p["order_index"] == data["participants"].index(p)

    # judge 为空时也能读
    sid2 = store.create_session("无裁判", 1, rows, None)
    assert store.get_session(sid2)["judge"] == {}


def test_participants_ordered_by_order_index(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    sid = store.create_session("顺序", 1, participants(3, 0), None)

    # 直接改库，把 order_index 反过来，验证 SQL 真的按 order_index 排序
    for idx, name in enumerate(["正方1号", "正方2号", "正方3号"]):
        execute(store.path, "UPDATE participants SET order_index=? WHERE session_id=? AND name=?",
                (2 - idx, sid, name))

    got = [p["name"] for p in store.get_session(sid)["participants"]]
    assert got == ["正方3号", "正方2号", "正方1号"]


def test_add_message_autoincrement_and_order(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    sid = store.create_session("话题", 2, participants(1, 1), None)

    first = store.add_message(sid, {"round": 1, "seq": 2, "side": "con", "name": "反方1号", "content": "第二句"})
    second = store.add_message(sid, {"round": 1, "seq": 1, "side": "pro", "name": "正方1号", "content": "第一句"})

    assert isinstance(first, int) and isinstance(second, int)
    assert first > 0 and second > first
    # SQLite AUTOINCREMENT 自增
    assert second == first + 1

    msgs = store.get_session(sid)["messages"]
    assert [m["seq"] for m in msgs] == [1, 2], "messages 必须按 seq 升序"
    assert [m["content"] for m in msgs] == ["第一句", "第二句"]
    assert msgs[0]["tokens"] == 0 and msgs[0]["elapsed"] == 0.0 and msgs[0]["error"] == ""
    assert msgs[0]["created_at"]


def test_finish_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    sid = store.create_session("话题", 1, participants(1, 1), JUDGE)

    store.finish_session(sid, "done", "反方胜在证据", "con")

    data = store.get_session(sid)
    assert data["status"] == "done"
    assert data["summary"] == "反方胜在证据"
    assert data["winner"] == "con"
    assert data["finished_at"], "finished_at 必须被写入"

    # 中途停止也走同一个入口
    sid2 = store.create_session("话题2", 1, participants(1, 1), None)
    store.finish_session(sid2, "stopped")
    data2 = store.get_session(sid2)
    assert data2["status"] == "stopped"
    assert data2["summary"] == "" and data2["winner"] == ""
    assert data2["finished_at"]


def test_list_sessions(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    sid_old = store.create_session("旧话题", 1, [{"side": "pro", "name": "老正", "provider": "deepseek", "model": "m"}], None)
    sid_new = store.create_session("新话题", 2, participants(1, 1), JUDGE)
    store.add_message(sid_new, {"round": 1, "seq": 1, "side": "pro", "name": "正方1号", "content": "x"})
    store.add_message(sid_new, {"round": 1, "seq": 2, "side": "con", "name": "反方1号", "content": "y"})
    # created_at 只精确到秒，直接改库造出明确的先后关系
    execute(store.path, "UPDATE sessions SET created_at=? WHERE id=?", ("2024-01-01 00:00:00", sid_old))
    execute(store.path, "UPDATE sessions SET created_at=? WHERE id=?", ("2025-06-01 12:00:00", sid_new))

    items = store.list_sessions()

    assert [i["id"] for i in items] == [sid_new, sid_old], "按 created_at 倒序"
    top = items[0]
    assert top["topic"] == "新话题"
    assert top["message_count"] == 2
    assert top["pro_names"] == "正方1号"
    assert top["con_names"] == "反方1号"
    assert top["status"] == "running"
    assert "judge_json" not in top, "列表接口不应带 judge_json"

    assert items[1]["message_count"] == 0
    assert items[1]["con_names"] is None
    assert "judge_json" not in items[1]

    # limit 生效
    assert len(store.list_sessions(limit=1)) == 1


def test_delete_session_cascades(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    sid = store.create_session("要删的", 1, participants(2, 2), JUDGE)
    store.add_message(sid, {"round": 1, "seq": 1, "content": "a"})
    store.add_message(sid, {"round": 1, "seq": 2, "content": "b"})
    other = store.create_session("保留的", 1, participants(1, 1), None)
    store.add_message(other, {"round": 1, "seq": 1, "content": "c"})

    assert store.delete_session(sid) is True
    assert store.get_session(sid) is None

    assert query(store.path, "SELECT COUNT(*) AS n FROM participants WHERE session_id=?", (sid,))[0]["n"] == 0
    assert query(store.path, "SELECT COUNT(*) AS n FROM messages WHERE session_id=?", (sid,))[0]["n"] == 0
    assert query(store.path, "SELECT COUNT(*) AS n FROM sessions WHERE id=?", (sid,))[0]["n"] == 0

    # 不误伤别的会话
    assert query(store.path, "SELECT COUNT(*) AS n FROM participants WHERE session_id=?", (other,))[0]["n"] == 2
    assert query(store.path, "SELECT COUNT(*) AS n FROM messages WHERE session_id=?", (other,))[0]["n"] == 1

    # 再删返回 False
    assert store.delete_session(sid) is False


def test_get_session_missing_returns_none(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    assert store.get_session("deadbeef0000") is None
    assert store.list_sessions() == []
