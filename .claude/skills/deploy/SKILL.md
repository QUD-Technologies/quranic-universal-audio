---
name: deploy
description: Deploy any branch to its HF Space directly from the CLI (local, no push) and monitor until the Space is live.
---

# deploy

Deploy the Inspector to its Hugging Face Space straight from the **current checkout** — no git push, no GH-Actions runner. The deploy stages the git-tracked tree, uploads it to the Space repo, and factory-reboots the Space, so it reflects the committed state of whatever branch/worktree you run it from. Default target is the **dev** Space; pass `prod` to target prod.

## Run

1. **Deploy** (foreground, ~15-30s to upload + trigger the rebuild):

   ```
   bash .claude/skills/deploy/scripts/deploy.sh dev
   ```

2. **Monitor** readiness as a **background** Bash job (`run_in_background: true`) so you're notified when the Space is actually live — it polls the HF Space runtime stage until `RUNNING` **and** `/healthz` returns 200, not just when the upload finished:

   ```
   python .claude/skills/deploy/scripts/wait_space.py dev
   ```

   Exits 0 when RUNNING + healthy, non-zero on `BUILD_ERROR` / `RUNTIME_ERROR` / a paused-or-stopped Space / timeout.

## Notes

- **Local — no remote branch required.** It stages the tracked files of the checkout you run it from; run it from the worktree on the branch you want live. Untracked files are not staged.
- Targets: `dev` → `QUD-Technologies/quranic-inspector-dev`, `prod` → `QUD-Technologies/quranic-universal-audio`.
- CI path instead (runs the full check suite, deploys a pushed ref): `gh workflow run inspector-deploy.yml --ref <branch> -f env=dev`.
