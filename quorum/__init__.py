"""AI Quorum —— 多模型圆桌讨论引擎。

核心流程：
    config.discover_models()  ->  选模型
    methods.get_method()      ->  生成讨论计划（Step 列表）
    engine.Engine.run()       ->  执行并产出 DiscussionResult
    report.write_report()     ->  落盘 markdown / text / json
"""

from .types import Agent, DiscussionResult, ModelSpec, Step, Utterance

__version__ = "0.1.0"
__all__ = ["Agent", "ModelSpec", "Step", "Utterance", "DiscussionResult", "__version__"]
