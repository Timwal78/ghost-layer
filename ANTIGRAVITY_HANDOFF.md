# GHOST — Agent Handoff Brief

**For:** the coding agent picking this up (e.g. Google Antigravity)
**From:** prior session — package is built, tested, committed. Only publishing remains.
**Owner:** Timothy Walton · timwal78 (GitHub) · ScriptMasterLabs@gmail.com · Script Master Labs LLC

---

## TL;DR — what's left

The Python package `ghost-layer` v0.1.0 is fully built, tested (17/17 passing),
twine-checked, and committed to a local git repo with tag `v0.1.0-alpha`. It has
**not** been pushed to GitHub or published to PyPI. Do those two things.

You will need the owner to provide, **in your local environment** (never in plain
chat that persists):
- A GitHub token or `gh auth login` session for user `timwal78`
- A PyPI API token (or set up Trusted Publishing — see Option B, no token needed)

---

## Where the code is

The owner has a file `ghost-layer-v0.1.0.tar.gz`. Extract it; the repo root is
`ghost-layer/`. It already contains a git history and the tag `v0.1.0-alpha`.

Repo contents (23 tracked files):
```
ghost/__init__.py        package API: spawn, act, evaporate, replay, possess, GhostProxy, ResidueStore
ghost/crypto.py          Ed25519 sign/verify (via `cryptography`), SHA-256 canonical hashing
ghost/store.py           SQLite residue store (sessions, actions, credentials_log)
ghost/session.py         lifecycle logic: spawn/act/evaporate/replay + scope & TTL enforcement
ghost/proxy.py           SDK-agnostic HTTP intercept; strips caller auth, injects ghost token
ghost/cli.py             Click CLI: `ghost spawn|possess|act|evaporate|replay`
tests/test_ghost.py      17 tests: lifecycle, scope deny, TTL expiry, tamper detection, proxy
examples/langchain_agent.py        requests-based agent routed through GhostProxy
examples/x402_payment_agent.py     x402/XRPL micropayment agent example (runs offline)
pyproject.toml           setuptools build; console_scripts: ghost = ghost.cli:main
requirements.txt         click>=8.0, cryptography>=41.0
.github/workflows/ci.yml CI: test matrix 3.9-3.12 + PyPI Trusted Publishing (OIDC) on v* tag
docs/ARCHITECTURE.md     technical design
docs/SECURITY.md         threat model + honest limitations (token not yet upstream-enforced)
docs/ghost-stack-page.html   SEO product page for scriptmasterlabs.com/stack/ghost (schema.org)
docs/deploy/sitemap.xml      sitemap incl. /stack/ghost + x402 stack URLs
docs/deploy/robots.txt
docs/deploy/llms.txt
README.md                spellbook-style readme with verified examples
LICENSE                  MIT
push.sh                  one-command publish helper
PUBLISH_NO_TERMINAL.md   browser-only publish path (OIDC)
SEO_DEPLOYMENT.md, LAUNCH_STRATEGY.md
```

Pre-built artifacts also exist (in the owner's outputs):
- `ghost_layer-0.1.0-py3-none-any.whl`
- `ghost_layer-0.1.0.tar.gz` (sdist)
Both passed `twine check`.

---

## Verification already done (you can re-run to confirm)

```bash
cd ghost-layer
pip install -e ".[dev]"
pytest -q            # expect: 17 passed
ghost --version      # expect: ghost, version 0.1.0
```

End-to-end CLI sanity (uses a temp DB):
```bash
export GHOST_HOME=/tmp/ghost_check
ghost spawn --intent demo --ttl 60 --scope aws_ec2     # note the gh_... session_id
ghost act --tool aws_ec2 --action RunInstances --session-id <SID>
ghost act --tool stripe  --action Charge       --session-id <SID>   # must DENY, exit 2
ghost evaporate --session-id <SID>
ghost replay   --session-id <SID>              # must show verified: true
```

---

## TASK 1 — Push to GitHub

Target repo: `https://github.com/timwal78/ghost-layer` (public, create if absent).

```bash
cd ghost-layer
# auth: gh auth login   (owner provides token/login)
gh repo create timwal78/ghost-layer --public --source=. --remote=origin --push \
  --description "Ephemeral execution layer for autonomous AI agents — scoped credentials, cryptographic residue."
git push origin v0.1.0-alpha
```
No `gh`? Set remote manually and push; owner enters token as the HTTPS password.

---

## TASK 2 — Publish to PyPI

### Option A — API token (fast)
```bash
cd ghost-layer
python -m build
TWINE_USERNAME=__token__ TWINE_PASSWORD=<PYPI_TOKEN_FROM_OWNER> twine upload dist/*
```

### Option B — Trusted Publishing (no token; preferred)
The workflow `.github/workflows/ci.yml` is already configured for OIDC. To use it:
1. On PyPI → Account → Publishing → add a **pending publisher**:
   project `ghost-layer`, owner `timwal78`, repo `ghost-layer`,
   workflow `ci.yml`, environment `pypi`.
2. On GitHub repo → Settings → Environments → create environment `pypi`.
3. Create a release with tag `v0.1.0`. The `publish` job builds and uploads via
   OIDC — no secret required.

Verify success: `pip install ghost-layer` resolves and installs 0.1.0.

---

## TASK 3 (optional) — Deploy the SEO page

`docs/ghost-stack-page.html` → host at `scriptmasterlabs.com/stack/ghost/index.html`.
Merge `docs/deploy/sitemap.xml` into the site's `sitemap.xml`, then submit the
sitemap in Google Search Console. Owner's hosting spans Vercel / Render / GitHub
Pages — confirm which serves scriptmasterlabs.com before deploying.

---

## Security notes for the agent

- Treat any token the owner pastes as **single-use**; advise rotation after the
  push/publish completes.
- Prior tokens shared in an earlier chat session are considered compromised and
  should already be revoked — do not attempt to reuse them.
- Do not commit any token into the repo or the workflow file. Use `gh auth`,
  a `~/.pypirc`, env vars at the prompt, or OIDC.
- `docs/SECURITY.md` documents a real limitation to surface on launch: the
  ephemeral ghost token is not yet enforced by upstream APIs (needs a gateway;
  on the roadmap). It currently provides ephemerality + scope + signed residue,
  not upstream rejection.

---

## Definition of done

- [ ] `github.com/timwal78/ghost-layer` exists, `main` pushed, tag `v0.1.0-alpha` present
- [ ] `pip install ghost-layer` installs 0.1.0 from PyPI
- [ ] (optional) `scriptmasterlabs.com/stack/ghost` live; sitemap submitted to GSC
- [ ] Owner has rotated any tokens used
```
