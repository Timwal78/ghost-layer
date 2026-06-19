"""Session lifecycle for GHOST.

Pure logic layer (no CLI, no I/O formatting) so it is unit-testable. Ephemeral
private keys live on disk under GHOST_HOME/sessions/<id>/ for the lifetime of
the session and are shredded at evaporate.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from . import crypto
from .store import GHOST_HOME, ResidueStore

__all__ = [
    "spawn",
    "act",
    "evaporate",
    "replay",
    "SessionError",
    "ExpiredSessionError",
    "ScopeError",
]

SESSIONS_DIR = GHOST_HOME / "sessions"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _session_dir(session_id: str) -> Path:
    return SESSIONS_DIR / session_id


def _fingerprint(public_raw: bytes) -> str:
    return crypto.sha256_hex(crypto.b64(public_raw))[:16]


class SessionError(Exception):
    pass


class ExpiredSessionError(SessionError):
    pass


class ScopeError(SessionError):
    pass


def spawn(
    store: ResidueStore,
    intent: str,
    ttl: int = 300,
    scopes: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Create an ephemeral session and persist a session record."""
    scopes = scopes or []
    session_id = "gh_" + secrets.token_hex(16)

    private_seed, public_raw = crypto.generate_keypair()
    sdir = _session_dir(session_id)
    sdir.mkdir(parents=True, exist_ok=True)
    key_path = sdir / "ed25519_seed"
    # 0600 so other users on the box cannot read the ephemeral key
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(private_seed)

    spawned = _now()
    expires = spawned + timedelta(seconds=ttl)
    public_b64 = crypto.b64(public_raw)

    store.insert_session(
        {
            "session_id": session_id,
            "intent": intent,
            "scopes": ",".join(scopes),
            "public_key": public_b64,
            "spawned_at": _iso(spawned),
            "ttl_seconds": ttl,
            "expires_at": _iso(expires),
        }
    )
    store.log_credential(session_id, _fingerprint(public_raw), "spawn", _iso(spawned))

    # Mint an opaque bearer token for gateway enforcement.
    # Only the SHA-256 hash is stored; the raw token is returned once and never persisted.
    raw_token = "ghtok_" + secrets.token_hex(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    store.store_token_hash(session_id, token_hash)

    return {
        "session_id": session_id,
        "intent": intent,
        "scopes": scopes,
        "public_key": public_b64,
        "spawned_at": _iso(spawned),
        "expires_at": _iso(expires),
        "ttl_seconds": ttl,
        "token": raw_token,   # present once — store securely, gateway uses it
    }


def _load_seed(session_id: str) -> bytes:
    key_path = _session_dir(session_id) / "ed25519_seed"
    if not key_path.exists():
        raise SessionError(f"ephemeral key for {session_id} not found (already evaporated?)")
    return key_path.read_bytes()


def act(
    store: ResidueStore,
    session_id: str,
    tool: str,
    action: str,
    params: Optional[dict[str, Any]] = None,
    response: Optional[dict[str, Any]] = None,
    http_status: int = 200,
    enforce_scope: bool = True,
) -> dict[str, Any]:
    """Record a scoped, signed action against a live session."""
    row = store.get_session(session_id)
    if row is None:
        raise SessionError(f"session {session_id} not found")
    if row["evaporated_at"]:
        raise ExpiredSessionError(f"session {session_id} already evaporated")
    if _now() > _parse_iso(row["expires_at"]):
        raise ExpiredSessionError(f"session {session_id} TTL expired at {row['expires_at']}")

    scopes = [s for s in (row["scopes"] or "").split(",") if s]
    if enforce_scope and scopes and tool not in scopes:
        raise ScopeError(f"tool '{tool}' not in session scopes {scopes}")

    params = params or {}
    seed = _load_seed(session_id)
    seq = store.next_seq(session_id)
    action_id = "act_" + secrets.token_hex(8)
    ts = _iso(_now())

    params_hash = crypto.canonical_hash(params)
    response_hash = crypto.canonical_hash(response) if response is not None else None

    # The signed payload binds order, identity, tool, action, and data hashes.
    payload = {
        "session_id": session_id,
        "seq": seq,
        "action_id": action_id,
        "tool": tool,
        "action": action,
        "params_hash": params_hash,
        "response_hash": response_hash,
        "http_status": http_status,
        "timestamp": ts,
    }
    signature = crypto.sign(seed, crypto.canonical_hash(payload).encode())

    store.insert_action(
        {
            "action_id": action_id,
            "session_id": session_id,
            "seq": seq,
            "tool": tool,
            "action": action,
            "params_hash": params_hash,
            "response_hash": response_hash,
            "http_status": http_status,
            "decision": "executed",
            "timestamp": ts,
            "signature": signature,
        }
    )

    return {
        "action_id": action_id,
        "session_id": session_id,
        "seq": seq,
        "tool": tool,
        "action": action,
        "params_hash": params_hash,
        "response_hash": response_hash,
        "http_status": http_status,
        "timestamp": ts,
        "signature": signature,
    }


def evaporate(store: ResidueStore, session_id: str) -> dict[str, Any]:
    """Destroy the ephemeral key, sign the action chain, finalize the record."""
    row = store.get_session(session_id)
    if row is None:
        raise SessionError(f"session {session_id} not found")
    if row["evaporated_at"]:
        raise SessionError(f"session {session_id} already evaporated")

    seed = _load_seed(session_id)
    actions = store.actions_for(session_id)
    chain = [
        {
            "seq": a["seq"],
            "action_id": a["action_id"],
            "signature": a["signature"],
        }
        for a in actions
    ]
    root_signature = crypto.sign(
        seed, crypto.canonical_hash({"session_id": session_id, "chain": chain}).encode()
    )

    evaporated = _now()
    spawned = _parse_iso(row["spawned_at"])
    lived = (evaporated - spawned).total_seconds()

    store.finalize_session(session_id, _iso(evaporated), lived, root_signature)
    store.log_credential(
        session_id,
        _fingerprint(crypto.unb64(row["public_key"])),
        "evaporate",
        _iso(evaporated),
    )

    # Shred the key material and remove the session directory.
    sdir = _session_dir(session_id)
    key_path = sdir / "ed25519_seed"
    if key_path.exists():
        with open(key_path, "wb") as fh:
            fh.write(secrets.token_bytes(len(seed)))
            fh.flush()
            os.fsync(fh.fileno())
    if sdir.exists():
        shutil.rmtree(sdir)

    return {
        "session_id": session_id,
        "intent": row["intent"],
        "status": "evaporated",
        "lived_for_seconds": round(lived, 3),
        "actions_executed": len(actions),
        "root_signature": root_signature,
        "evaporated_at": _iso(evaporated),
    }


def replay(store: ResidueStore, session_id: str) -> dict[str, Any]:
    """Return the full session record plus verification result."""
    row = store.get_session(session_id)
    if row is None:
        raise SessionError(f"session {session_id} not found")

    public_raw = crypto.unb64(row["public_key"])
    actions = store.actions_for(session_id)

    verified_actions = []
    all_ok = True
    for a in actions:
        payload = {
            "session_id": session_id,
            "seq": a["seq"],
            "action_id": a["action_id"],
            "tool": a["tool"],
            "action": a["action"],
            "params_hash": a["params_hash"],
            "response_hash": a["response_hash"],
            "http_status": a["http_status"],
            "timestamp": a["timestamp"],
        }
        ok = crypto.verify(public_raw, crypto.canonical_hash(payload).encode(), a["signature"])
        all_ok = all_ok and ok
        verified_actions.append(
            {
                "seq": a["seq"],
                "action_id": a["action_id"],
                "tool": a["tool"],
                "action": a["action"],
                "params_hash": a["params_hash"],
                "response_hash": a["response_hash"],
                "http_status": a["http_status"],
                "timestamp": a["timestamp"],
                "signature": a["signature"],
                "verified": ok,
            }
        )

    root_ok = None
    if row["root_signature"]:
        chain = [
            {"seq": a["seq"], "action_id": a["action_id"], "signature": a["signature"]}
            for a in actions
        ]
        root_ok = crypto.verify(
            public_raw,
            crypto.canonical_hash({"session_id": session_id, "chain": chain}).encode(),
            row["root_signature"],
        )
        all_ok = all_ok and root_ok

    return {
        "session_id": session_id,
        "intent": row["intent"],
        "scopes": [s for s in (row["scopes"] or "").split(",") if s],
        "spawned_at": row["spawned_at"],
        "expires_at": row["expires_at"],
        "evaporated_at": row["evaporated_at"],
        "lived_for_seconds": row["lived_seconds"],
        "public_key": row["public_key"],
        "actions": verified_actions,
        "root_signature": row["root_signature"],
        "root_verified": root_ok,
        "verified": all_ok,
    }
