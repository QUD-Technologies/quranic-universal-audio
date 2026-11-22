# HF Jobs — CLI and Python cheat sheet

Three submission surfaces. Same model. Pick the one that fits the caller.

## Submitting

### CLI

```bash
# UV script (local path is fine here — CLI uploads it)
hf jobs uv run train.py --flavor a10g-large --timeout 4h

# Inline UV command
hf jobs uv run python -c 'print("hi")'

# Extra deps + Python version (uv-style, before the script)
hf jobs uv run --with trl --python 3.12 train.py

# Override the base image (must have uv installed)
hf jobs uv run --image vllm/vllm-openai:latest --flavor a10g-large infer.py

# Remote UV script
hf jobs uv run https://raw.githubusercontent.com/huggingface/trl/main/trl/scripts/sft.py

# Plain Docker
hf jobs run python:3.12 python -c 'print("hi")'
hf jobs run --flavor a10g-small pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel \
    python -c "import torch; print(torch.cuda.get_device_name())"

# HF Space as the image
hf jobs run hf.co/spaces/lhoestq/duckdb duckdb -c "SELECT 'hi'"

# `--` separates job options from the inner command for clarity
hf jobs uv run --with trl-jobs -- trl-jobs sft --model_name Qwen/Qwen3-0.6B --dataset_name trl-lib/Capybara
```

### Python

```python
from huggingface_hub import run_job, run_uv_job, Volume, get_token

run_job(
    image="pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel",
    command=["python", "train.py"],
    flavor="a10g-large",
    timeout="4h",
    env={"WANDB_PROJECT": "quran-asr"},
    secrets={"HF_TOKEN": get_token()},  # NOT the literal "$HF_TOKEN"
    volumes=[Volume(type="bucket", source="<user>/asr-checkpoints", mount_path="/out")],
    namespace="<user>",
    labels={"task": "asr-train", "reciter": "ahmad_al_nufais"},
)

run_uv_job("train.py", flavor="a10g-small", dependencies=["trl"], timeout=7200)
```

### MCP (`hf_jobs`)

The tool the MCP server exposes in this Claude session.

```python
hf_jobs("uv", {
    "script": script_text_or_url,            # NOT a local path
    "flavor": "a10g-large",
    "timeout": "4h",
    "secrets": {"HF_TOKEN": "$HF_TOKEN"},    # MCP auto-replaces this
    "env": {"HF_XET_HIGH_PERFORMANCE": "1"},
    "dependencies": ["trl"],                 # extra deps beyond PEP 723 header
    "image": "vllm/vllm-openai:latest",      # optional base image override
    "python": "3.12",
    "script_args": ["--input", "x", "--output", "y"],
})

hf_jobs("run", {"image": "python:3.12", "command": ["python", "-c", "print('hi')"], "flavor": "cpu-basic"})
```

## Hardware (`hf jobs hardware`)

Flavors and rough $/hour (07/2025 listing — re-check `hf jobs hardware` for current prices):

| Flavor          | Specs                  | $/hr  |
|-----------------|------------------------|-------|
| `cpu-basic`     | 2 vCPU, 16 GB          | 0.01  |
| `cpu-upgrade`   | 8 vCPU, 32 GB          | 0.03  |
| `t4-small`      | 1× T4 16 GB            | 0.40  |
| `t4-medium`     | 1× T4 16 GB, more CPU  | 0.60  |
| `l4x1`          | 1× L4 24 GB            | 0.80  |
| `l4x4`          | 4× L4 96 GB            | 3.80  |
| `a10g-small`    | 1× A10G 24 GB          | 1.00  |
| `a10g-large`    | 1× A10G 24 GB, big CPU | 1.50  |
| `a10g-largex2`  | 2× A10G 48 GB          | 3.00  |
| `a10g-largex4`  | 4× A10G 96 GB          | 5.00  |
| `a100-large`    | 1× A100 80 GB          | 2.50  |
| `a100x4`        | 4× A100 320 GB         | 10.00 |
| `a100x8`        | 8× A100 640 GB         | 20.00 |
| `l40sx1`/`x4`/`x8` | L40S 48 GB each     | 1.80 / 8.30 / 23.50 |
| `v5e-1x1` / `2x2` / `2x4` | TPU v5e       | varies |

Selection heuristic: start one tier below what you think you need, watch `hf jobs stats`, scale up.

## Env vars and secrets

```bash
hf jobs uv run -e FOO=foo -e BAR=bar script.py
hf jobs uv run --env-file .env script.py
hf jobs uv run -s MY_SECRET=psswrd script.py             # inline secret
hf jobs uv run --secrets-file .env.secrets script.py
hf jobs uv run --secrets HF_TOKEN script.py              # forward local HF_TOKEN
```

