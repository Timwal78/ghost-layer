#!/usr/bin/env bash
# GHOST — push to GitHub and publish to PyPI.
# Run from the repo root after extracting ghost-layer-v0.1.0.tar.gz.
#
# Prereqs:
#   - gh CLI authenticated  (gh auth login)  OR a remote already set
#   - PyPI token in ~/.pypirc or TWINE_PASSWORD env
set -euo pipefail

REPO="ghost-layer"
GH_USER="timwal78"

echo "==> 1/4  Create GitHub repo (skip if it exists)"
if command -v gh >/dev/null 2>&1; then
  gh repo create "$GH_USER/$REPO" --public --source=. --remote=origin --description \
    "Ephemeral execution layer for autonomous AI agents — scoped credentials, cryptographic residue." \
    --push || git push -u origin main
else
  echo "    gh CLI not found. Set remote manually:"
  echo "    git remote add origin https://github.com/$GH_USER/$REPO.git"
  echo "    git push -u origin main"
fi

echo "==> 2/4  Push tags (creates the v0.1.0-alpha release tag upstream)"
git push origin --tags

echo "==> 3/4  Build distribution"
python -m pip install --upgrade build twine >/dev/null
python -m build

echo "==> 4/4  Publish to PyPI"
echo "    (uses ~/.pypirc or TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-...)"
twine check dist/*
twine upload dist/*

echo
echo "Done. Next:"
echo "  - Upload docs/ghost-stack-page.html  -> scriptmasterlabs.com/stack/ghost/index.html"
echo "  - Merge docs/deploy/sitemap.xml      -> scriptmasterlabs.com/sitemap.xml"
echo "  - Submit sitemap in Google Search Console"
