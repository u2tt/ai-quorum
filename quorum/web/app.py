"""FastAPI 应用：静态页面 + REST + SSE。

启动方式见 quorum/webapp.py（`debate --port 8765`）。
"""

from __future__ import annotations

import asyncio
import json
import queue
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config import (
    PROVIDER_DEFS,
    default_slots,
    discover_models,
    _probe_models,
    load_settings,
    provider_catalog,
    resolve_key,
)
from ..providers import probe_model
from ..types import ModelSpec
from .runner import Seat, Judge, DebateRunner
from .store import Store

STATIC_DIR = Path(__file__).parent / "static"

def _efforts_of(provider: str) -> list[str]:
    return list(PROVIDER_DEFS.get(provider, {}).get("efforts") or [])


def _default_effort(provider: str) -> str:
    return PROVIDER_DEFS.get(provider, {}).get("default_effort", "")

DEFAULT_PROMPT = {
    "pro": "你是正方辩手，立场是支持辩题。你论证扎实，先给结论再给依据，"
           "用具体事实、数据和机制推理支撑；主动攻击对方论证里的漏洞，而不是重复自己的立场。",
    "con": "你是反方辩手，立场是反对辩题。你论证扎实，先给结论再给依据，"
           "用具体事实、数据和机制推理支撑；主动攻击对方论证里的漏洞，而不是重复自己的立场。",
}


# ---------------------------------------------------------------- 请求体

class ParticipantIn(BaseModel):
    side: str
    model_key: str
    prompt: str = ""
    effort: str = ""
    name: str = ""


class JudgeIn(BaseModel):
    model_key: str
    prompt: str = ""
    effort: str = ""


class DebateIn(BaseModel):
    topic: str
    rounds: int = Field(default=3, ge=1, le=12)
    participants: list[ParticipantIn]
    judge: JudgeIn | None = None


class CustomModelIn(BaseModel):
    provider: str = ""
    model: str
    base_url: str = ""
    api_key: str = ""
    label: str = ""
    provider_key: str = ""      # 传预设厂商 key 时可省略 base_url / api_key


class UnhideIn(BaseModel):
    key: str


# ---------------------------------------------------------------- 应用工厂

