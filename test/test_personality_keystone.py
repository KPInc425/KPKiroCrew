"""Security tests: the adaptive-personality files are keystone-protected.

The dials and feedback files control the agent's own behavior, so they must sit
on the keystone floor: the agent can neither read nor write them, and shell
commands that touch them are blocked.
"""

from __future__ import annotations

from pathlib import Path

from kiro_crew.security import is_sensitive_bash_command, is_sensitive_path, is_sensitive_write_path


class TestPersonalityKeystone:
    def test_personality_dials_sensitive_path(self) -> None:
        assert is_sensitive_path("~/.kiro/crew/personality_dials.json") is True
        assert is_sensitive_path("~/.kirocrew/personality_dials.json") is True

    def test_personality_feedback_sensitive_path(self) -> None:
        assert is_sensitive_path("~/.kiro/crew/personality_feedback.json") is True
        assert is_sensitive_path("~/.kirocrew/personality_feedback.json") is True

    def test_personality_dials_write_protected(self) -> None:
        assert is_sensitive_write_path("~/.kiro/crew/personality_dials.json") is True
        assert is_sensitive_write_path("~/.kirocrew/personality_dials.json") is True

    def test_personality_dials_absolute_path(self) -> None:
        home = str(Path.home())
        assert is_sensitive_path(f"{home}/.kiro/crew/personality_dials.json") is True
        assert is_sensitive_path(f"{home}/.kirocrew/personality_dials.json") is True

    def test_personality_dials_bash_blocked(self) -> None:
        result = is_sensitive_bash_command("cat ~/.kiro/crew/personality_dials.json")
        assert result is not None and "blocked" in result.lower()
        legacy = is_sensitive_bash_command("cat ~/.kirocrew/personality_dials.json")
        assert legacy is not None and "blocked" in legacy.lower()

    def test_personality_feedback_bash_blocked(self) -> None:
        result = is_sensitive_bash_command("cat ~/.kiro/crew/personality_feedback.json")
        assert result is not None and "blocked" in result.lower()
