"""一个有边界、有连续记忆、会因状态而拒绝的实验性 Agent。"""

from .agent import BoundaryAgent, TurnResult
from .config import Config, band_of
from .debate import Verdict, debate
from .llm import MockLLM, OpenAICompatibleLLM
from .memory import Memory
from .rejection_log import RejectionLog
from .shadow import ShadowTrace

__all__ = [
    "BoundaryAgent", "TurnResult", "Config", "band_of",
    "Verdict", "debate", "Memory", "RejectionLog", "ShadowTrace",
    "OpenAICompatibleLLM", "MockLLM",
]
__version__ = "0.1.0"
