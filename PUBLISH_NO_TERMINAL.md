# GHOST — Publish With No Terminal (Mobile-Friendly)

Everything here is done in a **web browser** (works on your phone). No git, no
twine, no command line. Two services: GitHub (hosts the code + runs the publish)
and PyPI (receives the package). The trick is **Trusted Publishing** — PyPI
verifies GitHub directly, so you never create or paste a token.

---

## STEP 1 — Put the code on GitHub (browser)

You can't upload git history from a phone, but you can upload the *files* and let
GitHub build the history. Easiest route:

1. Go to **github.com** → **+** (top right) → **New repository**.
2. Name it exactly: `ghost-layer`  · Owner: `timwal78` · Public · **Create**.
3. On the empty repo page, click **"uploading an existing file"**.
4. Extract `ghost-layer-v0.1.0.tar.gz` on your phone (Files app / a zip app),
   then drag/select **all the files inside the `ghost-layer/` folder** into the
   upload box. Commit.

> If selecting nested folders is painful on mobile, this step is genuinely easier
> on a desktop later. The package is already saved in your outputs — no rush.

---

## STEP 2 — Turn on PyPI Trusted Publishing (browser, ~1 min)

This is what removes the token entirely.

1. Log in at **pypi.org**.
2. Go to **pypi.org/manage/account/publishing/** (Account → Publishing).
3. Under **"Add a new pending publisher"**, fill in:
   - **PyPI Project Name:** `ghost-layer`
   - **Owner:** `timwal78`
   - **Repository name:** `ghost-layer`
   - **Workflow name:** `ci.yml`
   - **Environment name:** `pypi`
4. **Add**.

That's it. PyPI now trusts *this specific GitHub workflow* to publish `ghost-layer`.
No API token is ever created.

---

## STEP 3 — Create the GitHub environment (browser, 30 sec)

The workflow publishes from an environment called `pypi`.

1. On the GitHub repo → **Settings** → **Environments** → **New environment**.
2. Name it exactly: `pypi` → **Configure environment** → save (no other settings
   needed).

---

## STEP 4 — Fire the publish by tagging a release (browser)

1. On the repo → **Releases** (right sidebar) → **Create a new release**.
2. **Choose a tag** → type `v0.1.0` → **Create new tag on publish**.
3. Title: `GHOST v0.1.0` → **Publish release**.

That tag matches the workflow trigger (`tags: v*`). GitHub Actions will:
run the tests → build the package → publish to PyPI via OIDC.

Watch progress under the repo's **Actions** tab. When the `publish` job goes
green, `pip install ghost-layer` is live worldwide.

---

## Why this is better than a token

- **Nothing to paste, nothing to leak.** OIDC is short-lived and scoped to that
  one workflow run.
- **Nothing to rotate** later.
- Works entirely from a phone browser once the files are on GitHub.

---

## If you'd rather use a desktop (fastest overall)

Skip all of the above. On a computer, from the extracted `ghost-layer/` folder:

```bash
gh repo create timwal78/ghost-layer --public --source=. --push
git push origin v0.1.0-alpha
python -m build
twine upload dist/*        # paste a FRESH PyPI token at the prompt
```

Either path ends in the same place. The OIDC route is the no-token,
no-terminal one.
