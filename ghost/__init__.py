"""GHOST — The Spectral Execution Layer for Autonomous Agents.

Ephemeral, scoped, signed execution for AI agents. Spawn a short-lived session,
route the agent's actions through an intercept that records cryptographic
residue, then evaporate — leaving a tamper-evident audit trail.
"""

from __future__ import annotations

__version__ = "0.1.1"
__author__ = "Timothy Walton (Script Master Labs LLC)"
__license__ = "MIT"

from .session import act, evaporate, replay, spawn  # noqa: E402
from .store import ResidueStore  # noqa: E402
from .proxy import GhostProxy, possess  # noqa: E402
from .gateway import GhostGateway, validate_gateway_token  # noqa: E402

__all__ = [
    "spawn",
    "act",
    "evaporate",
    "replay",
    "possess",
    "GhostProxy",
    "ResidueStore",
    "GhostGateway",
    "validate_gateway_token",
    "__version__",
]
