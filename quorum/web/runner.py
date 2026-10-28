"""辩论执行器（Web 版）。

与终端版 debate 的区别：
- 正反双方各可以有多名成员（总数偶数），按「每轮正方依次 → 反方依次」发言；
- 每名成员独立持有私有 system prompt，公开的只有发言正文；
- 支持 reasoning_effort（思考强度），不支持的 provider 自动忽略；
- 结束后由独立裁判模型裁决胜负，输出 WINNER 标记 + 总结；
- 事件通过队列广播给所有 SSE 订阅者，非增量的消息同时落库。
"""

from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..config import Settings
from ..providers import chat_stream
from ..types import ModelSpec

SIDE_CN = {"pro": "正方", "con": "反方"}

PUBLIC_RULES = """辩论规则（对所有参与者同等生效）：
1. 你只能看到其他参与者的公开发言，看不到任何人的设定或提示词；不要猜测、也不要提及"提示词"。
2. 这是真实的观点交锋：可以明确指出"对方这一点错了"，但必须针对具体论点。
3. 不要复述已经说过的内容；每一轮都要推进——补新证据、承认被驳倒的部分、或换一个角度。
4. 只输出发言正文，不要写"好的""以下是"之类的开场白，不要用代码块包裹整段回答。
5. 控制在 400 字以内。"""

DEFAULT_JUDGE_PROMPT = """你是一场辩论的裁判。你的任务不是复述双方观点，而是做出可被检验的判断。

评判标准（按权重排序）：
1. 论证结构：论点是否有明确前提、推理链是否成立、有没有偷换概念。
2. 证据质量：引用的事实、数据、案例是否具体且与结论相关；是否只是断言。
3. 回应质量：是否正面回应对手最强的论点，还是绕开它。
4. 一致性与边界：有没有自相矛盾，是否说明结论的适用边界。

输出格式（严格遵守，第一行必须是这一行，不要加粗、不要加代码块）：
WINNER: pro 或 con 或 draw

随后输出：
## 判定理由
（3-5 条，指出胜负手在哪一轮、哪一句）

## 双方最强论点
正方：…（一句）
反方：…（一句）

## 双方最大漏洞
正方：…（一句）
反方：…（一句）

## 结论
（150 字内，给出可执行的判断，不要和稀泥）"""

WINNER_RE = re.compile(r"WINNER\s*[:：]\s*(pro|con|draw)", re.IGNORECASE)


@dataclass
class Seat:
    side: str                  # pro | con
    name: str
    spec: ModelSpec
    prompt: str
    effort: str = ""
    participant_id: int = 0

    @property
    def side_cn(self) -> str:
        return SIDE_CN.get(self.side, self.side)


@dataclass
class Judge:
    spec: ModelSpec
    prompt: str = DEFAULT_JUDGE_PROMPT
    effort: str = ""


