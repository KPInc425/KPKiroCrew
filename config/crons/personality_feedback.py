"""Cron script: run the adaptive-personality feedback adjustment.

This is a template for the Kiro Crew cron-script mechanism. Copy it to
``~/.kiro/crew/crons/personality_feedback.py`` and register a job with:

    script='~/.kiro/crew/crons/personality_feedback.py:run'

The job runs ``personality.feedback.run_feedback_adjustment`` out-of-band on a
schedule (e.g. daily), so the behavior dials are tuned from accumulated ratings
without ever adjusting them in-band during a conversation. The function is a
no-op when there is not enough (or not negative enough) feedback, so running it
frequently is harmless.
"""

from kiro_crew.config.paths import config_dir
from kiro_crew.personality.feedback import run_feedback_adjustment


def run(ctx) -> None:
    """Run the feedback adjustment against the live data home."""
    run_feedback_adjustment(config_dir())