def create_app(db_path: str | Path | None = None, env_path: str | None = None) -> FastAPI:
    app = FastAPI(title="AI Debate", docs_url=None, redoc_url=None)
    store = Store(db_path)
    store.mark_orphans()  # 上次进程退出时残留的 running 会话无法继续
    settings = load_settings(env_path)
    runners: dict[str, DebateRunner] = {}
    # 已结束的 runner 保留一段时间的回放缓冲，之后回收，避免进程内存只增不减
    finished_ttl = 600.0

    def prune_runners() -> None:
        now = time.time()
        for sid, item in list(runners.items()):
            if item.finished_at and now - item.finished_at > finished_ttl and not item.running:
                runners.pop(sid, None)

    # 模型目录缓存：本地探测有 IO 成本，30 秒内复用
    cache: dict[str, Any] = {"ts": 0.0, "data": []}

    app.state.store = store
    app.state.settings = settings
    app.state.runners = runners

    # ------------------------------------------------------------ 模型目录

    def catalog(refresh: bool = False) -> list[dict[str, Any]]:
        now = time.time()
        if not refresh and cache["data"] and now - cache["ts"] < 30:
            return cache["data"]
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        hidden = store.hidden_keys()

        for spec in discover_models(settings):
            if spec.key in hidden:
                continue
            items.append(_model_card(spec, custom=False))
            seen.add(spec.key)

        for row in store.list_custom_models():
            key = row["key"]
            if key in hidden:
                continue
            spec = ModelSpec(
                provider=row["provider"],
                model=row["model"],
                label=row["label"] or row["model"],
                base_url=row["base_url"],
                api_key=row["api_key"],
            )
            if key in seen:
                continue
            items.append(_model_card(spec, custom=True))
            seen.add(key)
        cache["ts"] = now
        cache["data"] = items
        return items

    def model_card(key: str) -> dict[str, Any] | None:
        for item in catalog():
            if item["key"] == key:
                return item
        return None

    def spec_of(key: str) -> ModelSpec | None:
        custom = store.get_custom_model(key)
        if custom:
            return ModelSpec(
                provider=custom["provider"],
                model=custom["model"],
                label=custom["label"] or custom["model"],
                base_url=custom["base_url"],
                api_key=custom["api_key"],
            )
        for spec in discover_models(settings):
            if spec.key == key:
                return spec
        return None

    # ------------------------------------------------------------ 页面

    @app.get("/")
    def index():
        page = STATIC_DIR / "index.html"
        if not page.is_file():
            return JSONResponse(
                {"error": "前端尚未构建", "hint": f"缺少 {page}"}, status_code=503
            )
        # 单文件前端没有指纹文件名：不禁止缓存的话，改了样式/脚本用户刷新也看不到
        return FileResponse(page, headers={"Cache-Control": "no-store, must-revalidate"})

    if STATIC_DIR.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # ------------------------------------------------------------ 模型 API

    @app.get("/api/bootstrap")
    def bootstrap(refresh: bool = False):
        specs = catalog(refresh=refresh)
        slot_specs = [
            ModelSpec(
                provider=m["provider"],
                model=m["model"],
                label=m["label"],
                base_url=m["base_url"],
                api_key="",
            )
            for m in specs
        ]
        return {
            "models": specs,
            "providers": provider_catalog(settings),
            "default_slots": default_slots(settings, slot_specs),
            "defaults": {
                "rounds": settings.rounds_per_agent + 1,
                "max_rounds": 12,
                "default_prompt": DEFAULT_PROMPT,
            },
            "health": {"db": str(store.path), "env": str(settings.env_path or "")},
        }

    @app.get("/api/providers")
    def list_providers():
        return {"providers": provider_catalog(settings)}

    @app.get("/api/models")
    def list_models():
        return {"models": catalog()}

    @app.get("/api/fetch-models")
    def fetch_models(base_url: str = "", api_key: str = "", provider: str = ""):
        """从指定 OpenAI 兼容服务的 /v1/models 拉取模型清单，供「获取」按钮使用。

        api_key 留空时：先按 provider 预设回退，再按 base_url 反查同端点的厂商。
        """
        url = base_url.strip()
        meta = PROVIDER_DEFS.get(provider.strip()) or {}
        if not url:
            url = (meta.get("base_url") or "").strip()
        if not url:
            raise HTTPException(400, "先选择厂商或填写 Base URL")

        key = api_key.strip()
        if not key:
            key = resolve_key(settings, meta)
        if not key:
            for _pk, m in PROVIDER_DEFS.items():
                if (m.get("base_url") or "").rstrip("/") == url.rstrip("/"):
                    key = resolve_key(settings, m)
                    break

        models = _probe_models(url, api_key=key, timeout=8.0)
        if not models:
            raise HTTPException(400, "没有取到模型：检查 Base URL / API Key / 网络")
        return {"models": models, "base_url": url.rstrip("/")}

    @app.post("/api/models")
    def add_model(body: CustomModelIn):
        preset_key = (body.provider_key or body.provider).strip()
        provider = body.provider.strip() or preset_key
        model = body.model.strip()
        if not provider or not model:
            raise HTTPException(400, "provider / model 不能为空")

        meta = PROVIDER_DEFS.get(preset_key) or PROVIDER_DEFS.get(provider) or {}
        base_url = body.base_url.strip() or (meta.get("base_url") or "")
        if not base_url:
            raise HTTPException(400, "base_url 不能为空（非预设厂商请手动填写）")
        api_key = body.api_key.strip() or resolve_key(settings, meta)

        spec = ModelSpec(
            provider=preset_key if meta else provider,
            model=model,
            label=body.label.strip() or model,
            base_url=base_url.rstrip("/"),
            api_key=api_key,
        )
        probed = probe_model(spec, timeout=45.0)
        if not probed.ok:
            raise HTTPException(400, f"连通性测试失败：{probed.error or '模型无响应'}")

        store.unhide_model(spec.key)  # 重新添加即从隐藏列表恢复
        saved = store.add_custom_model(
            spec.provider, model, base_url, api_key, spec.label
        )
        cache["data"] = []
        saved.update(_model_card(spec, custom=True))
        return {"ok": True, "model": saved, "probe": probed.content[:60]}

    @app.get("/api/models/hidden")
    def list_hidden_models():
        return {"models": store.list_hidden_models()}

    @app.post("/api/models/unhide")
    def unhide_model(body: UnhideIn):
        key = body.key.strip()
        if not key:
            raise HTTPException(400, "key 不能为空")
        if not store.unhide_model(key):
            raise HTTPException(404, "该模型不在隐藏列表")
        cache["data"] = []
        return {"ok": True, "unhidden": True}

    @app.delete("/api/models")
    def delete_model(key: str = Query(...)):
        removed_custom = store.delete_custom_model(key)
        hidden = store.hide_model(key)
        cache["data"] = []
        if not removed_custom and not hidden:
            raise HTTPException(404, "自定义模型不存在")
        return {"ok": True, "hidden": True}

    # ------------------------------------------------------------ 会话 API

    @app.get("/api/sessions")
    def list_sessions(limit: int = 40):
        prune_runners()
        return {"sessions": store.list_sessions(limit=limit)}

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str):
        data = store.get_session(sid)
        if data is None:
            raise HTTPException(404, "会话不存在")
        data["running"] = sid in runners and runners[sid].running
        return data

    @app.delete("/api/sessions/{sid}")
    def delete_session(sid: str):
        runner = runners.pop(sid, None)
        if runner is not None:
            # 先置 closed：即使线程还在等模型返回，后续也不会再写库
            runner.close()
            if runner._thread is not None:
                runner._thread.join(timeout=5)
        if not store.delete_session(sid):
            raise HTTPException(404, "会话不存在")
        return {"ok": True}

    # ------------------------------------------------------------ 开辩

    @app.post("/api/debate")
    def start_debate(body: DebateIn):
        prune_runners()
        topic = body.topic.strip()
        if not topic:
            raise HTTPException(400, "辩题不能为空")

        if len(body.participants) < 2:
            raise HTTPException(400, "至少需要两名参与者")
        if len(body.participants) % 2 != 0:
            raise HTTPException(400, "参与者总数必须为偶数（正反各若干名）")

        pro = [p for p in body.participants if p.side == "pro"]
        con = [p for p in body.participants if p.side == "con"]
        if not pro or not con:
            raise HTTPException(400, "正方和反方都至少需要一名成员")
        if any(p.side not in ("pro", "con") for p in body.participants):
            raise HTTPException(400, "side 只能是 pro 或 con")

        seats: list[Seat] = []
        rows: list[dict[str, Any]] = []
        for idx, p in enumerate(body.participants):
            spec = spec_of(p.model_key)
            if spec is None:
                raise HTTPException(400, f"模型不可用：{p.model_key}")
            side_count = sum(1 for q in body.participants[: idx + 1] if q.side == p.side)
            name = p.name.strip() or f"{'正' if p.side == 'pro' else '反'}方{side_count}号 · {spec.label}"
            effort = p.effort if p.effort in _efforts_of(spec.provider) else ""
            prompt = p.prompt.strip() or DEFAULT_PROMPT[p.side]
            rows.append(
                {
                    "side": p.side,
                    "name": name,
                    "provider": spec.provider,
                    "model": spec.model,
                    "base_url": spec.base_url,
                    "api_key": spec.api_key,
                    "prompt": prompt,
                    "effort": effort,
                }
            )
            seats.append(
                Seat(side=p.side, name=name, spec=spec, prompt=prompt, effort=effort)
            )

        judge_obj: Judge | None = None
        judge_row: dict[str, Any] | None = None
        if body.judge is not None:
            j_spec = spec_of(body.judge.model_key)
            if j_spec is None:
                raise HTTPException(400, f"裁判模型不可用：{body.judge.model_key}")
            j_effort = (
                body.judge.effort
                if body.judge.effort in _efforts_of(j_spec.provider)
                else ""
            )
            judge_obj = Judge(spec=j_spec, prompt=body.judge.prompt.strip(), effort=j_effort)
            judge_row = {
                "provider": j_spec.provider,
                "model": j_spec.model,
                "base_url": j_spec.base_url,
                "label": j_spec.label,
                "effort": j_effort,
                "prompt": judge_obj.prompt,
            }

        sid = store.create_session(topic, body.rounds, rows, judge_row)

        # 回填 participant_id，便于前端把消息和席位对应
        data = store.get_session(sid) or {}
        id_map = {
            (p["side"], p["name"]): p["id"] for p in data.get("participants", [])
        }
        for seat in seats:
            seat.participant_id = id_map.get((seat.side, seat.name), 0)

        runner = DebateRunner(
            store=store,
            session_id=sid,
            topic=topic,
            rounds=body.rounds,
            seats=seats,
            judge=judge_obj,
            settings=settings,
            max_tokens=max(4096, settings.max_tokens),
        )
        runners[sid] = runner
        runner.start()
        return {"session_id": sid, "participants": len(seats), "rounds": body.rounds}

    @app.post("/api/debate/{sid}/stop")
    def stop_debate(sid: str):
        runner = runners.get(sid)
        if runner is None or not runner.running:
            raise HTTPException(404, "该场辩论不在进行中")
        runner.stop()
        return {"ok": True}

    # ------------------------------------------------------------ SSE

    @app.get("/api/debate/{sid}/stream")
    async def stream(sid: str):
        runner = runners.get(sid)
        if runner is None:
            raise HTTPException(404, "该场辩论不在进行中（可刷新历史记录查看结果）")

        async def gen():
            # 订阅在生成器内完成：客户端在响应开始前断开时，不会留下无法注销的队列
            q = runner.subscribe()
            try:
                while True:
                    try:
                        item = await asyncio.to_thread(q.get, True, 1.0)
                    except queue.Empty:
                        yield ": ping\n\n"
                        if not runner.running:
                            break
                        continue
                    event = item["event"]
                    data = json.dumps(item["data"], ensure_ascii=False)
                    yield f"event: {event}\ndata: {data}\n\n"
                    if event in ("done", "error"):
                        break
            finally:
                runner.unsubscribe(q)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.middleware("http")
    async def _no_store_api(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app


def _model_card(spec: ModelSpec, custom: bool) -> dict[str, Any]:
    meta = PROVIDER_DEFS.get(spec.provider, {})
    efforts = list(meta.get("efforts") or [])
    return {
        "key": spec.key,
        "provider": spec.provider,
        "model": spec.model,
        "label": spec.label,
        "base_url": spec.base_url,
        "local": spec.provider in ("lmstudio", "ollama", "llamaswap"),
        "custom": custom,
        "efforts": efforts,
        "default_effort": meta.get("default_effort", ""),
        "provider_name": meta.get("name", spec.provider),
    }
