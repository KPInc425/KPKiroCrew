"""Token authentication for the Windows helper MCP server.

The shared-secret token is the only thing standing between the WSL2 sandbox
(which is untrusted by design) and full Windows access. We compare tokens in
constant time so an attacker cannot use response timing to guess the secret
byte by byte.
"""

from __future__ import annotations

import hmac

# The Authorization header prefix we accept. Kept as a module constant so the
# exact wire format is defined in one place.
BEARER_PREFIX = "Bearer "


def token_matches(expected: str, provided: str) -> bool:
    """Return True only when ``provided`` equals ``expected``.

    Both values are compared in constant time via ``hmac.compare_digest``.
    Empty values never match, so a missing token or a blank config token both
    fail closed.
    """
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def extract_bearer(authorization: str) -> str:
    """Pull the token out of an ``Authorization`` header value.

    Returns an empty string when the header is missing or is not a Bearer
    token, so callers can treat the result as "no token supplied".
    """
    if not authorization or not authorization.startswith(BEARER_PREFIX):
        return ""
    return authorization[len(BEARER_PREFIX):].strip()
