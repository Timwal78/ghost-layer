# AGENTS.md — Operating Brief for ghost-layer

Any agent, on any platform (Claude, Antigravity, Cowork, Copilot, Cursor, etc.), MUST read this before acting on this repository. It encodes how this project is published, what is already wired, and the hard rules. Following it prevents broken releases and leaked credentials.

**Owner:** Timothy Walton · GitHub `Timwal78` · PyPI project `ghost-layer` · ScriptMasterLabs@gmail.com · Script Master Labs LLC (SDVOSB)

---

## 1. Current state (do not redo these)

- **Published:** `ghost-layer` is live on PyPI. Current version: see `ghost/__init__.py` `__version__` and `pyproject.toml`.
- **Repo:** https://github.com/Timwal78/ghost-layer — `main` + tag history.
- **CI/CD:** `.github/workflows/ci.yml` runs the test matrix (Py 3.9–3.12) on every push/PR, and publishes to PyPI via OIDC Trusted Publishing on any `v*` tag.
- **Trusted Publishing is configured on BOTH ends:**
  - PyPI publisher: repo `Timwal78/ghost-layer`, workflow `ci.yml`, environment `pypi`.
  - GitHub environment `pypi` exists (no secrets, no protection rules required).

> **Implication:** publishing needs **NO API token** and **NO terminal**. Do not add a `PYPI_API_TOKEN` secret, do not paste tokens, do not run `twine upload` manually unless OIDC is unavailable.

---

## 2. How to publish a new version (the ONLY supported path)

1. Bump the version in **BOTH** `pyproject.toml` and `ghost/__init__.py` (`__version__`). They must match.
2. Update `CHANGELOG.md` (create if missing).
3. Commit to `main`.
4. Create a **GitHub Release** with tag `vX.Y.Z` (matching the version). → CI runs tests → builds → publishes to PyPI via OIDC.
5. Verify: `pip index versions ghost-layer` shows the new version as LATEST.

The publish job is gated: `if: startsWith(github.ref, 'refs/tags/v')` and runs in `environment: pypi` with `permissions: id-token: write`. Do not remove either — they are what make OIDC work.

---

## 3. Hard rules (credentials & safety)

- **NEVER** commit a token, password, or `~/.pypirc` with a real token into the repo, a workflow, a screenshot, or chat. Treat any pasted secret as compromised and tell the owner to rotate it.
- `~/.pypirc` is a local fallback only. Prefer OIDC. If a real token ever lands in it, it must not be opened in a shared/agent editor view.
- Do not weaken `docs/SECURITY.md` claims. The current honest limitation: the ephemeral ghost token is **NOT yet enforced upstream** (needs a gateway; roadmap). v0.1 provides ephemerality + scope + signed residue, **NOT** upstream rejection. Keep messaging accurate in READMEs, posts, and the SEO page.
- Run `pytest -q` before any release. All tests must pass (currently 17).
- Do not edit files under `docs/deploy/` to point at the wrong host. Owner's domains: `scriptmasterlabs.com`, `nexus-402.com`, `neuralosagent.com`, `va-ratings.org`. **Never touch `nexus-os.com`** (not the owner's).

---

## 4. Architecture quick map (so you don't relearn it)

```
ghost/crypto.py   Ed25519 sign/verify (cryptography lib) + SHA-256 canonical hash
ghost/store.py    SQLite residue: sessions, actions, credentials_log
ghost/session.py  lifecycle: spawn / act / evaporate / replay; scope + TTL enforce
ghost/proxy.py    SDK-agnostic HTTP intercept; strips caller auth, injects ghost token
ghost/cli.py      Click CLI: ghost spawn|possess|act|evaporate|replay
```

Lifecycle: `spawn` (mint ephemeral keypair) → `act` (scoped, signed entry) → `evaporate` (shred key, root-sign the chain) → `replay` (re-verify each + root). Tamper any residue row → replay returns `verified: false` and the key to re-sign is already destroyed.

**Verify a clean install end-to-end:**
```bash
ghost spawn --intent test --ttl 300 --scope demo      # copy gh_... id
ghost act --tool demo --action go --session-id gh_...
ghost evaporate --session-id gh_...
ghost replay --session-id gh_...                       # expect verified: true
```

---

## 5. SEO / web (owner standing preference)

Keep `scriptmasterlabs.com` + Google Search Console sitemap current for x402 / XRP / XAH / AI-agent builds. Assets ready in repo:
- `docs/ghost-stack-page.html` → deploy to `scriptmasterlabs.com/stack/ghost`
- `docs/deploy/sitemap.xml`, `robots.txt`, `llms.txt` → merge into the site, then (re)submit sitemap in Google Search Console.

Confirm the host before deploying (Vercel / Render / GitHub Pages all in use).

---

## 6. Definition of done for any change

- [ ] Version bumped in `pyproject.toml` AND `ghost/__init__.py` (matching)
- [ ] `CHANGELOG.md` updated
- [ ] `pytest -q` green
- [ ] Released via GitHub Release tag `vX.Y.Z` (OIDC auto-publish)
- [ ] `pip index versions ghost-layer` shows new LATEST
- [ ] No tokens introduced anywhere; `SECURITY.md` claims still accurate
