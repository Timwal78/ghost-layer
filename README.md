# GHOST
### The Spectral Execution Layer for Autonomous Agents

> *"Most agents die in the light. Ours operate in the dark."*

Give your AI agent a **body that vanishes**. GHOST is an ephemeral execution
layer: an agent declares intent, spawns a short-lived signing key, executes
**scoped** actions through an intercept that records **cryptographic residue**,
then evaporates — leaving a tamper-evident audit trail and **zero standing
credentials**.

[![status](https://img.shields.io/badge/status-alpha-39FF14)](https://github.com/timwal78/ghost-layer)
[![license](https://img.shields.io/badge/license-MIT-FFD700)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-FF1493)](pyproject.toml)

---

## The Problem

You gave your agent AWS keys. Now you're watching CloudTrail at 3am.

Agents are **ephemeral bursts of intent**. Humans are persistent. Yet today's
agents execute with persistent, human-shaped credentials and unbounded scope.
One leaked key compromises everything, and there's no signed proof of *why* the
agent did what it did.

## The Inversion

| Human model | Agent model (GHOST) |
|---|---|
| log in → do stuff → log out | declare intent → **spawn** → execute → **evaporate** → leave **residue** |
| session persists | session auto-expires (TTL) |
| broad standing access | scoped to declared tools |
| audit logs (unsigned) | Ed25519-signed, tamper-evident chain |

---

## The Ritual (Quickstart)

```bash
pip install ghost-layer

ghost spawn --intent "deploy_staging" --ttl 300 --scope aws_ec2
ghost act   --tool aws_ec2 --action RunInstances --session-id gh_9ddb...
ghost evaporate --session-id gh_9ddb...
ghost replay    --session-id gh_9ddb...
```

What just happened: your agent never held a standing credential. The session
lived under a second. The residue is **Ed25519-signed** and immutable — replay
it and verify exactly why that instance spawned.

Out-of-scope calls are refused before they run:

```bash
ghost act --tool stripe --action CreateCharge --session-id gh_9ddb...
# DENIED (scope): tool 'stripe' not in session scopes ['aws_ec2']   (exit 2)
```

---

## Five Commands

| Command | What it does |
|---|---|
| `ghost spawn` | Mint an ephemeral session + fresh Ed25519 keypair, TTL countdown begins |
| `ghost possess` | Bind an agent to the session via the intercept proxy |
| `ghost act` | Record a **scoped, signed** action (blocked if out-of-scope or expired) |
| `ghost evaporate` | Shred the key, sign the whole action chain, finalize the residue |
| `ghost replay` | Re-verify every signature + the root chain signature |

---

## Use It In Code (SDK-agnostic)

The transport is injected, so GHOST wraps **any** HTTP client or agent
framework — LangChain, the OpenAI SDK, raw `requests`/`httpx`:

```python
from ghost import spawn, possess, evaporate, replay
from ghost.store import ResidueStore
import requests

store = ResidueStore()
session = spawn(store, intent="enrich_lead", ttl=120, scopes=["httpbin"])

def transport(method, url, headers=None, **kw):
    return requests.request(method, url, headers=headers, **kw)

proxy = possess(store, session["session_id"], transport, token="ghtok_demo")

# Auth headers the agent sets are STRIPPED; the ghost token is injected.
proxy.request("POST", "https://httpbin.org/post",
              tool="httpbin", action="submit",
              headers={"Authorization": "Bearer WILL_BE_STRIPPED"})

evaporate(store, session["session_id"])
print(replay(store, session["session_id"])["verified"])   # True
```

See [`examples/`](examples/) for a LangChain-style agent and an
**x402 / XRPL payment agent** that settles a micropayment through a single
60-second ghost body.

---

## Why It's Tamper-Evident

Every action is signed over a canonical hash binding `session_id`, sequence,
tool, action, and the hashes of params/response. At `evaporate`, a **root
signature** covers the ordered chain. Change one byte of the residue and
`ghost replay` reports `verified: false`. The private key is gone by then — it
**cannot** be re-signed.

```text
spawn ─▶ keypair (priv on disk 0600, pub in residue)
  │
  ├─ act ─▶ sign(payload)  ─▶ residue row
  ├─ act ─▶ sign(payload)  ─▶ residue row
  │
evaporate ─▶ sign(chain) = root_sig ─▶ shred priv key
  │
replay ─▶ verify(each) + verify(root)   ✓ tamper-evident
```

---

## Where It Fits

GHOST is the execution-safety layer beneath the **x402 agentic web**. It lets
autonomous agents trigger payment rails ([NEXUS-402](https://nexus-402.com),
402Proof, XAH Portal) and act on [SqueezeOS](https://scriptmasterlabs.com)
signals **without ever holding a standing key** — every autonomous move leaves
a signed receipt.

Full catalog: **[scriptmasterlabs.com/stack](https://scriptmasterlabs.com/stack)**

---

## Status & Roadmap

- [x] Python CLI — spawn / possess / act / evaporate / replay
- [x] SQLite residue store, Ed25519 signing, scope + TTL enforcement
- [x] Intercept proxy (SDK-agnostic), tamper-detection tests (17 passing)
- [ ] Rust proxy core (performance)
- [ ] Native LangChain / OpenAI tool wrappers
- [ ] Pre/post validation hooks, webhook notifications
- [ ] Optional cloud-hosted proxy

---

## Install from source

```bash
git clone https://github.com/timwal78/ghost-layer
cd ghost-layer
pip install -e ".[dev]"
pytest -q          # 17 passed
```

---

Built by **Script Master Labs LLC** · Disabled U.S. Army Veteran–Owned (SDVOSB) · Kinston, NC
Docs: [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [SECURITY.md](docs/SECURITY.md) · MIT License
