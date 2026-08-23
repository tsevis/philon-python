#!/bin/zsh
# Point this clone's hooks at the ones tracked in the repository.
#
# `.git/hooks` is not version-controlled, so a tracked directory plus one
# `core.hooksPath` setting is how a hook reaches every clone that asks for it.
# It is opt-in on purpose: a hook that installs itself is a hook that surprises
# someone.
#
#   zsh scripts/install-git-hooks.sh            install
#   zsh scripts/install-git-hooks.sh --uninstall  put it back as it was
set -euo pipefail

ROOT_DIR="${0:A:h:h}"
cd "${ROOT_DIR}"
HOOKS="scripts/git-hooks"

if [[ "${1:-}" == "--uninstall" ]]; then
  git config --unset core.hooksPath || true
  echo "Removed core.hooksPath. This clone is back to .git/hooks."
  exit 0
fi

# core.hooksPath REPLACES .git/hooks rather than adding to it, so anything
# already installed there would stop firing. Say so rather than silently
# disabling someone's work.
existing=$(ls .git/hooks 2>/dev/null | grep -v '\.sample$' || true)
if [[ -n "${existing}" ]] && [[ -z "$(git config --get core.hooksPath || true)" ]]; then
  echo "This clone already has hooks in .git/hooks, which core.hooksPath would replace:" >&2
  echo "${existing}" | sed 's/^/  /' >&2
  echo "Move or merge them into ${HOOKS}/ first, then run this again." >&2
  exit 1
fi

chmod +x "${HOOKS}"/*
git config core.hooksPath "${HOOKS}"
echo "Hooks installed from ${HOOKS}: $(ls "${HOOKS}" | tr '\n' ' ')"
echo "Engine parity is now checked on every commit. Skip one with --no-verify."
