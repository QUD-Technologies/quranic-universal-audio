#!/usr/bin/env bash
#
# First-time local setup — install the toolchain deps so the tests, typecheck,
# lint, and build run. Idempotent: safe to re-run. The dev/CI container is
# ephemeral (fresh clone, no node_modules / site-packages), so run this once per
# checkout before `npm run test` or pytest.
#
#   scripts/devenv/setup.sh             # frontend + backend (default)
#   scripts/devenv/setup.sh frontend    # just the FE (npm) deps
#   scripts/devenv/setup.sh backend     # just the BE (pip) deps
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
target="${1:-all}"

setup_frontend() {
    echo "==> Frontend deps (inspector/frontend): npm ci"
    # `npm ci` installs EXACTLY the lockfile versions (vitest 3.x, vite 5, the
    # svelte plugin) — unlike `npm install`, which can drift. Afterwards use the
    # repo's pinned scripts: `npm run test` / check / lint / build, NOT
    # `npx vitest` or `npm exec -- vitest` (those can resolve a mismatched global
    # vitest that fails to transform .svelte files).
    npm --prefix "$ROOT/inspector/frontend" ci
}

setup_hooks() {
    # Install the repo-ROOT deps (husky + lint-staged) so the root `prepare`
    # script runs `husky`, which sets `core.hooksPath=.husky` and arms the
    # committed pre-commit (lint-staged) + pre-push (ruff + pyright) hooks.
    # Without this the hooks never fire in a fresh checkout. Best-effort: a
    # missing npm or offline box must not abort the rest of setup.
    echo "==> Git hooks (repo root): npm install (activates husky)"
    if ! npm --prefix "$ROOT" install; then
        echo "   WARN: root 'npm install' failed; git hooks not armed" \
             "(pre-commit/pre-push won't run). Re-run once npm is available." >&2
    fi
}

setup_backend() {
    echo "==> Backend deps (inspector): pip install requirements + dev"
    local reqs=(-r "$ROOT/inspector/requirements.txt" -r "$ROOT/inspector/requirements-dev.txt")
    if ! python3 -m pip install "${reqs[@]}"; then
        # Some base images ship distro-managed packages (e.g. debian's blinker)
        # that pip can't uninstall to satisfy a version bump ("RECORD file not
        # found"). --ignore-installed installs fresh copies that shadow them.
        echo "   pip conflict (distro-managed package?); retrying --ignore-installed ..."
        python3 -m pip install --ignore-installed "${reqs[@]}"
    fi
    # talqeen-quran-data + its pack are the Hafs reference data (DK v2 words, ayah
    # ends, surah metadata). Required: needs TALQEEN_DATA_READ_TOKEN, GH_TOKEN or a
    # gh login for the private data repo.
    echo "==> Quran data (required): talqeen-quran-data SDK + pack"
    python3 "$ROOT/scripts/devenv/install_quran_data.py"
    # qua-domain is the multi-riwayah edition data (scripts, fonts, coordinate
    # indexes, Hafs->target projection) from the private monorepo. Optional by
    # design: without a credential this installs nothing and the Inspector runs
    # Hafs-only. Never let it abort setup.
    echo "==> Edition data (optional): qua-domain"
    python3 "$ROOT/scripts/devenv/install_qua_domain.py" ||         echo "   WARN: qua-domain install failed; running Hafs-only." >&2
}

case "$target" in
    all)         setup_frontend; setup_hooks; setup_backend ;;
    frontend|fe) setup_frontend; setup_hooks ;;
    backend|be)  setup_backend ;;
    *) echo "usage: setup.sh [all|frontend|backend]" >&2; exit 2 ;;
esac

cat <<'EOF'

Setup complete. Run with the PINNED scripts (not npx / npm exec):
  cd inspector/frontend && npm run test    # vitest — also: npm run check / lint / build
  cd inspector && python -m pytest tests/ -v
EOF