Secrets are encrypted server-side and not visible in logs. Env vars are visible. Always prefer `--secrets` for tokens.

### Built-in env vars in the container

| Var           | Value                                    |
|---------------|------------------------------------------|
| `JOB_ID`      | Unique job id (matches the URL)          |
| `ACCELERATOR` | e.g. `a10g-small`, `none` for CPU jobs   |
| `CPU_CORES`   | int                                      |
| `MEMORY`      | e.g. `16Gi`                              |

Use `JOB_ID` to namespace outputs (`f"checkpoints/{os.environ['JOB_ID']}/"`) so parallel jobs don't clobber each other.

## Volume mounts

`-v hf://[TYPE/]SOURCE:/MOUNT_PATH[:ro]`

| Type             | Example                                              | RW |
|------------------|------------------------------------------------------|----|
| Model            | `hf://openai/gpt-oss-120b:/model`                    | ro |
| Dataset          | `hf://datasets/HuggingFaceFW/fineweb:/data`          | ro |
| Dataset subdir   | `hf://datasets/org/ds/train:/data`                   | ro |
| Bucket           | `hf://buckets/<user>/asr-checkpoints:/out`          | rw |
| Bucket read-only | `hf://buckets/<user>/audio-cache:/audio:ro`         | ro |

Multiple `-v` flags allowed. Python: `volumes=[Volume(type=..., source=..., mount_path=..., read_only=...)]`. Requires `huggingface_hub >= 1.8.0`.

## Timeout

`--timeout 30m | 1.5h | 1d | 7200` (number = seconds). Default 30 min. On timeout the job is killed immediately — unsaved work is lost. Add 20–30% buffer.

## Namespace and labels

```bash
hf jobs uv run --namespace my-org script.py
hf jobs uv run --label task=asr-train --label reciter=ahmad_al_nufais script.py
```

Repeating the same `key` overwrites. Filter later with `hf jobs ps --filter label=key=value`.

## Managing running jobs

```bash
hf jobs ps                                           # running only
hf jobs ps -a                                        # all (incl. completed/error)
hf jobs ps --filter status=running --filter "command=*train.py"
hf jobs ps --filter label=task=asr-train -a
hf jobs ps --filter status!=completed -a             # negation + glob supported
hf jobs stats                                        # live CPU/GPU/RAM/net for running jobs
hf jobs stats <job-id> [<job-id> ...]
hf jobs inspect <job-id>                             # full JSON: image, command, env keys, secrets list, status
hf jobs logs <job-id>                                # full log stream; pipe to `tail -n 50` for the tail
hf jobs cancel <job-id>
```

Python equivalents: `list_jobs()`, `inspect_job(job_id=...)`, `fetch_job_logs(job_id=...)` (iterator), `fetch_job_metrics(job_id=...)`, `cancel_job(job_id=...)`. All accept `namespace=`.

## Scheduled jobs

CRON or aliases (`@hourly`, `@daily`, `@weekly`, `@monthly`, `@yearly`).

```bash
# CLI form not yet exposed for "scheduled"; use Python or MCP
```

```python
from huggingface_hub import (
    create_scheduled_job, create_scheduled_uv_job,
    list_scheduled_jobs, inspect_scheduled_job,
    suspend_scheduled_job, resume_scheduled_job, delete_scheduled_job,
)

create_scheduled_uv_job("refresh.py", schedule="@daily", flavor="cpu-upgrade",
                        secrets={"HF_TOKEN": get_token()})
create_scheduled_job(image="python:3.12", command=["python", "ping.py"],
                     schedule="*/15 * * * *")
```

MCP form: `hf_jobs("scheduled uv", {...})`, `hf_jobs("scheduled run", {...})`, `hf_jobs("scheduled ps")`, `hf_jobs("scheduled inspect"|"suspend"|"resume"|"delete", {"job_id": "..."})`.

## Webhooks → jobs

```python
from huggingface_hub import create_webhook
create_webhook(
    job_id=job.id,
    watched=[{"type": "user", "name": "<user>"}],
    domains=["repo"],
    secret="...",
)
```

Job runs with the event JSON in `WEBHOOK_PAYLOAD`. Useful for "rerun extraction when X dataset gets new revisions."

## Debugging a failed job

1. `hf jobs logs <id> | tail -n 50` — find the traceback.
2. `hf jobs inspect <id>` — verify the secrets/env you expected actually arrived (only key names are shown; values stay encrypted).
3. Reproduce locally with the equivalent command — `hf jobs uv run …` ↔ `uv run …`, `hf jobs run …` ↔ `docker run …`.
4. Status `Job timeout` → bump `--timeout` and resubmit.
5. 401/403 → `--secrets HF_TOKEN` missing, token lacks scope, or free-tier account (Jobs require Pro/Team/Enterprise).
