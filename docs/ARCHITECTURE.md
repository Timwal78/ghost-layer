# GHOST: Technical Architecture

## Design Principle
Ephemeral execution proxy: Agents spawn temporary credentials, execute pre-validated actions, evaporate, and leave cryptographically-signed residue for audit.

---

## Core Flow

```
[Agent Intent] 
    ↓
[ghost spawn] → ephemeral session + ed25519 key pair
    ↓
[ghost possess] → local HTTP proxy intercept (localhost:9999)
    ↓
[ghost act] → API call routed through proxy
    ↓
[Pre-Hook] → scope validation (is this action allowed?)
    ↓
[Execute] → real API call (with ephemeral credentials)
    ↓
[Post-Hook] → response logged + signature appended
    ↓
[ghost evaporate] → session destroyed, credentials rotated/revoked
    ↓
[ghost residue] → immutable, signed audit entry in SQLite
```

---

## Component Specs

### 1. `ghost spawn`
**Purpose:** Create ephemeral session with rotated credentials.

**Input:**
- `--intent` (string): Human-readable intent (e.g., "deploy_staging", "fetch_metrics")
- `--ttl` (int): Lifetime in seconds (default: 300s = 5 min)
- `--scope` (list): API scopes (e.g., "aws_ec2", "stripe_create_charge")

**Output:**
```json
{
  "session_id": "gh_8f2a7c3e...",
  "token": "ghtoken_xxxxx...",
  "ephemeral_key": "ed25519_public_xxxx",
  "spawn_time": "2026-06-12T15:32:00Z",
  "expires_at": "2026-06-12T15:37:00Z",
  "ttl_seconds": 300,
  "scopes": ["aws_ec2"]
}
```

**Implementation Notes:**
- Each `spawn` generates a new ed25519 keypair (stored in `/tmp/ghost_sessions/`)
- Token is HMAC-signed with SHA-256 (server rotates weekly)
- TTL countdown begins immediately; no renewal possible
- Scope validation happens at `ghost act` time

### 2. `ghost possess`
**Purpose:** Install local HTTP proxy that intercepts agent API calls.

**Input:**
- `--agent` (string): Agent type (e.g., "openai://gpt-4", "langchain://chain-id", "custom://http://localhost:8000")
- `--session-id` (string): Session ID from `ghost spawn`
- `--port` (int): Proxy listen port (default: 9999)

**Output:**
```
Proxy active on localhost:9999
Forwarding to agent: openai://gpt-4
Session: gh_8f2a7c3e...
Ready for ghost act
```

**Implementation Notes:**
- Python `http.server.HTTPServer` (single-threaded for simplicity)
- Intercepts `requests.post()` calls (monkey-patch `requests.Session`)
- Strips user credentials from outbound headers
- Inserts ephemeral session token in `X-Ghost-Token` header
- Logs all mutations (POST, PUT, DELETE); reads (GET) logged only if verbose mode

### 3. `ghost act`
**Purpose:** Execute scoped API call through proxy with pre/post validation.

**Input:**
- `--tool` (string): Tool name (e.g., "aws_ec2", "stripe_create_charge")
- `--action` (string): Action name (e.g., "RunInstances", "create_payment_intent")
- `--params` (JSON): Action parameters
- `--validate-pre` (script, optional): Pre-execution script (exits non-zero to block)
- `--validate-post` (script, optional): Post-execution validation

**Output:**
```json
{
  "action_id": "act_5d3f...",
  "tool": "aws_ec2",
  "action": "RunInstances",
  "status": "executed",
  "response": { "InstanceId": "i-0f1234..." },
  "residue_hash": "sha256_xxxx...",
  "timestamp": "2026-06-12T15:32:45Z"
}
```

**Implementation Notes:**
- Pre-hook: Script exit code 0 = proceed; non-zero = block + log denial
- Post-hook: Validates response shape (optional schema file)
- All API payloads logged to SQLite before execution
- Supports retry with exponential backoff (3 attempts default)
- Failures logged with full context for replay debugging

### 4. `ghost evaporate`
**Purpose:** Destroy session, rotate credentials, confirm cleanup.

**Input:**
- `--session-id` (string): Session ID to evaporate

**Output:**
```json
{
  "session_id": "gh_8f2a7c3e...",
  "status": "evaporated",
  "lived_for_seconds": 47,
  "actions_executed": 3,
  "residue_hash": "sha256_root_xxxx...",
  "credentials_rotated": true,
  "ephemeral_key_destroyed": true,
  "timestamp": "2026-06-12T15:33:15Z"
}
```

**Implementation Notes:**
- Deletes `/tmp/ghost_sessions/gh_8f2a7c3e/` entirely
- Overwrites token in-memory (no reference remains)
- Posts final residue hash to immutable SQLite store
- Returns summary of actions executed during session lifetime
- Confirms zero credentials remain in accessible memory

