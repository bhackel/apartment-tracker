#!/usr/bin/env bash
#
# Publish the apartment tracker + Towers availability map to a PUBLIC GitHub repo
# with GitHub Pages, WITHOUT leaking git history.
#
# Why fresh history: earlier commits contain a private ntfy topic (now moved to
# the NTFY_URL env var). This script squashes everything into ONE clean commit
# so those old commits are never pushed. Your local history is replaced too, so
# local and remote stay in sync for the auto-update Action.
#
# Prereqs:  gh auth login -h github.com   (run once, interactively)
# Usage:    bash publish_github_pages.sh [repo-name]     (default: apartment-tracker)
set -euo pipefail
cd "$(dirname "$0")"
REPO="${1:-apartment-tracker}"

gh auth status >/dev/null 2>&1 || { echo "Not authed. Run:  gh auth login -h github.com"; exit 1; }
OWNER="$(gh api user -q .login)"
echo ">> Publishing as $OWNER/$REPO"

# Safety net: refuse if a real ntfy topic URL is committed anywhere (excluding
# this script). The tracker must read NTFY_URL from the environment instead.
if grep -rInE "ntfy\.sh/[A-Za-z0-9_-]{8,}" . --exclude-dir=.git --exclude="$(basename "$0")" 2>/dev/null | grep -q .; then
  echo "!! a hardcoded ntfy topic URL is present in the tree — aborting."; grep -rInE "ntfy\.sh/[A-Za-z0-9_-]{8,}" . --exclude-dir=.git --exclude="$(basename "$0")"; exit 1
fi

# 1. Create the public repo (no-op if it already exists)
gh repo view "$OWNER/$REPO" >/dev/null 2>&1 || \
  gh repo create "$OWNER/$REPO" --public \
    -d "Garden Communities apartment tracker + Towers at Costa Verde availability map"

# 2. Replace local history with a single fresh commit
git add -A
git checkout --orphan _fresh_publish
git add -A
git commit -q -m "Apartment tracker + Towers at Costa Verde availability map"
git branch -D main 2>/dev/null || true
git branch -m main

# 3. Point origin at the repo and push the clean history
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/$OWNER/$REPO.git"
git push -u origin main --force

# 4. Let the Action commit availability.json (needs write token)
gh api -X PUT "repos/$OWNER/$REPO/actions/permissions/workflow" \
  -f default_workflow_permissions=write -F can_approve_pull_request_reviews=false >/dev/null 2>&1 || true

# 5. Enable Pages from main /docs
gh api -X POST "repos/$OWNER/$REPO/pages" -f "source[branch]=main" -f "source[path]=/docs" >/dev/null 2>&1 || \
gh api -X PUT  "repos/$OWNER/$REPO/pages" -f "source[branch]=main" -f "source[path]=/docs" >/dev/null 2>&1 || true

# 6. Kick off an immediate data refresh
gh workflow run "Update availability map" -R "$OWNER/$REPO" >/dev/null 2>&1 || true

echo
echo "Repo:  https://github.com/$OWNER/$REPO"
echo "Pages: https://$OWNER.github.io/$REPO/    (first build ~1-2 min)"
