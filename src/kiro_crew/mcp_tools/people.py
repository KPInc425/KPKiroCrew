"""The people relationship graph and adaptive-personality tools.

``schemas()`` returns the ADVERTISEMENT half of each tool -- its name, the
model-facing description, and the JSON Schema a call is validated against.
``HANDLERS`` maps each of those names to the function that runs it. Both halves
of a tool live here so its contract and its behavior are read together, and
``test_mcp_tool_registry`` fails if one arrives without the other (the same
pattern as :mod:`kiro_crew.mcp_tools.learn`).

``people_add_fact`` and ``personality_feedback`` mutate durable state and
resolve the caller with ``mcp_core._resolve_session_key_strict`` so a subagent
under the parent's process tree cannot PID-walk into the parent's identity and
write facts or feedback on the parent's behalf. The read-only ``people_lookup``
and ``people_list`` need no verified identity.

Handlers reach this server's shared plumbing as attributes of ``mcp_core`` --
``mcp_core._get``, ``mcp_core._put``, the strict identity resolver, the
governance vet, ``config_dir``. An attribute lookup resolves at CALL time, so a
test that rebinds one on the module still intercepts the handler.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from kiro_crew import mcp_core


def schemas() -> list[dict[str, Any]]:
    """Descriptors for the people-memory and personality tools."""
    return [
        {
            "name": "people_add_fact",
            "description": (
                "Store a structured fact about a person in long-term semantic "
                "memory under the people.* prefix. Use when the user tells you "
                "something about someone (a name, birthday, preference, or a "
                "relationship to another person). Relationships are stored as a "
                "JSON object under the 'relationships' attribute, e.g. "
                '{"bob": "coworker"}. Names are lowercase alphanumeric '
                "(underscores allowed). Ask before remembering a new person."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Person's name, lowercase alphanumeric (underscores allowed)",
                    },
                    "attribute": {
                        "type": "string",
                        "description": "Fact attribute, e.g. name, birthday, relationship, aliases",
                    },
                    "value": {
                        "type": ["string", "number", "boolean", "object", "array", "null"],
                        "description": "The fact value: a string, number, or JSON object",
                    },
                },
                "required": ["name", "attribute", "value"],
            },
        },
        {
            "name": "people_lookup",
            "description": (
                "Retrieve everything known about a person (their dossier) from "
                "semantic memory. Call when a person is mentioned in the "
                "conversation. Returns all people.<name>.* facts. To resolve an "
                "alias, read the 'aliases' attribute and look up each alias as a "
                "name too."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Person's name, lowercase alphanumeric (underscores allowed)",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "people_list",
            "description": "List all people currently known in semantic memory.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "personality_feedback",
            "description": (
                "Record user feedback on whether the last response was helpful. "
                "Ask the user 'was that helpful?' and use this tool with their "
                "rating (1=not helpful, 5=very helpful). Only ask occasionally, "
                "not every turn."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "rating": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                        "description": "User rating: 1=not helpful, 5=very helpful",
                    },
                    "note": {
                        "type": "string",
                        "description": "Optional free-text reason for the rating",
                    },
                },
                "required": ["rating"],
            },
        },
    ]


def people_add_fact(name: str, args: dict[str, Any]) -> str:
    person = str(args["name"]).strip().lower()
    attribute = str(args["attribute"]).strip().lower()
    value = args["value"]
    # STRICT resolution (env-var only, no PID walk): writing a durable people
    # fact mutates persistent semantic memory, and a subagent under the parent's
    # process tree must not be able to PID-walk into the parent's identity and
    # write facts on the parent's behalf.
    sk = mcp_core._resolve_session_key_strict()
    if not sk:
        return "Error: people_add_fact requires a verified session identity"
    # A durable memory write is re-injected into every future session's context,
    # so it is gated the same way learn_add is (capabilities.memory_writes,
    # default on; a policy/profile may disable it for a sandboxed surface/app).
    gov = mcp_core._vet_memory_writes_governance(sk)
    if gov:
        return f"Error: {gov}"
    key = f"people.{person}.{attribute}"
    d = mcp_core._put(
        "/api/memory/semantic",
        {"key": key, "value": value, "confidence": 1.0, "source": "user_explicit"},
        session_key=sk,
    )
    if d.get("error"):
        return f"Error: {d['error']}"
    return f"Saved people fact: {key}"


def people_lookup(name: str, args: dict[str, Any]) -> str:
    person = str(args["name"]).strip().lower()
    d = mcp_core._get("/api/memory/semantic?limit=1000")
    err_val = d.get("error")
    if err_val:
        return f"Error: {err_val}"
    prefix = f"people.{person}."
    facts = []
    for entry in d.get("entries", []):
        key = entry.get("key", "")
        if not key.startswith(prefix):
            continue
        try:
            val = json.loads(entry.get("value_json", "null"))
        except (TypeError, ValueError):
            val = entry.get("value_json")
        facts.append(f"{key} = {json.dumps(val)}")
    if not facts:
        return f"No facts known about '{person}'."
    return "Dossier for " + person + ":\n" + "\n".join(facts)


def people_list(name: str, args: dict[str, Any]) -> str:
    d = mcp_core._get("/api/memory/semantic?limit=1000")
    err_val = d.get("error")
    if err_val:
        return f"Error: {err_val}"
    names = set()
    for entry in d.get("entries", []):
        key = entry.get("key", "")
        if key.startswith("people."):
            person = key[len("people.") :].split(".", 1)[0]
            if person:
                names.add(person)
    if not names:
        return "No people are known yet."
    return "Known people:\n" + "\n".join(sorted(names))


def personality_feedback(name: str, args: dict[str, Any]) -> str:
    # STRICT resolution (env-var only, no PID walk): recording feedback mutates
    # the session's persistent feedback store, and a subagent under the parent's
    # process tree must not be able to PID-walk into the parent's identity and
    # record feedback on the parent's behalf.
    sk = mcp_core._resolve_session_key_strict()
    if not sk:
        return "Error: personality_feedback requires a verified session identity"
    rating = int(args["rating"])
    note = args.get("note", "")
    try:
        from kiro_crew.personality import FeedbackCollector

        collector = FeedbackCollector(mcp_core.config_dir())
        # The MCP worker thread has no running event loop, so asyncio.run is
        # safe here -- the same sync->async bridge the codebase uses in worker
        # threads (e.g. SessionAgentRunner.run).
        asyncio.run(collector.record_feedback(sk, rating, note))
    except Exception as e:
        return f"Error: failed to record feedback: {e}"
    return f"Feedback recorded (rating {rating}/5) for session {sk!r}."


HANDLERS: dict[str, Callable[[str, dict[str, Any]], str]] = {
    "people_add_fact": people_add_fact,
    "people_lookup": people_lookup,
    "people_list": people_list,
    "personality_feedback": personality_feedback,
}