### 5. `ghost replay`
**Purpose:** Retrieve cryptographically-signed execution history.

**Input:**
- `--session-id` (string): Session to replay (or `--all` for full history)
- `--output-format` (string): "json" (default), "csv", "html"

**Output:**
```json
{
  "session_id": "gh_8f2a7c3e...",
  "intent": "deploy_staging",
  "spawned_at": "2026-06-12T15:32:00Z",
  "evaporated_at": "2026-06-12T15:33:15Z",
  "actions": [
    {
      "action_id": "act_5d3f...",
      "tool": "aws_ec2",
      "action": "RunInstances",
      "timestamp": "2026-06-12T15:32:45Z",
      "params_hash": "sha256_params...",
      "response_hash": "sha256_response...",
      "signature": "ed25519_sig_xxxxx..."
    }
  ],
  "root_signature": "ed25519_root_sig_xxxxx...",
  "verified": true
}
```

**Implementation Notes:**
- Each action signed with ed25519 private key (stored server-side only)
- Root signature covers entire session history (immutable proof)
- Signatures verified locally (requires public key from `ghost spawn` output)
- HTML format renders as audit trail with visual diff markers
- CSV export for compliance/logging systems

---

## Data Storage

### SQLite Schema

```sql
CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  intent TEXT,
  scope TEXT,  -- comma-separated
  spawned_at TIMESTAMP,
  ttl_seconds INTEGER,
  evaporated_at TIMESTAMP,
  root_signature TEXT
);

CREATE TABLE actions (
  action_id TEXT PRIMARY KEY,
  session_id TEXT REFERENCES sessions(session_id),
  tool TEXT,
  action TEXT,
  params_hash TEXT,
  response_hash TEXT,
  http_status INTEGER,
  timestamp TIMESTAMP,
  signature TEXT
);

CREATE TABLE credentials_log (
  session_id TEXT REFERENCES sessions(session_id),
  key_hash TEXT,
  rotated_at TIMESTAMP,
  reason TEXT  -- "spawn", "rotate", "evaporate"
);

CREATE INDEX idx_session_spawned ON sessions(spawned_at DESC);
CREATE INDEX idx_action_session ON actions(session_id);
```

### File Storage
- Ephemeral keypairs: `/tmp/ghost_sessions/{session_id}/ed25519_key`
- Proxy logs (debug): `/tmp/ghost_sessions/{session_id}/proxy.log`
- Validation hooks: `~/.ghost/hooks/{tool}_{action}_pre.sh`, `_post.sh`

---

## Security Model

### Credential Lifecycle
1. **Spawn**: Generate ed25519 keypair + HMAC token
2. **Possess**: Token embedded in proxy interceptor (memory-only)
3. **Act**: Token used in X-Ghost-Token header (never in URL/body)
4. **Evaporate**: Key + token destroyed; SQLite record signed
5. **Replay**: Signature verified without accessing ephemeral key

### Attack Surface
- **Proxy bypass**: Agent calls non-proxied API → Ghost catches in logs (session-scoped token rejected upstream)
- **Token theft**: Token is HMAC-signed; tampering causes signature mismatch at verification
- **Residue forgery**: Root signature covers all actions; single byte change invalidates entire session
- **TTL extension**: Token expires server-side; no refresh possible (by design)

### Assumptions
- `localhost` proxy is secure (runs on agent machine)
- Ed25519 keys are generated with cryptographically-secure RNG
- SQLite store is on trusted filesystem

---

## Integration Points

### Agent Libraries
- **LangChain**: Monkey-patch `requests.Session` in agent chain
- **OpenAI SDK**: Intercept via `client.api_key` substitution + proxy env var
- **Custom**: Expose HTTP proxy URL for routing through `ghost possess`

### Validation Hooks
Pre-hooks can be:
- Shell scripts (`exit 0` = allowed)
- Python validators (raise exception to deny)
- YAML rule files (scope checker)

Post-hooks can:
- Assert response shape (e.g., "response.InstanceId must exist")
- Invoke webhooks (notify on success/failure)
- Trigger downstream automation

---

## Roadmap

**Week 1 MVP:**
- ✅ Python CLI (Click-based)
- ✅ Session manager (spawn/evaporate)
- ✅ SQLite residue store
- ✅ Basic HTTP proxy (localhost:9999)
- ✅ Ed25519 signing

**Week 2:**
- Rust proxy core (performance)
- OpenAI/LangChain hooks
- Pre/post validation hooks
- HTML audit trail rendering

**Month 2:**
- Multi-agent orchestration
- Credential rotation policies
- Webhook notifications
- Cloud-hosted proxy (optional)

---

## References
- [Ed25519 Specification](https://en.wikipedia.org/wiki/EdDSA)
- [HMAC-SHA256](https://en.wikipedia.org/wiki/HMAC)
- [SQLite Best Practices](https://www.sqlite.org/bestpractice.html)
