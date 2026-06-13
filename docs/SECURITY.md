# GHOST Security Model

## What GHOST guarantees

1. **Ephemeral keys.** Each session mints a fresh Ed25519 keypair. The private
   seed is written to `GHOST_HOME/sessions/<id>/ed25519_seed` with mode `0600`
   and is **shredded and removed** at `evaporate`. Only the public key persists.

2. **Scope confinement.** A session declares allowed tools at spawn. `act`
   refuses any tool outside that set (exit code 2). An empty scope list means
   "unrestricted" — set scopes in production.

3. **TTL expiry.** After `ttl_seconds`, `act` refuses the session (exit code 3).
   There is no renewal by design; spawn a new body.

4. **Tamper-evident residue.** Every action is signed over a canonical hash of
   `{session_id, seq, action_id, tool, action, params_hash, response_hash,
   http_status, timestamp}`. At evaporate, a **root signature** covers the
   ordered chain. `replay` re-verifies each signature and the root; any edit to
   the residue DB makes verification fail, and the key needed to re-sign is gone.

5. **Credential stripping at the proxy.** `GhostProxy` removes
   `Authorization` / `X-API-Key` / `API-Key` headers a caller sets and injects
   the ephemeral `X-Ghost-Token` instead.

## What GHOST does NOT (yet) guarantee — be honest with users

- **Upstream enforcement requires the gateway.** The enforcement gap present in
  v0.1.0 is closed in v0.1.1 via `GhostGateway`. When you run `ghost serve`
  (sidecar or broker mode), the gateway validates `X-Ghost-Token` on every
  request and immediately rejects it after `evaporate` — even for third-party
  APIs in broker mode. **Without the gateway running**, token revocation is
  local only (scope + TTL enforcement in `act`). If you need true upstream
  rejection, run the gateway.
- **Proxy bypass.** If an agent calls an API *without* going through the
  gateway or `GhostProxy`, that call is unscoped and unlogged. Route all agent
  egress through `ghost serve` in production.
- **Local trust.** The ephemeral seed lives on the agent host during the
  session. A root-level compromise of that host during the session window can
  read it. TTLs should be as short as the task allows.
- **RNG.** Key generation relies on the platform CSPRNG via `cryptography`.

## Reporting

Email security concerns to **ScriptMasterLabs@gmail.com**. Please do not open
public issues for vulnerabilities until a fix is available.

## Cryptography

- Signatures: Ed25519 (`cryptography` library, `Ed25519PrivateKey`).
- Hashing: SHA-256 over canonical JSON (`sort_keys`, tight separators).
- No custom crypto is implemented; GHOST composes vetted primitives.
