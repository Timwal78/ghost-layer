# Changelog

All notable changes to `ghost-layer` are documented here.

---

## [0.1.1] — 2026-06-13

### Added — Gateway Enforcement Layer (closes SECURITY.md gap)

- **`ghost/gateway.py`** — `GhostGateway` reverse proxy / egress broker.
  - Validates `X-Ghost-Token` on every inbound request against the residue store.
  - Rejects (HTTP 401) if token is missing, unknown, TTL-expired, or evaporated.
  - **Sidecar mode**: forwards to a local upstream; enforces token liveness for services you control.
  - **Broker mode** (`--upstream-key`): holds the real upstream credential; injects it only for valid live tokens. Works with any third-party API (AWS, Stripe, etc.) — the real key never reaches the agent.
  - `evaporate()` now invalidates the token server-side. A cached token returns 401 from the gateway immediately — the upstream-enforcement gap described in `docs/SECURITY.md` is closed.

- **`ghost serve` CLI command** — start the gateway from the terminal:
  ```
  ghost serve --upstream https://api.stripe.com --upstream-key sk_live_... --port 7391
  ```

- **`spawn()` now mints an opaque bearer token** (`ghtok_<hex64>`):
  - Only the SHA-256 hash is persisted in the residue DB; the raw token is returned once and never stored.
  - Token is displayed at `ghost spawn` time with a clear "store securely" warning.

- **`ghost.validate_gateway_token()`** — public API for embedding the gateway validator in your own HTTP middleware.

- **`GhostGateway` as context manager** — `with GhostGateway(...) as gw:` for test / scripted use.

- **Additive DB migration** — `token_hash` column added to existing `sessions` tables via guarded `ALTER TABLE` (backward-compatible; existing v0.1.0 DBs upgrade automatically).

- **10 new tests** in `tests/test_gateway.py` — token minting, validation, enforcement after evaporate, proxy forwarding, broker key injection. Total: 27 tests.

### Changed

- `spawn()` return manifest now includes `"token": "ghtok_..."` field.
- `pyproject.toml` classifier promoted from Alpha → Beta.
- `ghost/__init__.py` exports `GhostGateway`, `validate_gateway_token`.

### Security

- Prior to v0.1.1, GHOST provided ephemerality + scope + signed residue but could not enforce token revocation at the upstream API layer. v0.1.1 closes this via the gateway: the real credential lives only in the gateway process; evaporation immediately kills forwarding for that token. See `docs/SECURITY.md` for the updated threat model.

---

## [0.1.0] — 2026-06-13

Initial release.

- Ephemeral Ed25519 keypair per session (`spawn`)
- Scoped, signed action recording (`act`)
- Key shredding + root signature at session end (`evaporate`)
- Cryptographic residue verification (`replay`) — tamper returns `verified: false`
- SDK-agnostic HTTP intercept (`GhostProxy` / `possess`)
- Click CLI: `ghost spawn | act | evaporate | replay | possess`
- SQLite residue store (append-only)
- 17 tests across lifecycle, scope denial, TTL expiry, tamper detection, proxy
