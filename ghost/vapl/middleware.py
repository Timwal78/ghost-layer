"""VAPL middleware helpers for Ghost Layer gateway responses."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

log = logging.getLogger("ghost.vapl.middleware")


def _agent_did(session_info: dict[str, Any]) -> str:
    raw = session_info.get("session_id", "") or session_info.get("intent", "")
    h = hashlib.sha256(raw.encode()).digest()
    b64 = base64.urlsafe_b64encode(h[:32]).rstrip(b"=").decode()
    return f"did:ghost:{b64}"


def emit_vapl_headers(session_info: dict[str, Any], path: str, status_code: int) -> dict[str, str]:
    """Return VAPL VC response headers for a successful proxied request."""
    try:
        from .soul_manager import get_soul
        from .credentials import issue_interaction_vc

        soul = get_soul()
        subject_did = _agent_did(session_info)
        resource = f"https://ghost-layer.onrender.com{path}"
        outcome = "success" if 200 <= status_code < 300 else "error"
        vc = issue_interaction_vc(soul, subject_did, "GhostGatewayForward", resource, outcome)
        vc_b64 = base64.urlsafe_b64encode(json.dumps(vc).encode()).rstrip(b"=").decode()
        return {
            "X-VAPL-VC": vc_b64,
            "X-VAPL-Issuer": soul.did,
            "X-VAPL-VC-ID": vc.get("id", ""),
        }
    except Exception as exc:  # noqa: BLE001
        log.debug("[VAPL] header emission failed: %s", exc)
        return {}


def build_vapl_manifest() -> dict:
    """Build the /.well-known/vapl.json ProvenanceSoul manifest for Ghost Layer."""
    try:
        from .soul_manager import get_soul
        from .identity import _base58_encode, ED25519_MULTICODEC

        soul = get_soul()
        pub_bytes = base64.urlsafe_b64decode(soul.public_key_base64url + "==")
        multibase = "z" + _base58_encode(ED25519_MULTICODEC + pub_bytes)
        key_id = soul.did[len("did:key:"):]
        return {
            "@context": [
                "https://www.w3.org/ns/credentials/v2",
                "https://vapl.scriptmasterlabs.com/v1/context.jsonld",
            ],
            "id": f"{soul.did}#soul",
            "type": "ProvenanceSoul",
            "controller": soul.did,
            "verificationMethod": {
                "id": f"{soul.did}#{key_id}",
                "type": "Ed25519VerificationKey2020",
                "controller": soul.did,
                "publicKeyMultibase": multibase,
            },
            "service": "GhostLayer",
            "endpoint": "https://ghost-layer.onrender.com",
            "capabilities": ["GhostGatewayForward", "EphemeralExecution", "TokenValidation"],
            "vcHeadersEmitted": True,
            "vcHeader": "X-VAPL-VC",
            "registry": "https://vapl-registry.onrender.com",
            "updatedAt": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat().replace("+00:00", "Z"),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("[VAPL] manifest build failed: %s", exc)
        return {"error": str(exc)}
