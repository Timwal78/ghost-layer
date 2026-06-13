"""Example: GHOST as the execution layer for an x402 / XRPL payment agent.

Scenario: a SqueezeOS GOD MODE signal fires. An autonomous agent must route a
micropayment over an x402 rail (e.g. NEXUS-402) without ever holding a
persistent key. GHOST gives it a 60-second body and a signed receipt.

This example fakes the rail transport so it runs offline. In production, the
transport calls your real NEXUS-402 / 402Proof endpoint.
"""

from __future__ import annotations

from ghost import spawn, evaporate, replay, possess
from ghost.store import ResidueStore


class _RailResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def x402_rail_transport(method, url, headers=None, **kwargs):
    """Fake x402 rail. Replace with a real call to NEXUS-402 / 402Proof."""
    # Real impl: forward `X-Ghost-Token` upstream, attach 402 payment proof, etc.
    return _RailResponse(200, {"tx_hash": "EFA1...XRPL", "settled": True})


def main():
    store = ResidueStore()

    session = spawn(
        store,
        intent="settle_signal_micropayment",
        ttl=60,
        scopes=["nexus402"],  # confined to one rail
    )
    sid = session["session_id"]
    print(f"agent body spawned: {sid}")

    proxy = possess(store, sid, x402_rail_transport, token="ghtok_x402")

    resp = proxy.request(
        "POST",
        "https://nexus-402.com/pay",
        tool="nexus402",
        action="settle_micropayment",
        params={"asset": "USDC", "amount": "0.05", "memo": "GODMODE:AMC"},
    )
    print("rail response:", resp.json())

    out = evaporate(store, sid)
    r = replay(store, sid)
    print(f"settled in {out['lived_for_seconds']}s | residue verified: {r['verified']}")
    print("root signature:", r["root_signature"][:24], "...")


if __name__ == "__main__":
    main()
