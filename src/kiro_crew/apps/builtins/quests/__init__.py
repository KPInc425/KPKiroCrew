"""Quests — gamification builtin app.

A quest/XP/achievement system ported from KPKopanion's ``adventure/`` and
``gamification/`` modules. Quests are generated from goals and habits, and
completing them awards XP that drives a level/title progression. Achievements
are unlocked milestones.

Architecture:

* ``models.py`` — the data model (``Quest``, ``QuestObjective``, ``XPTotal``,
  ``Achievement``) and the XP/level constants.
* ``store.py`` — ``QuestStore``, async JSON persistence under the app data dir
  with atomic writes.
* ``engine.py`` — ``QuestEngine``, quest generation and completion (awards XP).
* ``backend/routes.py`` — browser-facing aiohttp routes under
  ``/api/apps/quests/*``, deny-by-default.
* ``mcp_server.py`` — the app's own stdio MCP server (``kirocrew app mcp
  quests``), with strict session identity for mutating tools.

The app is opt-in (``defaultEnabled: false``) and single-user: KPKopanion's
``network_id`` dimension is dropped.
"""

# Required re-export: dashboard/server.py's startup route registration imports
# the PACKAGE and checks hasattr(_mod, "register_routes") — same convention as
# mochi/__init__.py.
from kiro_crew.apps.builtins.quests.backend.routes import register_routes  # noqa: F401,E402
