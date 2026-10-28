"""讨论方法编排层：把一场讨论编译成一条有序的 Step 流水线。

引擎的职责边界很小：按顺序取出 ``Step``、用 ``agent_index`` 找到 ``Agent``、
把 ``instruction`` 拼到对话历史后面发请求。因此本模块只负责**结构**——
谁在第几阶段、以什么角色、能看到多少历史、被要求做什么——不涉及模型调用、
渲染、存储或任何 I/O。

设计约定
--------
1. 每个方法都是一个纯函数
   ``build(topic, agents, rounds, synthesizer_index) -> list[Step]``：
   相同输入必然得到相同计划，无随机、无副作用，便于测试与回放。
2. ``visibility`` 承担信息隔离：
   - ``none``：看不到任何历史，引擎会把**连续的、同 phase 同 group** 的
     ``none`` 步并行执行（首轮独立观点、牛津立场确认、德尔菲匿名估计）；
   - ``all``：看到全部历史，用于互相引用/反驳；
   - ``self``：只看自己说过的话，用于自我批评、自我修订。
3. 所有 ``instruction`` 都是可直接执行的中文指令片段，**不含对话历史**，
   可以放心写「结合以上发言」；引擎会另外补充话题与历史。
4. 单 Agent 场景（``len(agents) == 1``）统一退化为
   「独白 → 自我批评 → 修订」，保证方法表对任意 Agent 数量都可用；
   只有 ``standard`` 明确要求至少 2 人（圆桌需要对话者）。
5. ``agent_index`` 永远落在 ``range(len(agents))`` 内，``synthesizer_index``
   会被取模收敛到合法下标，避免上游传参越界。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .types import Agent, Step

__all__ = ["MethodSpec", "METHODS", "get_method", "method_table"]


# ---------------------------------------------------------------- 数据结构


def _unbuilt(topic: str, agents: list[Agent], rounds: int = 2, synthesizer_index: int = 0) -> list[Step]:
    """占位实现：任何忘记挂 build 的方法都会立刻暴露。"""

    raise NotImplementedError("MethodSpec.build 尚未挂载")


@dataclass(frozen=True)
class MethodSpec:
    """一种讨论方法的元信息 + 计划生成函数。"""

    name: str                    # 命令行使用的标识，如 "standard"
    title: str                   # 中文展示名，如「标准圆桌」
    description: str             # 一句话说明
    min_agents: int              # 建议的最少参与者数
    needs_even: bool = False     # 是否需要偶数个 Agent（正反平分）
    fixed_rounds: bool = False   # True 表示结构固定，忽略 rounds 参数
    build: Callable[..., list[Step]] = field(default=_unbuilt, repr=False, compare=False)

    def describe(self) -> str:
        """一行摘要，供 CLI 列表展示。"""

        tags: list[str] = [f"≥{self.min_agents} 人"]
        if self.needs_even:
            tags.append("需偶数")
        if self.fixed_rounds:
            tags.append("固定轮次")
        return f"{self.title}（{'/'.join(tags)}）：{self.description}"


# ---------------------------------------------------------------- 通用工具


def _norm_index(index: int, size: int) -> int:
    """把任意下标收敛到 ``[0, size)``。"""

    if size <= 0:
        raise ValueError("agents 不能为空")
    return int(index) % size


def _solo_steps(topic: str, agents: list[Agent]) -> list[Step]:
    """单 Agent 的通用退化形态：独白 → 自我批评 → 修订。"""

    opening = (
        f"讨论话题：{topic}\n"
        "本轮只有你一个人，请把它当成一次独立思考。完成三件事：\n"
        "1) 用一句话给出你的核心结论；\n"
        "2) 列出 3 条支撑理由，每条附一个具体依据（数据、案例或机制）；\n"
        "3) 指出你这条结论成立所依赖的最关键前提。\n"
        "控制在 400 字以内，不要写寒暄。"
    )
    critique = (
        "现在请扮演一位刻薄的批评者，审阅你刚才的回答（只能看到自己的发言）。\n"
        "1) 指出其中最站不住脚的一条理由，说明它为什么可能错；\n"
        "2) 构造一个能推翻你结论的具体反例（要有场景细节）；\n"
        "3) 指出你在回答里回避或含糊带过的问题。\n"
        "不要为了显得客观而平均分配批评，集中打在真正的薄弱处，300 字以内。"
    )
    revision = (
        "最后做一次诚实的修订：\n"
        "1) 保留哪些判断（说明经得起批评的理由）；\n"
        "2) 收回或修改哪些判断（对应上面的哪条批评）；\n"
        "3) 给出修订后的结论与 2 条可执行建议；\n"
        "4) 说明还有哪些信息你无法确认。\n"
        "400 字以内，用 Markdown 小标题组织。"
    )
    return [
        Step(0, "独立陈述", "独立发言人", opening, visibility="none", group=0, meta={"kind": "solo"}),
        Step(0, "自我批评", "自我质疑者", critique, visibility="self", group=0, meta={"kind": "solo_critique"}),
        Step(0, "修订结论", "修订者", revision, visibility="self", group=0, meta={"kind": "solo_revision"}),
    ]


def _synthesis_step(
    topic: str,
    agents: list[Agent],
    synthesizer_index: int,
    *,
    phase: str = "综合结论",
    role: str = "总结者",
    instruction: str,
    kind: str = "synthesis",
    extra_meta: dict | None = None,
) -> Step:
    """构造收尾的综合步。"""

    meta = {"kind": kind}
    if extra_meta:
        meta.update(extra_meta)
    return Step(
        _norm_index(synthesizer_index, len(agents)),
        phase,
        role,
        instruction,
        visibility="all",
        group=0,
        meta=meta,
    )


def _name_list(agents: list[Agent]) -> str:
    return "、".join(agent.name for agent in agents)


# ---------------------------------------------------------------- 1. 标准圆桌


def _build_standard(
    topic: str,
    agents: list[Agent],
    rounds: int = 2,
    synthesizer_index: int = 0,
) -> list[Step]:
    """标准圆桌：独立首轮 → 多轮自由讨论 → 综合。"""

    if len(agents) < 2:
        raise ValueError(
            "standard（标准圆桌）至少需要 2 个 Agent；只有一个模型时请改用 socratic 或 delphi 等方法。"
        )

    rounds = max(1, int(rounds))
    steps: list[Step] = []

    # Phase 1：各自独立首轮观点（互不可见，可并行）
    for index, _agent in enumerate(agents):
        steps.append(
            Step(
                index,
                "开场陈述",
                "独立陈述者",
                (
                    f"讨论话题：{topic}\n"
                    "这是首轮独立陈述：你看不到其他人的发言，也不需要猜测他们会说什么。\n"
                    "请完成：\n"
                    "1) 用一句话亮明你的核心立场（要能被反驳，不要和稀泥）；\n"
                    "2) 列出支撑该立场的 3 条理由，每条附一个具体依据（数据、案例或机制）；\n"
                    "3) 指出你认为最容易被其他人忽略的一个前提或风险。\n"
                    "控制在 400 字以内，不要写开场寒暄和总结套话。"
                ),
                visibility="none",
                group=0,
                meta={"kind": "opening", "round": 0, "total_rounds": rounds},
            )
        )

    # Phase 2：多轮自由讨论（可见全部历史）
    for round_no in range(1, rounds + 1):
        for index in range(len(agents)):
            steps.append(
                Step(
                    index,
                    f"圆桌讨论 · 第 {round_no} 轮",
                    f"讨论者（第 {round_no} 轮）",
                    (
                        f"讨论话题：{topic}\n"
                        f"这是第 {round_no}/{rounds} 轮自由讨论，你可以看到此前全部发言。\n"
                        "请严格按以下要求发言：\n"
                        "1) 必须点名回应至少一位其他参与者（例如「我不同意 "
                        f"{agents[(index + 1) % len(agents)].name} 的第二点」），并引用其具体论点；\n"
                        "2) 请勿重复你已经说过的内容——只做补充、修正或反驳；\n"
                        "3) 如果你被某人说服，直接承认，并说明哪一点改变了你的判断；\n"
                        "4) 允许明确反对，但必须给出反对的依据，不要为了和气而妥协；\n"
                        "5) 最后用一句话给出你此刻最核心的主张。\n"
                        "控制在 350 字以内。"
                    ),
                    visibility="all",
                    group=0,
                    meta={"kind": "discussion", "round": round_no, "total_rounds": rounds},
                )
            )

    # Phase 3：综合
    steps.append(
        _synthesis_step(
            topic,
            agents,
            synthesizer_index,
            phase="综合结论",
            role="总结者",
            instruction=(
                f"讨论话题：{topic}\n"
                "你是本场讨论的总结者。请只基于以上真实出现过的发言做综合，用 Markdown 输出：\n"
                "## 共识结论\n3-5 条，只写多数发言者真正达成一致的内容；\n"
                "## 主要分歧\n逐条列出分歧点、各方立场，以及各方最强的那个论据；\n"
                "## 可执行建议\n3 条，每条包含具体动作、由谁执行、如何验证有效；\n"
                "## 尚未解决\n2-3 条目前无法回答的问题。\n"
                "不要引入前文没有的新观点；凡属他人贡献，注明是哪位发言者提出的。"
            ),
        )
    )
    return steps


# ---------------------------------------------------------------- 2. 牛津辩论


def _oxford_sides(count: int) -> tuple[list[int], list[int]]:
    """前一半为正方、后一半为反方；奇数时正方多一人。"""

    half = count // 2
    pro_count = half + (count % 2)
    return list(range(pro_count)), list(range(pro_count, count))


def _build_oxford(
    topic: str,
    agents: list[Agent],
    rounds: int = 2,
    synthesizer_index: int = 0,
) -> list[Step]:
    """牛津辩论：立场确认 → 两轮交替反驳 → 交叉质询 → 结辩 → 裁判点评。

    ``needs_even`` 由调用方提示用户；若真的传入奇数个 Agent，本函数不报错，
    而是让正方多占一席（前 half+1 人为正方，其余为反方）。
    """

    if len(agents) < 2:
        return _solo_steps(topic, agents)

    pro, con = _oxford_sides(len(agents))
    pro_names = _name_list([agents[i] for i in pro])
    con_names = _name_list([agents[i] for i in con])
    steps: list[Step] = []

    # 阶段 1：立场确认（正反各一人，互不可见，可并行）
    for index, side, label, side_names, other_names in (
        (pro[0], "pro", "正方", pro_names, con_names),
        (con[0], "con", "反方", con_names, pro_names),
    ):
        stance = "支持" if side == "pro" else "反对"
        steps.append(
            Step(
                index,
                "立场确认",
                f"{label}一辩",
                (
                    f"辩题：{topic}\n"
                    f"你被分到{label}，立场是**{stance}**该辩题。本方成员：{side_names}；"
                    f"对方是{other_names}。\n"
                    "这是开场立论，你暂时看不到对方发言。请完成：\n"
                    "1) 用一句话清晰定义你方立场（界定范围，避免被对方偷换概念）；\n"
                    f"2) 给出 3 条{stance}的论据，按强度排序，每条附具体证据或机制；\n"
                    "3) 预判对方最可能提出的最强反驳，并提前说明为什么它不成立；\n"
                    "4) 用一句话给出你方判准：什么样的证据会让你方认输。\n"
                    "控制在 450 字以内，不要人身攻击，不要空喊口号。"
                ),
                visibility="none",
                group=0,
                meta={"kind": "opening", "side": side},
            )
        )

    # 阶段 2：两轮交替反驳（正方先手）
    order: list[tuple[int, str, str]] = [(i, "正方", "pro") for i in pro] + [(i, "反方", "con") for i in con]
    for round_no in (1, 2):
        for index, label, side in order:
            target = con_names if side == "pro" else pro_names
            steps.append(
                Step(
                    index,
                    f"交替反驳 · 第 {round_no} 轮",
                    f"{label}辩手",
                    (
                        f"辩题：{topic}\n"
                        f"这是第 {round_no}/2 轮交替反驳，你是{label}。对方成员：{target}。\n"
                        "请按顺序完成：\n"
                        "1) 先用自己的话复述对方一个具体论点（一句话），再点名回应"
                        f"（例如「{target.split('、')[0]} 说……，但这忽略了……」）；\n"
                        "2) 给出你的反驳或反例，必须具体，不能只说「对方片面」；\n"
                        "3) 不得重复本方一辩已经说过的论证，只能加强、修补或换角度；\n"
                        "4) 结尾用一句话重申你方立场为什么仍然成立。\n"
                        "控制在 350 字以内。"
                    ),
                    visibility="all",
                    group=0,
                    meta={"kind": "rebuttal", "side": side, "round": round_no},
                )
            )

    # 阶段 3：交叉质询（各提一问，对方正面回答）
    steps.append(
        Step(
            pro[0],
            "交叉质询 · 正方提问",
            "正方质询者",
            (
                f"辩题：{topic}\n"
                f"现在由你向反方（{con_names}）提出**一个**最尖锐的问题。要求：\n"
                "1) 只问不答，不要顺便替对方回答；\n"
                "2) 问题必须打在其论证的承重墙上——未证明的前提、被回避的成本、自相矛盾之处；\n"
                "3) 问题必须具体到可被一句话回答，禁止「你如何解释一切」这类空问题；\n"
                "4) 用一句「请反方回答：……」收尾。\n"
                "150 字以内。"
            ),
            visibility="all",
            meta={"kind": "cross_exam_ask", "side": "pro"},
        )
    )
    steps.append(
        Step(
            con[0],
            "交叉质询 · 反方回答",
            "反方答辩者",
            (
                f"辩题：{topic}\n"
                "对方刚刚向你方提出了质询问题。请：\n"
                "1) 第一句就正面回答，不要绕开或用反问挡回去；\n"
                "2) 给出支撑你回答的一条具体证据；\n"
                "3) 如果这个问题确实戳中了要害，明确承认，并说明你方立场要如何收窄才能成立；\n"
                "4) 回答完再补一句：这个问题为什么不会推翻你方整体立场。\n"
                "300 字以内。"
            ),
            visibility="all",
            meta={"kind": "cross_exam_answer", "side": "con"},
        )
    )
    steps.append(
        Step(
            con[-1],
            "交叉质询 · 反方提问",
            "反方质询者",
            (
                f"辩题：{topic}\n"
                f"现在由你向正方（{pro_names}）提出**一个**最尖锐的问题。要求：\n"
                "1) 只问不答；\n"
                "2) 针对正方立论中最脆弱的假设、被忽略的反例或未计的成本；\n"
                "3) 问题必须具体、可被一句话回答；\n"
                "4) 用一句「请正方回答：……」收尾。\n"
                "150 字以内。"
            ),
            visibility="all",
            meta={"kind": "cross_exam_ask", "side": "con"},
        )
    )
    steps.append(
        Step(
            pro[-1],
            "交叉质询 · 正方回答",
            "正方答辩者",
            (
                f"辩题：{topic}\n"
                "对方刚刚向你方提出了质询问题。请：\n"
                "1) 第一句直接回答；\n"
                "2) 给出一条具体证据；\n"
                "3) 若被打中要害，坦率承认需要收窄哪部分主张；\n"
                "4) 说明为什么这不影响你方核心立场。\n"
                "300 字以内。"
            ),
            visibility="all",
            meta={"kind": "cross_exam_answer", "side": "pro"},
        )
    )

    # 阶段 4：结辩
    steps.append(
        Step(
            pro[-1],
            "结辩",
            "正方结辩",
            (
                f"辩题：{topic}\n"
                "这是你方最后的结辩发言。请：\n"
                "1) 用 3 句话概括你方全场最强的论证链（不要罗列所有论点）；\n"
                "2) 指出对方在整场辩论中始终没有回答的那一个问题；\n"
                "3) 承认对方最有价值的一个观点，并说明它为何不足以改变结论；\n"
                "4) 最后用一句话请裁判采纳。\n"
                "300 字以内。"
            ),
            visibility="all",
            meta={"kind": "closing", "side": "pro"},
        )
    )
    steps.append(
        Step(
            con[-1],
            "结辩",
            "反方结辩",
            (
                f"辩题：{topic}\n"
                "这是你方最后的结辩发言。请：\n"
                "1) 用 3 句话概括你方全场最强的论证链；\n"
                "2) 指出对方在整场辩论中始终没有回答的那一个问题；\n"
                "3) 承认对方最有价值的一个观点，并说明它为何不足以改变结论；\n"
                "4) 最后用一句话请裁判采纳。\n"
                "300 字以内。"
            ),
            visibility="all",
            meta={"kind": "closing", "side": "con"},
        )
    )

    # 阶段 5：裁判点评
    steps.append(
        _synthesis_step(
            topic,
            agents,
            synthesizer_index,
            phase="裁判点评",
            role="裁判",
            kind="judgement",
            instruction=(
                f"辩题：{topic}\n"
                "你是本场辩论的裁判，不属于任何一方。请中立地裁定：\n"
                "## 判定\n明确说出哪一方论证更强（允许判平局），并用 2-3 句说明判准；\n"
                "## 双方漏洞\n正方、反方各至少指出 1 条关键漏洞（含偷换概念、证据不足、回避质询）；\n"
                "## 各自最有价值的观点\n正反方各 1 条，说明它值得保留的理由；\n"
                "## 中立结论\n抛开胜负，这个辩题在现实中应该如何取舍，给出可执行判断。\n"
                "不要因为要平衡而各打五十大板，也不要引入辩论中没有出现的论据。500 字以内。"
            ),
        )
    )
    return steps


# ---------------------------------------------------------------- 3. 魔鬼代言人


def _build_advocate(
    topic: str,
    agents: list[Agent],
    rounds: int = 2,
    synthesizer_index: int = 0,
) -> list[Step]:
    """魔鬼代言人：独立方案 → 收敛共识 → 集中攻击 → 逐一辩护 → 修订。"""

    if len(agents) < 2:
        return _solo_steps(topic, agents)

    rounds = max(1, int(rounds))
    synth = _norm_index(synthesizer_index, len(agents))
    devil = next((i for i in range(len(agents)) if i != synth), synth)
    steps: list[Step] = []

    # Phase 1：各自独立给初步方案
    for index in range(len(agents)):
        steps.append(
            Step(
                index,
                "初步方案",
                "方案提出者",
                (
                    f"讨论话题：{topic}\n"
                    "这是独立阶段，你看不到其他人的方案。请：\n"
                    "1) 给出你的初步方案，用 3-5 个可执行步骤描述，不要只给原则；\n"
                    "2) 明确写出你依赖的 2 个关键假设；\n"
                    "3) 说明预期收益（尽量量化）与最大的风险；\n"
                    "4) 写清楚在什么条件下你会认为这个方案失败。\n"
                    "400 字以内。"
                ),
                visibility="none",
                group=0,
                meta={"kind": "proposal", "round": 0},
            )
        )

    # Phase 2：讨论收敛出共识
    for round_no in range(1, rounds + 1):
        for index in range(len(agents)):
            steps.append(
                Step(
                    index,
                    f"共识收敛 · 第 {round_no} 轮",
                    f"方案讨论者（第 {round_no} 轮）",
                    (
                        f"讨论话题：{topic}\n"
                        f"这是第 {round_no}/{rounds} 轮，目标是收敛出一个所有人都能接受的方案。\n"
                        "请：\n"
                        "1) 点名指出你的方案与谁的哪一条存在实质冲突，并给出取舍理由；\n"
                        "2) 主动吸收别人方案里比你更好的部分，说明你改了什么；\n"
                        "3) 不要重复你上一轮说过的话，只写增量；\n"
                        "4) 结尾用 3 条要点给出「我认可的共同方案」当前版本。\n"
                        "350 字以内。"
                    ),
                    visibility="all",
                    meta={"kind": "converge", "round": round_no, "total_rounds": rounds},
                )
            )

    # Phase 3：魔鬼代言人集中攻击（刻意不选 synthesizer）
    steps.append(
        Step(
            devil,
            "魔鬼代言人攻击",
            "魔鬼代言人",
            (
                f"讨论话题：{topic}\n"
                "你的任务不是提出替代方案，而是尽最大努力击穿上面的共识。请：\n"
                "1) 指出该共识依赖的 3 个最脆弱的假设，逐个说明它们为什么可能不成立；\n"
                "2) 构造一个让共识彻底失效的具体场景（要有时间、角色、因果链细节）；\n"
                "3) 指出共识内部自相矛盾、或成本/风险被系统性低估的地方；\n"
                "4) 给出最坏情况下的后果，并说明它是否可承受。\n"
                "不要留情面，也不要为了显得公允而自我削弱——攻击得越具体，讨论越有价值。500 字以内。"
            ),
            visibility="all",
            meta={"kind": "devil_attack"},
        )
    )

    # Phase 4：其他人逐一回应攻击
    for index in range(len(agents)):
        if index == devil:
            continue
        steps.append(
            Step(
                index,
                "回应攻击",
                "辩护者",
                (
                    f"讨论话题：{topic}\n"
                    "刚刚魔鬼代言人对你们的共识发起了攻击。请逐条处理：\n"
                    "1) 明确接受哪几条（不要全盘否认），并说明方案要如何修改；\n"
                    "2) 反驳哪几条，每条给出反驳依据（证据、机制或反例）；\n"
                    "3) 如果某条攻击无法反驳，直接说明这一点，并给出应对预案；\n"
                    "4) 用一句话总结：经过这次攻击，方案变得更强还是已不成立。\n"
                    "350 字以内。"
                ),
                visibility="all",
                meta={"kind": "defense"},
            )
        )

    # Phase 5：synthesizer 修订结论
    steps.append(
        _synthesis_step(
            topic,
            agents,
            synthesizer_index,
            phase="修订结论",
            role="修订者",
            kind="revision",
            instruction=(
                f"讨论话题：{topic}\n"
                "请基于全部发言做出修订后的结论，必须显式区分三类内容：\n"
                "## 被击穿的部分\n哪些原有主张在攻击下确实不成立，原因是什么；\n"
                "## 需要修补的部分\n哪些主张仍然成立但需要附加条件或成本控制，具体补什么；\n"
                "## 站得住的部分\n哪些结论经得起攻击，为什么；\n"
                "## 修订后的方案\n整合以上三类，给出最终方案与它的前置条件；\n"
                "## 监控信号\n列出 2 个可以提前预警方案失效的可观测指标。\n"
                "不要回避已被击穿的内容，也不要重复未被质疑的论述。550 字以内。"
            ),
        )
    )
    return steps


# ---------------------------------------------------------------- 4. 苏格拉底


def _build_socratic(
    topic: str,
    agents: list[Agent],
    rounds: int = 2,
    synthesizer_index: int = 0,
) -> list[Step]:
    """苏格拉底式追问：提问者与回答者交替，三轮追问后由提问者收束。

    注意：按方法定义，最后的「我们究竟确认了什么」由**提问者（index 0）**给出，
    因此调用方应保持 ``synthesizer_index=0``，引擎才会把这段收束当作最终结论。
    """

    if len(agents) < 2:
        return _solo_steps(topic, agents)

    questioner = 0
    answerers = list(range(1, len(agents)))
    steps: list[Step] = []

    steps.append(
        Step(
            questioner,
            "提出问题",
            "提问者",
            (
                f"讨论话题：{topic}\n"
                "你的角色是提问者，全程不给结论，只负责把问题问到底。本轮请：\n"
                "1) 提出一个真正困难的核心问题（不是「什么是 X」这种定义题，而是会逼出取舍的问题）；\n"
                "2) 明确指出这个问题背后的 2-3 个隐含假设，逐个说明如果假设不成立会怎样；\n"
                "3) 向回答者提出第一个必须正面回答的问题，一次只问一个。\n"
                "不要提供你自己的答案，不要暗示你期待的回答。300 字以内。"
            ),
            visibility="none",
            group=0,
            meta={"kind": "question", "round": 0},
        )
    )

    for index in answerers:
        steps.append(
            Step(
                index,
                "作答",
                "回答者",
                (
                    f"讨论话题：{topic}\n"
                    "请正面回答提问者的问题：\n"
                    "1) 第一句就给出你的答案，不要先铺陈背景；\n"
                    "2) 写出完整推理链：从哪个前提出发、经过几步、得出什么；\n"
                    "3) 明确标出你不确定的地方（用「我不确定」开头说明），不要含糊带过；\n"
                    "4) 说明你的答案在什么条件下会不成立。\n"
                    "350 字以内。"
                ),
                visibility="all",
                meta={"kind": "answer", "round": 0},
            )
        )

    for round_no in range(1, 4):
        steps.append(
            Step(
                questioner,
                f"追问 · 第 {round_no} 轮",
                "提问者",
                (
                    f"讨论话题：{topic}\n"
                    f"这是第 {round_no}/3 轮追问。请从回答者刚才的发言里挑出最模糊或最可疑的一处，"
                    "**引用其原话**，然后追问：\n"
                    "1) 这个说法具体指什么？边界在哪里？\n"
                    "2) 如果它所依赖的前提不成立，你的结论还剩多少？\n"
                    "3) 有没有能直接反驳它的反例？\n"
                    "只提 1-2 个问题，不要自己回答，也不要换一个新话题。250 字以内。"
                ),
                visibility="all",
                meta={"kind": "followup", "round": round_no},
            )
        )
        for index in answerers:
            steps.append(
                Step(
                    index,
                    f"修正 · 第 {round_no} 轮",
                    "回答者",
                    (
                        f"讨论话题：{topic}\n"
                        "针对刚才的追问修正你的回答：\n"
                        "1) 明确哪些说法你要收回或改写（写出修改后的版本）；\n"
                        "2) 哪些你坚持，并给出比上次更强的理由或证据；\n"
                        "3) 回答追问中你之前回避的那部分；\n"
                        "4) 不要重复上一轮已经说过的内容。\n"
                        "300 字以内。"
                    ),
                    visibility="all",
                    meta={"kind": "revision", "round": round_no},
                )
            )

    steps.append(
        Step(
            questioner,
            "收束",
            "提问者",
            (
                f"讨论话题：{topic}\n"
                "追问结束，请回答「我们究竟确认了什么」。用 Markdown 输出：\n"
                "## 确认成立的结论\n3-5 条，每条注明它是被哪一轮追问检验过的；\n"
                "## 被推翻或削弱的原判断\n2 条，说明是被哪个反问打掉的；\n"
                "## 仍未解决的核心难题\n1 条，并说明还需要什么信息或证据才能推进；\n"
                "## 一句忠告\n给读者一句关于如何看待这个议题的提醒。\n"
                "不要引入未讨论过的新观点。400 字以内。"
            ),
            visibility="all",
            meta={"kind": "closure"},
        )
    )
    return steps


# ---------------------------------------------------------------- 5. 德尔菲


def _build_delphi(
    topic: str,
    agents: list[Agent],
    rounds: int = 2,
    synthesizer_index: int = 0,
) -> list[Step]:
    """德尔菲法：匿名估计 → 匿名互评 → 二次估计 → 汇总收敛。"""

    if len(agents) < 2:
        return _solo_steps(topic, agents)

    steps: list[Step] = []

    # 阶段 1：匿名独立估计
    for index in range(len(agents)):
        steps.append(
            Step(
                index,
                "匿名独立估计",
                "独立估计者",
                (
                    f"讨论话题：{topic}\n"
                    "这是匿名独立估计阶段：你看不到其他人的判断，也不要猜测他们会怎么说。请：\n"
                    "1) 给出你的估计，必须是一个**具体数值或明确区间**（写清单位和时间范围）；\n"
                    "2) 给出置信度（高/中/低）以及大致概率；\n"
                    "3) 列出 3 条理由，每条附一个数据点或类比依据；\n"
                    "4) 指出最影响结果的 2 个关键变量，并说明它们往哪个方向变动会让你的估计改变多少。\n"
                    "350 字以内，禁止只给「取决于多种因素」这类回避式回答。"
                ),
                visibility="none",
                group=0,
                meta={"kind": "estimate", "round": 1},
            )
        )

    # 阶段 2：匿名互评（可见历史，但要求剥离发言人）
    for index in range(len(agents)):
        steps.append(
            Step(
                index,
                "匿名意见评估",
                "评估者",
                (
                    f"讨论话题：{topic}\n"
                    "以下是其他专家提交的匿名意见（已附在对话历史中）。重要规则：\n"
                    "**不要把观点和具体发言人绑定**——不要写「某某说」，只谈论点本身，保持德尔菲法的匿名性。\n"
                    "请完成：\n"
                    "1) 找出与你判断差异最大的 1-2 个观点，说明你认为它可能错在哪里，或它让你意识到了什么；\n"
                    "2) 指出所有意见共同忽略的那个变量；\n"
                    "3) 指出大家在哪个数量级上其实是一致的（这往往是可靠信号）。\n"
                    "300 字以内，不要透露或猜测任何发言者身份。"
                ),
                visibility="all",
                group=0,
                meta={"kind": "review", "round": 2, "anonymous": True},
            )
        )

    # 阶段 3：二次独立估计（仍保持匿名表述）
    for index in range(len(agents)):
        steps.append(
            Step(
                index,
                "二次估计",
                "复估者",
                (
                    f"讨论话题：{topic}\n"
                    "基于上一轮的匿名互评，给出你修正后的估计。要求：\n"
                    "1) 第一行写出最终数值/区间（与第一轮同口径，方便汇总）；\n"
                    "2) 明确写出「相比我第一次的估计，我上调 / 下调 / 维持，原因是……」；\n"
                    "3) 如果维持不变，说明为什么其他意见不足以动摇你，而不是简单坚持；\n"
                    "4) 写出你现在最不确定的那一个假设。\n"
                    "不要提及任何具体发言人。250 字以内。"
                ),
                visibility="all",
                group=0,
                meta={"kind": "reestimate", "round": 3, "anonymous": True},
            )
        )

    # 阶段 4：汇总
    steps.append(
        _synthesis_step(
            topic,
            agents,
            synthesizer_index,
            phase="汇总",
            role="汇总者",
            kind="aggregate",
            extra_meta={"aggregate": "median", "anonymous": True},
            instruction=(
                f"讨论话题：{topic}\n"
                "请对全部估计做统计性汇总，不要引入新判断：\n"
                "## 收敛结果\n给出所有最终估计的中位数与收敛区间（写明数值与单位），并说明样本数；\n"
                "## 意见分布\n列出最悲观、最乐观、主流三个位置的具体数值；\n"
                "## 分歧来源\n2-3 条，指出是哪些关键假设差异导致了数值差异，并说明哪一方证据更强；\n"
                "## 还缺什么信息\n列出若要进一步收敛，最需要补充的 2 项信息。\n"
                "保持匿名：只描述数值与论点，不写发言人姓名。400 字以内。"
            ),
        )
    )
    return steps


# ---------------------------------------------------------------- 6. 头脑风暴


def _build_brainstorm(
    topic: str,
    agents: list[Agent],
    rounds: int = 2,
    synthesizer_index: int = 0,
) -> list[Step]:
    """头脑风暴：发散（禁止评价）→ 构建（基于他人点子）→ 收敛（聚类+Top3）。"""

    if len(agents) < 2:
        return _solo_steps(topic, agents)

    steps: list[Step] = []

    # 阶段 1：发散
    for index in range(len(agents)):
        steps.append(
            Step(
                index,
                "发散",
                "发散者",
                (
                    f"讨论话题：{topic}\n"
                    "这是发散阶段，你看不到别人的想法。请给出 **5 个尽量不同**的点子：\n"
                    "1) 每个点子写一行，格式为「点子：一句话说明（为什么可能有效）」；\n"
                    "2) 5 个点子之间不能是同一想法的变体，至少 2 个要反常识或反直觉；\n"
                    "3) 本阶段**禁止任何评价**——不讨论可行性、成本、风险，也不要自我审查；\n"
                    "4) 至少 1 个点子要跳出常规框架（换主体、换场景、换约束）。\n"
                    "350 字以内，不要写引言和结语。"
                ),
                visibility="none",
                group=0,
                meta={"kind": "diverge", "round": 1},
            )
        )

    # 阶段 2：构建
    for index in range(len(agents)):
        peers = [a.name for i, a in enumerate(agents) if i != index]
        steps.append(
            Step(
                index,
                "构建",
                "构建者",
                (
                    f"讨论话题：{topic}\n"
                    f"其他人的点子已经在对话历史里（{_name_list(agents)}）。请：\n"
                    "1) 挑选 2 个**别人**的点子，做组合或改进；必须明确写出「基于 "
                    f"{peers[0]} 的点子」这样的归属；\n"
                    "2) 说明你具体改了什么、为什么改完更好；\n"
                    "3) 可以指出某个点子不可行，但必须同时给出替代做法；\n"
                    "4) 不要提出与已有点子完全无关的新点子。\n"
                    "350 字以内。"
                ),
                visibility="all",
                meta={"kind": "build", "round": 2},
            )
        )

    # 阶段 3：收敛
    steps.append(
        _synthesis_step(
            topic,
            agents,
            synthesizer_index,
            phase="收敛",
            role="收敛者",
            kind="converge",
            instruction=(
                f"讨论话题：{topic}\n"
                "请把全场点子收敛成可执行的结论：\n"
                "## 聚类\n归为 3-4 个主题，每个主题起名字并列出包含的点子（注明提出者）；\n"
                "## Top 3\n按「影响力 × 可行性」排序，每个写入选理由与最大风险；\n"
                "## 落地第一步\n为 Top1 设计下周就能开始做的第一个动作（具体到谁做什么、需要什么资源、如何判断有效）；\n"
                "## 淘汰清单\n列出被淘汰的点子及一句话原因。\n"
                "500 字以内。"
            ),
        )
    )
    return steps


# ---------------------------------------------------------------- 7. 权衡对比


def _build_tradeoff(
    topic: str,
    agents: list[Agent],
    rounds: int = 2,
    synthesizer_index: int = 0,
) -> list[Step]:
    """权衡对比：拆解方案与维度 → 逐维打分 → 对比表与分场景推荐。"""

    if len(agents) < 2:
        return _solo_steps(topic, agents)

    steps: list[Step] = []

    # 阶段 1：方案与维度拆解
    for index in range(len(agents)):
        steps.append(
            Step(
                index,
                "方案拆解",
                "方案分析师",
                (
                    f"讨论话题：{topic}\n"
                    "这是独立拆解阶段，你看不到其他人的分析。请：\n"
                    "1) 列出这个主题下值得比较的 2-4 个备选方案，每个起一个短名（便于后续打分）；\n"
                    "2) 提出 4-6 个评估维度（如成本、见效速度、可逆性、规模化难度、失败代价），"
                    "每个维度用一句话说明「高分代表什么」；\n"
                    "3) 指出其中哪个维度是决定性维度，为什么；\n"
                    "4) 本阶段不要打分，也不要比较优劣。\n"
                    "350 字以内。"
                ),
                visibility="none",
                group=0,
                meta={"kind": "decompose", "round": 1},
            )
        )

    # 阶段 2：逐维打分
    for index in range(len(agents)):
        steps.append(
            Step(
                index,
                "逐维打分",
                "评分者",
                (
                    f"讨论话题：{topic}\n"
                    "请对备选方案做量化对比：\n"
                    "1) 用 Markdown 表格输出，行为方案、列为评估维度，每格填 1-10 分；\n"
                    "2) 每个分数后用一句简短依据（写在表格下方的编号说明里，如「方案A·成本 4：需自建团队」）；\n"
                    "3) 你缺信息的格子填「?」，并在表下说明需要什么信息才能打分，不要凭感觉编造分数；\n"
                    "4) 最后说明你最没把握的是哪一行、为什么。\n"
                    "400 字以内，分数必须整数且落在 1-10。"
                ),
                visibility="all",
                meta={"kind": "score", "round": 2},
            )
        )

    # 阶段 3：汇总
    steps.append(
        _synthesis_step(
            topic,
            agents,
            synthesizer_index,
            phase="决策汇总",
            role="决策顾问",
            kind="decision",
            instruction=(
                f"讨论话题：{topic}\n"
                "请基于以上打分给出决策建议：\n"
                "## 对比表\n汇总成一张总表（各人分数取平均或给出区间，并说明你的汇总口径）；\n"
                "## 分场景推荐\n什么情况下选方案 A、什么情况下选方案 B，每种场景写清判断依据；\n"
                "## 翻转条件\n明确指出「在什么条件下推荐会翻转」——写出具体阈值或触发事件（例如预算低于 X、周期短于 Y）；\n"
                "## 现在就要决定的话\n如果必须今天拍板，你选哪个，最需要立刻验证的假设是什么。\n"
                "总分相近时不要强行分出高下，直接说明为什么打平。550 字以内。"
            ),
        )
    )
    return steps


# ---------------------------------------------------------------- 方法注册表

_STANDARD = MethodSpec(
    name="standard",
    title="标准圆桌",
    description="各自独立陈述后多轮互相质询，最后由一位成员综合出结论。",
    min_agents=2,
    build=_build_standard,
)

_OXFORD = MethodSpec(
    name="oxford",
    title="牛津辩论",
    description="正反方平分，经立论、交替反驳、交叉质询、结辩后由裁判点评。",
    min_agents=2,
    needs_even=True,
    fixed_rounds=True,
    build=_build_oxford,
)

_ADVOCATE = MethodSpec(
    name="advocate",
    title="魔鬼代言人",
    description="先收敛共识，再指定一人全力攻击，其余人辩护后修订结论。",
    min_agents=3,
    build=_build_advocate,
)

_SOCRATIC = MethodSpec(
    name="socratic",
    title="苏格拉底追问",
    description="提问者连追三轮，专挑模糊与矛盾，最后收束出真正确认的结论。",
    min_agents=2,
    fixed_rounds=True,
    build=_build_socratic,
)

_DELPHI = MethodSpec(
    name="delphi",
    title="德尔菲评估",
    description="匿名给出数值估计、匿名互评、二次估计，最后汇总中位数与分歧来源。",
    min_agents=3,
    fixed_rounds=True,
    build=_build_delphi,
)

_BRAINSTORM = MethodSpec(
    name="brainstorm",
    title="头脑风暴",
    description="先禁止评价地发散，再基于他人点子构建，最后聚类排出 Top3。",
    min_agents=2,
    build=_build_brainstorm,
)

_TRADEOFF = MethodSpec(
    name="tradeoff",
    title="权衡对比",
    description="拆出备选方案与评估维度，逐维打分后给出分场景推荐与翻转条件。",
    min_agents=2,
    build=_build_tradeoff,
)

METHOD_ORDER: tuple[MethodSpec, ...] = (
    _STANDARD,
    _OXFORD,
    _ADVOCATE,
    _SOCRATIC,
    _DELPHI,
    _BRAINSTORM,
    _TRADEOFF,
)

METHODS: dict[str, MethodSpec] = {spec.name: spec for spec in METHOD_ORDER}


def method_table() -> list[MethodSpec]:
    """按固定顺序返回全部方法（CLI 列表顺序即此顺序）。"""

    return list(METHOD_ORDER)


def get_method(name: str) -> MethodSpec:
    """按名称取方法；未知名称抛 ``KeyError`` 并列出所有可用名。"""

    key = (name or "").strip().lower()
    try:
        return METHODS[key]
    except KeyError:
        available = "、".join(spec.name for spec in METHOD_ORDER)
        raise KeyError(f"未知的讨论方法 {name!r}；可用方法：{available}") from None
