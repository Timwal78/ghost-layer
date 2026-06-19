"""ghost/gateway.py — Enforcement layer for GHOST (v0.1.1).

Runs a reverse-proxy / egress-broker that enforces token liveness.
The agent sends requests to the gateway carrying X-Ghost-Token. The
gateway validates the token against the residue store (live, not
evaporated, not TTL-expired), then forwards the request upstream —
either to a local service (sidecar mode) or to a third-party API
while injecting the *real* upstream credential (broker mode).

When evaporate() is called, token_hash is cleared from the DB.
Any subsequent gateway request with that token gets HTTP 401 —
even if the caller cached the token. This is the upstream-enforcement
gap closed by v0.1.1.

Usage (CLI wired in cli.py):
    ghost serve --upstream https://your-api.example.com --port 7391
    ghost serve --upstream https://api.stripe.com --upstream-key sk_live_... --port 7391

The agent's HTTP client must:
    - Target  http://localhost:7391  (or wherever ghost serve listens)
    - Send    X-Ghost-Token: ghtok_<hex>
    - Send    X-Ghost-Session: gh_<hex>   (optional; gateway resolves from token)
    - Omit    Authorization header (gateway injects the upstream key if --upstream-key set)
"""

from __future__ import annotations

import http.server
import ipaddress
import json
import logging
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from .session import ExpiredSessionError, _parse_iso
from .store import ResidueStore

log = logging.getLogger("ghost.gateway")

_GHOST_TOKEN_HEADER = "X-Ghost-Token"
_GHOST_SESSION_HEADER = "X-Ghost-Session"
_BLOCKED_REQUEST_HEADERS = {"authorization", "x-ghost-token", "x-ghost-session"}

# Private / link-local / loopback / reserved networks that must never be
# reachable from the proxy (SSRF protection).
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("10.0.0.0/8"),        # RFC-1918
    ipaddress.ip_network("172.16.0.0/12"),     # RFC-1918
    ipaddress.ip_network("192.168.0.0/16"),    # RFC-1918
    ipaddress.ip_network("169.254.0.0/16"),    # link-local / AWS metadata
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
    ipaddress.ip_network("0.0.0.0/8"),         # "this" network
]

# HTTP response headers applied to every response this server sends.
_SECURITY_HEADERS: dict[str, str] = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _is_private_address(host: str) -> bool:
    """Return True if *host* resolves to a private/reserved IP (SSRF guard)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        # Unresolvable host — treat as safe to let the downstream error naturally.
        return False
    for *_, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if any(ip in net for net in _PRIVATE_NETWORKS):
            return True
    return False


def validate_upstream_url(url: str) -> str:
    """Validate that *url* is a safe upstream target.

    Raises ValueError with a human-readable message if the URL is rejected.
    Returns the normalised URL on success.
    """
    if not url:
        raise ValueError("upstream URL must not be empty")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"upstream URL scheme must be http or https, got {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ValueError("upstream URL must include a hostname")
    if _is_private_address(host):
        raise ValueError(
            f"upstream URL {url!r} resolves to a private/internal address — "
            "SSRF protection blocks this target"
        )
    return url


class GatewayTokenError(Exception):
    """Raised when a gateway request carries a missing, invalid, or evaporated token."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_gateway_token(store: ResidueStore, raw_token: str) -> dict[str, Any]:
    """Validate an inbound X-Ghost-Token and return session info.

    Raises GatewayTokenError on any failure so the gateway can return 401.
    Raises ExpiredSessionError if the session's TTL has passed.
    """
    if not raw_token or not raw_token.startswith("ghtok_"):
        raise GatewayTokenError("missing or malformed X-Ghost-Token")

    row = store.validate_token(raw_token)
    if row is None:
        raise GatewayTokenError("token not found or session already evaporated")

    # Check TTL expiry (evaporated_at already filtered by validate_token query)
    if _now() > _parse_iso(row["expires_at"]):
        raise ExpiredSessionError(
            f"session {row['session_id']} TTL expired at {row['expires_at']}"
        )

    return {
        "session_id": row["session_id"],
        "intent": row["intent"],
        "scopes": [s for s in (row["scopes"] or "").split(",") if s],
        "expires_at": row["expires_at"],
    }


