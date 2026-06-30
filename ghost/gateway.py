"""ghost/gateway.py — Enforcement layer for GHOST (v0.2.0).

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

v0.2.0 additions:
  - X-402-Proof-Receipt header on every successful proxied response.
    Contains a base64-encoded JSON receipt agents can use to prove delivery.
    Fields: receipt_id, session_id, path, upstream_status, delivered_at,
            response_bytes, gateway_version.
  - X-402-Cache-Status header: MISS on live upstream hits.
  - Idempotency: X-Ghost-Idempotency-Key deduplication within session scope.

Usage (CLI wired in cli.py):
    ghost serve --upstream https://your-api.example.com --port 7391
    ghost serve --upstream https://api.stripe.com --upstream-key sk_live_... --port 7391

The agent's HTTP client must:
    - Target  http://localhost:7391  (or wherever ghost serve listens)
    - Send    X-Ghost-Token: ghtok_<hex>
    - Send    X-Ghost-Session: gh_<hex>   (optional; gateway resolves from token)
    - Send    X-Ghost-Idempotency-Key: <unique-key>   (optional; prevents duplicate upstream calls)
    - Omit    Authorization header (gateway injects the upstream key if --upstream-key set)
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import ipaddress
import json
import logging
import os
import socket
import struct
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
_GHOST_INTERNAL_SECRET = os.environ.get("GHOST_INTERNAL_SECRET", "")
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


# ─── 402Proof receipt ─────────────────────────────────────────────────────────

_GATEWAY_VERSION = "ghost-layer/0.2.0"


def build_proof_receipt(
    session_id: str,
    path: str,
    upstream_status: int,
    response_bytes: int,
) -> str:
    """Build a base64-encoded JSON 402Proof receipt for a delivered response.

    The receipt is deterministic for the same (session_id, path, delivered_at
    truncated to minute) so agents can detect duplicates without storing state.

    Fields:
        receipt_id       — sha256(session_id + path + delivered_at_minute)[:16]
        session_id       — Ghost Layer session identifier
        path             — request path forwarded to upstream
        upstream_status  — HTTP status returned by upstream
        delivered_at     — ISO 8601 UTC timestamp
        response_bytes   — byte length of the upstream response body
        gateway_version  — ghost-layer version string
        delivery_status  — "delivered" | "upstream_error"
    """
    delivered_at = _now().isoformat()
    delivered_minute = delivered_at[:16]  # YYYY-MM-DDTHH:MM

    receipt_input = f"{session_id}{path}{delivered_minute}".encode()
    receipt_id = hashlib.sha256(receipt_input).hexdigest()[:16]

    delivery_status = "delivered" if 200 <= upstream_status < 300 else "upstream_error"

    receipt: dict[str, Any] = {
        "receipt_id": receipt_id,
        "session_id": session_id,
        "path": path,
        "upstream_status": upstream_status,
        "delivered_at": delivered_at,
        "response_bytes": response_bytes,
        "gateway_version": _GATEWAY_VERSION,
        "delivery_status": delivery_status,
    }

    return base64.b64encode(json.dumps(receipt, separators=(",", ":")).encode()).decode()


# ─── In-process idempotency cache (session-scoped) ───────────────────────────

class _IdempotencyCache:
    """Thread-safe LRU-like cache for idempotency key → cached response.

    Scope: per-gateway-process. Entries expire after ttl_seconds.
    Key: sha256(session_id + idempotency_key) so keys are session-scoped.
    """

    _TTL_SECONDS = 300

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _make_key(self, session_id: str, idempotency_key: str) -> str:
        raw = f"{session_id}:{idempotency_key}".encode()
        return hashlib.sha256(raw).hexdigest()

    def get(self, session_id: str, idempotency_key: str) -> Optional[dict[str, Any]]:
        key = self._make_key(session_id, idempotency_key)
        with self._lock:
            record = self._store.get(key)
            if record is None:
                return None
            age = (_now() - record["cached_at"]).total_seconds()
            if age > self._TTL_SECONDS:
                del self._store[key]
                return None
            return record

    def set(
        self,
        session_id: str,
        idempotency_key: str,
        status: int,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        key = self._make_key(session_id, idempotency_key)
        with self._lock:
            self._store[key] = {
                "cached_at": _now(),
                "status": status,
                "body": body,
                "headers": headers,
            }


_idempotency_cache = _IdempotencyCache()


# ─── Ghost Cube WebSocket broadcaster ────────────────────────────────────────
# Clients connect via GET /ws/cube (Upgrade: websocket).
# The gateway broadcasts a JSON event after every proxied request.
# Uses raw WebSocket framing — no external library required.

_ws_clients: dict[str, Any] = {}   # conn_id → wfile
_ws_lock = threading.Lock()


def _ws_send_text(wfile: Any, text: str) -> None:
    """Encode and write a single WebSocket text frame (opcode 0x01)."""
    payload = text.encode("utf-8")
    length = len(payload)
    header = bytearray([0x81])  # FIN=1, opcode=text
    if length < 126:
        header.append(length)
    elif length < 65_536:
        header.append(126)
        header.extend(struct.pack(">H", length))
    else:
        header.append(127)
        header.extend(struct.pack(">Q", length))
    wfile.write(bytes(header) + payload)
    wfile.flush()


def broadcast_cube_event(event: dict[str, Any]) -> None:
    """Broadcast a JSON event to all connected Ghost Cube WebSocket clients.

    Called after every successful proxied upstream request. Dead connections
    are removed from the registry on first failed write.
    """
    text = json.dumps(event, separators=(",", ":"))
    dead: list[str] = []
    with _ws_lock:
        for conn_id, wfile in list(_ws_clients.items()):
            try:
                _ws_send_text(wfile, text)
            except Exception:  # noqa: BLE001
                dead.append(conn_id)
        for conn_id in dead:
            _ws_clients.pop(conn_id, None)


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

    def _handle_ws_cube(self) -> bool:
        """Handle WebSocket upgrade for GET /ws/cube.

        Returns True if the request was a WebSocket upgrade (handled or rejected),
        False if the caller should continue with normal HTTP processing.

        No authentication required — the cube dashboard is a read-only live feed.
        Clients receive broadcast JSON events for every upstream proxied request.
        """
        if self.path != "/ws/cube" or self.command != "GET":
            return False
        if self.headers.get("Upgrade", "").lower() != "websocket":
            return False

        ws_key = self.headers.get("Sec-WebSocket-Key", "")
        if not ws_key:
            self._send_json(400, {"error": "missing Sec-WebSocket-Key"})
            return True

        # WebSocket handshake (RFC 6455 §4.2.2)
        magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        accept = base64.b64encode(
            hashlib.sha1((ws_key + magic).encode(), usedforsecurity=False).digest()
        ).decode()

        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()

        conn_id = os.urandom(4).hex()
        with _ws_lock:
            _ws_clients[conn_id] = self.wfile

        # Send a welcome frame so the client knows it's live
        try:
            _ws_send_text(self.wfile, json.dumps({
                "type": "connected",
                "gateway_version": _GATEWAY_VERSION,
                "message": "Ghost Cube live feed active",
            }, separators=(",", ":")))
        except Exception:  # noqa: BLE001
            pass

        # Block reading frames from client (ping/close/text all handled here).
        # We only need to keep the connection alive; data flows server→client.
        try:
            while True:
                try:
                    header = self.rfile.read(2)
                    if not header or len(header) < 2:
                        break
                    opcode = header[0] & 0x0F
                    masked = bool(header[1] & 0x80)
                    length = header[1] & 0x7F
                    if length == 126:
                        length = struct.unpack(">H", self.rfile.read(2))[0]
                    elif length == 127:
                        length = struct.unpack(">Q", self.rfile.read(8))[0]
                    mask_key = self.rfile.read(4) if masked else b""
                    payload = self.rfile.read(length)
                    if masked:
                        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
                    if opcode == 0x8:   # close
                        # Echo close frame and exit
                        self.wfile.write(b"\x88\x00")
                        self.wfile.flush()
                        break
                    # pings (0x9) and client text/binary frames are ignored
                except Exception:  # noqa: BLE001
                    break
        finally:
            with _ws_lock:
                _ws_clients.pop(conn_id, None)

        return True

    def _handle_internal_broadcast(self) -> None:
        """POST /internal/broadcast — receive a JSON event from mcp-x402-xrpl
        (e.g. after a Xahau score anchor) and fan it out to all Ghost Cube
        WebSocket clients. Protected by GHOST_INTERNAL_SECRET if set."""
        if _GHOST_INTERNAL_SECRET:
            provided = self.headers.get("X-Internal-Secret", "")
            if provided != _GHOST_INTERNAL_SECRET:
                self._send_json(403, {"error": "forbidden", "detail": "Invalid X-Internal-Secret"})
                return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            import json as _json
            event = _json.loads(raw)
        except Exception:
            self._send_json(400, {"error": "invalid_json"})
            return

        broadcast_cube_event(event)
        self._send_json(200, {"ok": True, "clients_notified": len(_ws_clients)})

    def _handle(self) -> None:
        # Ghost Cube WebSocket — unauthenticated live feed
        if self._handle_ws_cube():
            return

        # Internal broadcast — POST /internal/broadcast from mcp-x402-xrpl after Xahau anchor
        if self.path == "/internal/broadcast" and self.command == "POST":
            self._handle_internal_broadcast()
            return

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

            session_id: str = session_info["session_id"]

            # ── Idempotency check ─────────────────────────────────────────
            idempotency_key: Optional[str] = self.headers.get("X-Ghost-Idempotency-Key")
            if idempotency_key:
                cached = _idempotency_cache.get(session_id, idempotency_key)
                if cached is not None:
                    self.send_response(cached["status"])
                    for k, v in cached["headers"].items():
                        self.send_header(k, v)
                    self.send_header("X-Ghost-Idempotency-Replayed", "true")
                    self._add_security_headers()
                    self.end_headers()
                    self.wfile.write(cached["body"])
                    return

            # ── Build forwarded headers — strip ghost + caller auth ───────
            _blocked = _BLOCKED_REQUEST_HEADERS | {"x-ghost-idempotency-key"}
            fwd_headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in _blocked
            }
            fwd_headers["X-Ghost-Session"] = session_id
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
                with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
                    resp_body = resp.read()

                    # Build 402Proof receipt for successful deliveries
                    receipt_b64 = build_proof_receipt(
                        session_id=session_id,
                        path=self.path,
                        upstream_status=resp.status,
                        response_bytes=len(resp_body),
                    )

                    # Collect response headers for idempotency cache
                    resp_headers: dict[str, str] = {}

                    self.send_response(resp.status)
                    for k, v in resp.headers.items():
                        if k.lower() not in {"transfer-encoding", "connection"}:
                            self.send_header(k, v)
                            resp_headers[k] = v
                    if vapl_enabled and 200 <= resp.status < 300:
                        from .vapl.middleware import emit_vapl_headers
                        for k, v in emit_vapl_headers(session_info, self.path, resp.status).items():
                            self.send_header(k, v)
                            resp_headers[k] = v
                    # Emit 402Proof receipt and cache metadata headers
                    self.send_header("X-402-Proof-Receipt", receipt_b64)
                    self.send_header("X-402-Cache-Status", "MISS")
                    self.send_header("X-Ghost-Gateway-Version", _GATEWAY_VERSION)
                    self._add_security_headers()
                    self.end_headers()
                    self.wfile.write(resp_body)

                    # Broadcast live event to Ghost Cube WebSocket clients
                    broadcast_cube_event({
                        "type": "request",
                        "path": self.path,
                        "method": self.command,
                        "upstream_status": resp.status,
                        "response_bytes": len(resp_body),
                        "receipt_id": json.loads(base64.b64decode(receipt_b64))["receipt_id"],
                        "delivered_at": _now().isoformat(),
                        "gateway_version": _GATEWAY_VERSION,
                    })

                    # Cache for idempotency replay
                    if idempotency_key and 200 <= resp.status < 300:
                        resp_headers["X-402-Proof-Receipt"] = receipt_b64
                        resp_headers["X-402-Cache-Status"] = "HIT"
                        resp_headers["X-Ghost-Gateway-Version"] = _GATEWAY_VERSION
                        _idempotency_cache.set(
                            session_id, idempotency_key,
                            status=resp.status,
                            body=resp_body,
                            headers=resp_headers,
                        )

            except urllib.error.HTTPError as exc:
                resp_body = exc.read()
                receipt_b64 = build_proof_receipt(
                    session_id=session_id,
                    path=self.path,
                    upstream_status=exc.code,
                    response_bytes=len(resp_body),
                )
                self.send_response(exc.code)
                self.send_header("X-402-Proof-Receipt", receipt_b64)
                self.send_header("X-402-Cache-Status", "MISS")
                self._add_security_headers()
                self.end_headers()
                self.wfile.write(resp_body)
            except Exception as exc:  # noqa: BLE001
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
