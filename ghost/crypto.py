"""Cryptographic primitives for GHOST.

Ed25519 signing of residue entries plus SHA-256 hashing helpers. The signing
key is generated per-session at spawn time and destroyed at evaporate time;
only the public (verifying) key is persisted so the audit trail can be verified
after the session is gone.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def generate_keypair() -> tuple[bytes, bytes]:
    """Return (private_seed_32b, public_raw_32b) for a fresh Ed25519 key."""
    sk = Ed25519PrivateKey.generate()
    private_seed = sk.private_bytes_raw()
    public_raw = sk.public_key().public_bytes_raw()
    return private_seed, public_raw


def sign(private_seed: bytes, message: bytes) -> str:
    """Sign message with the private seed; return base64 signature."""
    sk = Ed25519PrivateKey.from_private_bytes(private_seed)
    sig = sk.sign(message)
    return base64.b64encode(sig).decode("ascii")


def verify(public_raw: bytes, message: bytes, signature_b64: str) -> bool:
    """Verify a base64 signature against message using the raw public key."""
    try:
        pk = Ed25519PublicKey.from_public_bytes(public_raw)
        pk.verify(base64.b64decode(signature_b64), message)
        return True
    except (InvalidSignature, ValueError):
        return False


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(text)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_hash(obj: Any) -> str:
    """Deterministic SHA-256 over a JSON-serialisable object."""
    return sha256_hex(json.dumps(obj, sort_keys=True, separators=(",", ":")))
