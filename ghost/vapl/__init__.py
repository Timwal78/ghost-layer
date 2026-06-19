"""VAPL embedded in Ghost Layer — ephemeral credential issuance for routed sessions."""
from .identity import ProvenanceSoul, generate_soul
from .credentials import issue_interaction_vc, verify_vc
from .soul_manager import get_soul
from .middleware import emit_vapl_headers, build_vapl_manifest

__all__ = [
    "ProvenanceSoul", "generate_soul",
    "issue_interaction_vc", "verify_vc",
    "get_soul",
    "emit_vapl_headers", "build_vapl_manifest",
]