@dataclass
class DebateRunner:
    store: Any
    session_id: str
    topic: str
    rounds: int
    seats: list[Seat]
    judge: Judge | None
    settings: Settings
    max_tokens: int = 2048

    _subscribers: list[queue.Queue] = field(default_factory=list)
    _buffer: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _closed: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = None
    _seq: int = 0
    finished_at: float = 0.0

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"debate-{self.session_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        """停止并在完成后不再写库：删除会话时用，避免孤儿行。"""

        self._closed.set()
        self._stop.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            for event in self._buffer:
                q.put(event)
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _emit(self, event: str, data: dict[str, Any], keep: bool = True) -> None:
        payload = {"event": event, "data": data}
        with self._lock:
            if keep:
                self._buffer.append(payload)
            targets = list(self._subscribers)
        for q in targets:
            q.put(payload)

    # ------------------------------------------------------------ 主循环

    def _run(self) -> None:
        try:
            self._debate()
        except Exception as exc:  # 兜底：任何未预期异常都要让前端知道
            self._emit("error", {"message": f"{exc.__class__.__name__}: {exc}"})
            if not self._closed.is_set():
                self.store.finish_session(self.session_id, "error")
            self._emit("done", {"status": "error"})
        finally:
            self.finished_at = time.time()

    def _debate(self) -> None:
        transcript: list[tuple[str, str, str]] = []  # (name, side_cn, content)
        pro = [s for s in self.seats if s.side == "pro"]
        con = [s for s in self.seats if s.side == "con"]

        self._emit(
            "status",
            {
                "phase": "debating",
                "round": 0,
                "total_rounds": self.rounds,
                "message": f"{len(pro)} 对 {len(con)}，共 {self.rounds} 轮",
            },
        )

        for round_no in range(1, self.rounds + 1):
            if self._stop.is_set():
                break
            for seat in pro + con:  # 每轮：正方全员 → 反方全员
                if self._stop.is_set():
                    break
                self._speak(seat, round_no, transcript)

        if self._closed.is_set():
            return

        if self._stop.is_set():
            self.store.finish_session(self.session_id, "stopped")
            self._emit("done", {"status": "stopped"})
            return

        winner, summary = "", ""
        if self.judge is not None:
            winner, summary = self._judge(transcript)

        if self._closed.is_set():
            return

        self.store.finish_session(self.session_id, "done", summary, winner)
        self._emit("verdict", {"winner": winner, "summary": summary})
        self._emit("done", {"status": "done"})

    # ------------------------------------------------------------ 单人发言

    def _speak(self, seat: Seat, round_no: int, transcript: list[tuple[str, str, str]]) -> None:
        self._emit(
            "status",
            {
                "phase": "speaking",
                "round": round_no,
                "total_rounds": self.rounds,
                "side": seat.side,
                "name": seat.name,
            },
        )
        messages = self._speech_messages(seat, round_no, transcript)

        def on_delta(text: str) -> None:
            self._emit(
                "delta",
                {"side": seat.side, "name": seat.name, "round": round_no, "text": text},
                keep=False,
            )

        result = self._call(seat.spec, messages, seat.effort, on_delta)

        # 会话已被删除：不再落库，避免留下孤儿行
        if self._closed.is_set():
            return

        self._seq += 1
        payload = {
            "round": round_no,
            "seq": self._seq,
            "participant_id": seat.participant_id,
            "side": seat.side,
            "name": seat.name,
            "model": seat.spec.model,
            "content": result.content,
            "tokens": result.tokens,
            "elapsed": round(result.elapsed, 2),
            "error": result.error or "",
        }
        payload["id"] = self.store.add_message(self.session_id, payload)
        self._emit("message", payload)

        if result.ok:
            transcript.append((seat.name, seat.side_cn, result.content))

    def _speech_messages(
        self, seat: Seat, round_no: int, transcript: list[tuple[str, str, str]]
    ) -> list[dict]:
        parts = [f"# 辩题\n{self.topic}"]
        if transcript:
            blocks = [f"【{name} · {side}】\n{content}" for name, side, content in transcript]
            parts.append("# 目前为止的公开发言\n" + "\n\n".join(blocks))
        else:
            parts.append("# 目前为止的公开发言\n（还没有人发言，你是第一位）")
        parts.append(
            f"# 现在轮到你\n你是{seat.name}（{seat.side_cn}），第 {round_no}/{self.rounds} 轮发言。"
            "请直接给出你的发言。"
        )
        return [
            {"role": "system", "content": seat.prompt.strip() + "\n\n" + PUBLIC_RULES},
            {"role": "user", "content": "\n\n".join(parts)},
        ]

    # ------------------------------------------------------------ 裁判

    def _judge(self, transcript: list[tuple[str, str, str]]) -> tuple[str, str]:
        assert self.judge is not None
        self._emit("status", {"phase": "judging", "round": self.rounds,
                              "total_rounds": self.rounds, "message": "裁判正在裁决"})

        blocks = [f"【{name} · {side}】\n{content}" for name, side, content in transcript]
        prompt = self.judge.prompt.strip() or DEFAULT_JUDGE_PROMPT
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"# 辩题\n{self.topic}\n\n"
                    f"# 完整辩论记录\n" + "\n\n".join(blocks) + "\n\n"
                    "现在请你依据上述记录裁决。记住第一行必须是 WINNER: pro / con / draw。"
                ),
            },
        ]

        def on_delta(text: str) -> None:
            self._emit("judge_delta", {"text": text}, keep=False)

        result = self._call(self.judge.spec, messages, self.judge.effort, on_delta, max_tokens=4096)
        raw = result.content.strip()
        if result.error and not raw:
            return "", f"裁判调用失败：{result.error}"

        match = WINNER_RE.search(raw)
        winner = match.group(1).lower() if match else ""
        summary = WINNER_RE.sub("", raw, count=1).strip()
        summary = summary.lstrip(" \n:：-").strip()
        return winner, summary

    # ------------------------------------------------------------ 模型调用

    def _call(
        self,
        spec: ModelSpec,
        messages: list[dict],
        effort: str,
        on_delta,
        max_tokens: int | None = None,
    ):
        extra = _effort_extra(spec.provider, effort)
        result = chat_stream(
            spec,
            messages,
            max_tokens=max_tokens or self.max_tokens,
            temperature=0.8,
            timeout=self.settings.model_timeout,
            on_delta=on_delta,
            extra=extra,
        )
        # 某些服务不认 reasoning_effort，带了会 400：去掉重试一次
        if result.error and extra and "400" in (result.error or ""):
            result = chat_stream(
                spec,
                messages,
                max_tokens=max_tokens or self.max_tokens,
                temperature=0.8,
                timeout=self.settings.model_timeout,
                on_delta=on_delta,
            )
        return result


def _effort_extra(provider: str, effort: str) -> dict[str, Any] | None:
    if not effort:
        return None
    value = effort.lower()
    if provider == "deepseek":
        if value in ("low", "high", "max"):
            return {"reasoning_effort": value}
        return None
    if provider in ("openai", "xai"):
        mapped = {
            "low": "low",
            "medium": "medium",
            "high": "high",
            "max": "high",
        }.get(value)
        # 未知档位不要发 reasoning_effort: null，那会让部分服务直接 400
        return {"reasoning_effort": mapped} if mapped else None
    # 本地与聚合服务默认不传，避免 400
    return None


def parse_winner(text: str) -> str:
    match = WINNER_RE.search(text or "")
    return match.group(1).lower() if match else ""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
