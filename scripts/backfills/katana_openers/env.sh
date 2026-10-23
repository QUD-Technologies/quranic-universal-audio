# Sourced on Katana: the opener job's env (the retime engine's).
R=/srv/scratch/z5344896/qua_runs/openers
REPO=$R/repo
export PATH=/srv/scratch/z5344896/envs/qua313/bin:$PATH
PYTHONPATH=""
for src in packages/sdk packages/quran-domain packages/contracts engines/timing-batch; do
    PYTHONPATH="$REPO/$src/src${PYTHONPATH:+:$PYTHONPATH}"
done
export PYTHONPATH PYTHONUNBUFFERED=1
module load ffmpeg/7.0.2 2>/dev/null || module load ffmpeg 2>/dev/null || true
export HF_TOKEN="$(</srv/scratch/z5344896/.hf_token)"
export HF_HOME=/srv/scratch/z5344896/cache/hf
export QUA_SDK_CACHE_DIR=/srv/scratch/z5344896/qua_runs/native-forms/2026-10-09/cache
