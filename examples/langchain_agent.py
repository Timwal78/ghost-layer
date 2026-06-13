"""Example: route an agent's HTTP tool calls through GHOST.

This works with any agent framework (LangChain, OpenAI SDK, custom) because the
transport is injected. Here we use `requests`; swap in httpx, an SDK client, or
a LangChain tool wrapper as needed.

    pip install ghost-layer requests
    python examples/langchain_agent.py
"""

from __future__ import annotations

import requests

from ghost import spawn, evaporate, replay, possess
from ghost.store import ResidueStore


def requests_transport(method, url, headers=None, **kwargs):
    """Adapter: GhostProxy -> requests."""
    return requests.request(method, url, headers=headers, timeout=15, **kwargs)


def main():
    store = ResidueStore()

    # 1. Spawn an ephemeral, scoped session for this agent task.
    session = spawn(
        store,
        intent="enrich_lead_record",
        ttl=120,
        scopes=["httpbin"],  # the only tool this agent may touch
    )
    sid = session["session_id"]
    print(f"spawned {sid}  (expires {session['expires_at']})")

    # 2. Possess: wrap the transport so every mutating call is recorded + signed.
    proxy = possess(store, sid, requests_transport, token="ghtok_demo")

    # 3. The agent performs its work THROUGH the proxy. Auth headers it tries to
    #    set are stripped; the ephemeral ghost token is injected instead.
    resp = proxy.request(
        "POST",
        "https://httpbin.org/post",
        tool="httpbin",
        action="submit_enrichment",
        params={"lead_id": 4471, "source": "agent"},
        json={"lead_id": 4471},
        headers={"Authorization": "Bearer THIS_WILL_BE_STRIPPED"},
    )
    print("upstream status:", resp.status_code)

    # 4. Evaporate: shred the key, sign the action chain.
    out = evaporate(store, sid)
    print(f"evaporated after {out['lived_for_seconds']}s, "
          f"{out['actions_executed']} actions recorded")

    # 5. Replay: verify the tamper-evident residue.
    r = replay(store, sid)
    print("residue verified:", r["verified"], "| root:", r["root_verified"])


if __name__ == "__main__":
    main()
