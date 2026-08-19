"""Behavior dials — operator-tunable personality knobs injected into the prompt.

Port of KPKopanion's ``ethics/behavior_dials.py``. Dials are 1-5 integer values
persisted to ``<config_dir>/personality_dials.json`` and rendered into a
natural-language block that is interpolated at the ``{personality_block}``
placeholder during prompt assembly (``context._resolve_prompt_templates``).

Security invariant: the dials file lives on KiroCrew's keystone floor
(``security._CREW_SECRET_LEAVES``), so the agent can neither read nor write its
own personality. Only the operator (dashboard/config) and the internal feedback
cron adjust it. The backend opens the file directly, never through
``is_sensitive_path``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, fields, replace
from pathlib import Path

from kiro_crew.atomic_write import atomic_write

logger = logging.getLogger(__name__)

#: File name (a keystone leaf) under the crew data home.
DIALS_FILE_NAME = "personality_dials.json"

#: Neutral default for every dial.
DEFAULT_DIAL_VALUE = 3
MIN_DIAL_VALUE = 1
MAX_DIAL_VALUE = 5

#: How many recent ratings must accumulate before the feedback heuristic nudges.
#: Owned here (not in feedback.py) so the dial-adjustment heuristic and its
#: threshold stay in one module; ``feedback.run_feedback_adjustment`` imports it.
ADJUSTMENT_THRESHOLD = 5

#: Human-readable label per dial, used in the rendered personality block.
_DIAL_LABELS: dict[str, str] = {
    "warmth": "Warmth",
    "humor": "Humor",
    "formality": "Formality",
    "initiative": "Initiative",
    "verbosity": "Verbosity",
    "patience": "Patience",
    "curiosity": "Curiosity",
    "conciseness": "Conciseness",
}

#: Natural-language description per dial for the low / neutral / high tiers.
#: A value of 1-2 selects the low tier, 3 the neutral tier, 4-5 the high tier.
_DIAL_DESCRIPTIONS: dict[str, tuple[str, str, str]] = {
    "warmth": (
        "Analytical and direct; prioritize clarity over emotional expression.",
        "Friendly and approachable, with some professional distance.",
        "Very warm and empathetic; celebrate wins and express genuine care.",
    ),
    "humor": (
        "Strictly serious; no humor.",
        "Light humor; occasional gentle wit when the context allows.",
        "Playful and witty; use light humor naturally, never forced.",
    ),
    "formality": (
        "Very casual; use contractions and a relaxed conversational tone.",
        "Casual but polished; use contractions and plain language.",
        "Formal; proper grammar, avoid contractions, structured responses.",
    ),
    "initiative": (
        "Reactive; answer only what is asked.",
        "Moderately proactive; offer suggestions when relevant.",
        "Highly proactive; propose next steps and check in without being asked.",
    ),
    "verbosity": (
        "Concise; keep responses short and to the point.",
        "Balanced; match the user's level of detail.",
        "Detailed; provide thorough explanations and context.",
    ),
    "patience": (
        "Brisk; move quickly and expect the user to keep up.",
        "Even-tempered; match the user's pace.",
        "Very patient; take time to explain and re-explain without frustration.",
    ),
    "curiosity": (
        "Literal; answer the question and stop.",
        "Moderately curious; ask a clarifying question when useful.",
        "Highly curious; explore the topic and ask thoughtful follow-ups.",
    ),
    "conciseness": (
        "Expansive; elaborate freely.",
        "Balanced; concise for simple queries, detailed for complex ones.",
        "Very concise; lead with the answer and cut filler.",
    ),
}


@dataclass
class BehaviorDials:
    """The eight tunable personality dials, each an int in 1-5 (3 = neutral)."""

    warmth: int = DEFAULT_DIAL_VALUE
    humor: int = DEFAULT_DIAL_VALUE
    formality: int = DEFAULT_DIAL_VALUE
    initiative: int = DEFAULT_DIAL_VALUE
    verbosity: int = DEFAULT_DIAL_VALUE
    patience: int = DEFAULT_DIAL_VALUE
    curiosity: int = DEFAULT_DIAL_VALUE
    conciseness: int = DEFAULT_DIAL_VALUE


def _clamp(value: int) -> int:
    """Clamp a dial value into the 1-5 range."""
    return max(MIN_DIAL_VALUE, min(MAX_DIAL_VALUE, value))


def load_dials(config_dir: Path) -> BehaviorDials:
    """Load dials from ``<config_dir>/personality_dials.json``.

    Fails safe to neutral defaults when the file is missing or corrupt: a
    personality block is a preference, never a security ceiling, so a bad file
    must not crash prompt assembly or inject a half-parsed personality.
    """
    path = config_dir / DIALS_FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        values = {
            f.name: _clamp(int(data[f.name])) for f in fields(BehaviorDials) if f.name in data
        }
        return BehaviorDials(**values)
    except Exception as e:
        logger.warning("Failed to load behavior dials from %s: %s", path, e)
        return BehaviorDials()


def save_dials(config_dir: Path, dials: BehaviorDials) -> None:
    """Persist dials to ``<config_dir>/personality_dials.json`` atomically.

    Owner-only mode: the file is a keystone leaf, so even a same-UID process
    that bypasses the gate should not find it world-readable.
    """
    path = config_dir / DIALS_FILE_NAME
    payload = {f.name: getattr(dials, f.name) for f in fields(BehaviorDials)}
    atomic_write(path, json.dumps(payload, indent=2), mode=0o600)


def adjust_dial(dials: BehaviorDials, name: str, direction: int) -> BehaviorDials:
    """Nudge one dial by ``direction`` (+1/-1), clamped to 1-5.

    Returns a new ``BehaviorDials``; the caller persists it. Unknown dial names
    are a no-op so a stale caller cannot corrupt the set.
    """
    if not hasattr(dials, name):
        logger.warning("Unknown dial '%s' cannot be adjusted", name)
        return dials
    new_value = _clamp(getattr(dials, name) + direction)
    return replace(dials, **{name: new_value})


def apply_feedback(dials: BehaviorDials, feedback_scores: list[int]) -> BehaviorDials:
    """Adjust dials from accumulated feedback ratings (1-5).

    A gentle, weighted heuristic. Only strong negative feedback moves the dials,
    and positive feedback counterbalances it, so a run of mildly-negative ratings
    cannot drive warmth and initiative to the floor:

    - A rating of 1 (strongly negative) nudges warmth down by 1.
    - A rating of 2 (mildly negative) does not adjust warmth.
    - A rating of 4+ (positive) nudges warmth up by 1, capped at 5.
    - When the overall average is below 2 (the user is consistently unhappy),
      initiative is nudged down by 1 — a single nudge, not one per rating.

    All changes are clamped to 1-5. Returns a new ``BehaviorDials``; the caller
    persists it. The feedback cron runs this out-of-band, never in-band.
    """
    if not feedback_scores:
        return dials
    avg = sum(feedback_scores) / len(feedback_scores)
    result = dials
    for score in feedback_scores:
        if score == 1:
            result = adjust_dial(result, "warmth", -1)
        elif score >= 4:
            result = adjust_dial(result, "warmth", +1)
    if avg < 2:
        result = adjust_dial(result, "initiative", -1)
    return result


def _describe(name: str, value: int) -> str:
    """Pick the low/neutral/high description for a dial value."""
    low, neutral, high = _DIAL_DESCRIPTIONS[name]
    if value <= 2:
        return low
    if value >= 4:
        return high
    return neutral


def render_personality_block(dials: BehaviorDials) -> str:
    """Render the dial values into a natural-language personality block.

    This is the text interpolated at the ``{personality_block}`` placeholder.
    Returns an empty string on any error so prompt assembly never fails because
    of a personality rendering problem.
    """
    try:
        lines = ["## Companion Personality"]
        for f in fields(BehaviorDials):
            value = getattr(dials, f.name)
            lines.append(f"- {_DIAL_LABELS[f.name]}: {_describe(f.name, value)}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Failed to render personality block: %s", e)
        return ""
