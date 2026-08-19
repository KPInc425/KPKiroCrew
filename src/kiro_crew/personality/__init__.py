"""Adaptive personality — behavior dials + feedback loop.

Public API re-exported for the rest of KiroCrew (prompt assembly, the
``personality_feedback`` MCP tool, and the feedback cron).
"""

from kiro_crew.personality.dials import (
    BehaviorDials,
    adjust_dial,
    apply_feedback,
    load_dials,
    render_personality_block,
    save_dials,
)
from kiro_crew.personality.feedback import FeedbackCollector, run_feedback_adjustment

__all__ = [
    "BehaviorDials",
    "FeedbackCollector",
    "adjust_dial",
    "apply_feedback",
    "load_dials",
    "render_personality_block",
    "run_feedback_adjustment",
    "save_dials",
]
