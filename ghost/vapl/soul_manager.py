"""Ghost Layer VAPL soul singleton."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading

from .identity import ProvenanceSoul, generate_soul

log = logging.getLogger("ghost.vapl.soul")
_lock = threading.Lock()
_soul: ProvenanceSoul | None = None


def get_soul() -> ProvenanceSoul:
    global _soul
    if _soul is None:
        with _lock:
            if _soul is None:
                path = os.environ.get("VAPL_SOUL_FILE", os.path.join(tempfile.gettempdir(), "vapl_soul_ghost.json"))
                if os.path.exists(path):
                    try:
                        with open(path) as f:
                            _soul = ProvenanceSoul.from_dict(json.load(f))
                        log.info("[VAPL] Soul loaded  DID=%s", _soul.did)
                        return _soul
                    except Exception as exc:
                        log.warning("[VAPL] Could not load soul: %s — generating new", exc)
                _soul = generate_soul()
                try:
                    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
                    with open(path, "w") as f:
                        json.dump(_soul.to_dict(), f, indent=2)
                    log.info("[VAPL] New soul generated  DID=%s", _soul.did)
                except Exception as exc:
                    log.warning("[VAPL] Could not persist soul: %s", exc)
    return _soul