class _GhostRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that validates the ghost token then proxies to upstream."""

    # Injected by GhostGateway before serving
    store: ResidueStore
    upstream_url: str
    upstream_key: Optional[str]
    log_requests: bool

    def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[override]
        if self.server.log_requests:  # type: ignore[attr-defined]
            super().log_message(fmt, *args)

    def _add_security_headers(self) -> None:
        """Emit HTTP security headers on every response from this gateway."""
        for k, v in _SECURITY_HEADERS.items():
            self.send_header(k, v)

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _serve_vapl_manifest(self) -> None:
        try:
            from .vapl.middleware import build_vapl_manifest
            payload = json.dumps(build_vapl_manifest(), indent=2).encode()
        except Exception as exc:  # noqa: BLE001
            log.debug("[VAPL] manifest error: %s", exc)
            payload = json.dumps({"error": "manifest unavailable"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Content-Length", str(len(payload)))
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _handle(self) -> None:
        # VAPL discovery — unauthenticated, no token required
        if self.path == "/.well-known/vapl.json":
            self._serve_vapl_manifest()
            return

        raw_token = self.headers.get(_GHOST_TOKEN_HEADER, "")
        # Open a fresh connection in this handler thread (SQLite is not thread-safe).
        db_path = self.server.db_path  # type: ignore[attr-defined]
        store = ResidueStore(db_path)
        try:
            try:
                session_info = validate_gateway_token(store, raw_token)
            except GatewayTokenError as exc:
                self._send_json(401, {"error": "unauthorized", "detail": str(exc)})
                return
            except ExpiredSessionError as exc:
                self._send_json(401, {"error": "session_expired", "detail": str(exc)})
                return

            # Build forwarded headers — strip ghost + caller auth, inject upstream key
            fwd_headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in _BLOCKED_REQUEST_HEADERS
            }
            fwd_headers["X-Ghost-Session"] = session_info["session_id"]
            fwd_headers["X-Ghost-Scopes"] = ",".join(session_info["scopes"])

            upstream_key: Optional[str] = self.server.upstream_key  # type: ignore[attr-defined]
            if upstream_key:
                fwd_headers["Authorization"] = f"Bearer {upstream_key}"

            # Read request body
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None

            # Forward to upstream
            target = self.server.upstream_url.rstrip("/") + self.path  # type: ignore[attr-defined]
            req = urllib.request.Request(
                target,
                data=body,
                headers=fwd_headers,
                method=self.command,
            )
            vapl_enabled: bool = getattr(self.server, "vapl_enabled", False)  # type: ignore[attr-defined]
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 — scheme validated by validate_upstream_url()
                    resp_body = resp.read()
                    self.send_response(resp.status)
                    for k, v in resp.headers.items():
                        if k.lower() not in {"transfer-encoding", "connection"}:
                            self.send_header(k, v)
                    if vapl_enabled and 200 <= resp.status < 300:
                        from .vapl.middleware import emit_vapl_headers
                        for k, v in emit_vapl_headers(session_info, self.path, resp.status).items():
                            self.send_header(k, v)
                    self._add_security_headers()
                    self.end_headers()
                    self.wfile.write(resp_body)
            except urllib.error.HTTPError as exc:
                resp_body = exc.read()
                self.send_response(exc.code)
                self._add_security_headers()
                self.end_headers()
                self.wfile.write(resp_body)
            except Exception as exc:  # noqa: BLE001
                # Log internally but do not leak upstream topology to callers.
                log.warning("upstream request failed: %s", exc)
                self._send_json(502, {"error": "upstream_error", "detail": "upstream request failed"})
        finally:
            store.close()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle


class GhostGateway:
    """Threaded HTTP gateway that enforces ghost token liveness.

    Args:
        store:        ResidueStore connected to the GHOST DB.
        upstream_url: Base URL to forward requests to.
        upstream_key: Optional real API key to inject as Authorization (broker mode).
        port:         Port to listen on (default 7391).
        host:         Bind address (default 127.0.0.1 — localhost only).
        log_requests: Whether to print request logs (default False).

    Example — sidecar mode (your own service):
        gw = GhostGateway(store, upstream_url="http://localhost:8080")
        gw.start()
        # agent calls http://localhost:7391/... with X-Ghost-Token

    Example — broker mode (third-party API, real key never reaches agent):
        gw = GhostGateway(
            store,
            upstream_url="https://api.stripe.com",
            upstream_key="sk_live_...",
        )
        gw.start()
        # evaporate() → gateway rejects token with 401 even for Stripe
    """

    def __init__(
        self,
        store: ResidueStore,
        upstream_url: str,
        upstream_key: Optional[str] = None,
        port: int = 7391,
        host: str = "127.0.0.1",
        log_requests: bool = False,
        vapl_enabled: bool = True,
        validate_ssrf: bool = True,
    ) -> None:
        # Validate upstream URL at construction time to catch SSRF targets early.
        # Tests that use localhost upstreams can pass validate_ssrf=False.
        if validate_ssrf:
            validate_upstream_url(upstream_url)
        self.store = store
        self.upstream_url = upstream_url
        self.upstream_key = upstream_key
        self.port = port
        self.host = host
        self.log_requests = log_requests
        self.vapl_enabled = vapl_enabled
        self._server: Optional[http.server.HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def _serve_forever_safe(server: http.server.HTTPServer) -> None:
        """Wrap serve_forever with top-level exception logging so the thread death is visible."""
        try:
            server.serve_forever()
        except Exception as exc:  # noqa: BLE001
            log.critical("GhostGateway server thread crashed: %s", exc, exc_info=True)

    def start(self) -> None:
        """Start the gateway in a background thread."""
        server = http.server.HTTPServer((self.host, self.port), _GhostRequestHandler)
        server.db_path = self.store.db_path  # type: ignore[attr-defined]  # per-request connections
        server.upstream_url = self.upstream_url  # type: ignore[attr-defined]
        server.upstream_key = self.upstream_key  # type: ignore[attr-defined]
        server.log_requests = self.log_requests  # type: ignore[attr-defined]
        server.vapl_enabled = self.vapl_enabled  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(
            target=self._serve_forever_safe,
            args=(server,),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Shut down the gateway."""
        if self._server:
            self._server.shutdown()
            self._server = None

    def __enter__(self) -> "GhostGateway":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()


__all__ = [
    "GhostGateway",
    "GatewayTokenError",
    "validate_gateway_token",
    "validate_upstream_url",
]
