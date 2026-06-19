"""Agent-side intercept for GHOST.

`possess()` returns a callable proxy that wraps an HTTP transport. Every mutating
call the agent makes is mapped to a ghost.act() residue entry before the real
call is dispatched, and the ephemeral session token is injected while any caller
credentials are stripped. This module has no hard dependency on `requests`; the
transport is injected, which keeps it unit-testable and SDK-agnostic.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from . import session as _session
from .store import ResidueStore

__all__ = ["GhostProxy", "possess"]

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class GhostProxy:
    """Intercepts HTTP calls, records signed residue, then dispatches.

    transport: a callable (method, url, **kwargs) -> response-like object with
    at least a `.status_code` attribute and optional `.json()` method. Inject a
    real adapter (requests/httpx) in production; inject a fake in tests.
    """

    def __init__(
        self,
        store: ResidueStore,
        session_id: str,
        transport: Callable[..., Any],
        token: str,
        enforce_scope: bool = True,
    ) -> None:
        self.store = store
        self.session_id = session_id
        self.transport = transport
        self.token = token
        self.enforce_scope = enforce_scope

    def request(
        self,
        method: str,
        url: str,
        tool: str,
        action: str,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs: Any,
    ) -> Any:
        method = method.upper()
        headers = dict(headers or {})

        # Strip any caller-supplied auth; inject the ephemeral ghost token.
        for h in list(headers):
            if h.lower() in {"authorization", "x-api-key", "api-key"}:
                headers.pop(h)
        headers["X-Ghost-Token"] = self.token
        headers["X-Ghost-Session"] = self.session_id

        # Reads are dispatched without a residue entry unless mutating.
        if method not in MUTATING_METHODS:
            return self.transport(method, url, headers=headers, **kwargs)

        # Record intent BEFORE dispatch so a crash still leaves a trace.
        record = _session.act(
            self.store,
            self.session_id,
            tool=tool,
            action=action,
            params=params or {"url": url, "method": method},
            response=None,
            http_status=0,
            enforce_scope=self.enforce_scope,
        )

        resp = self.transport(method, url, headers=headers, **kwargs)
        status = getattr(resp, "status_code", 0)

        # Post-dispatch we record the response as a follow-on signed entry.
        body: Optional[dict[str, Any]] = None
        json_fn = getattr(resp, "json", None)
        if callable(json_fn):
            try:
                body = json_fn()
            except Exception:
                body = None
        _session.act(
            self.store,
            self.session_id,
            tool=tool,
            action=f"{action}:response",
            params={"action_id": record["action_id"]},
            response=body if isinstance(body, dict) else {"status": status},
            http_status=status,
            enforce_scope=False,  # response logging is never scope-blocked
        )
        return resp


def possess(
    store: ResidueStore,
    session_id: str,
    transport: Callable[..., Any],
    token: str,
    enforce_scope: bool = True,
) -> GhostProxy:
    return GhostProxy(store, session_id, transport, token, enforce_scope)
