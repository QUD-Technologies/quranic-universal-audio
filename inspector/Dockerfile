# syntax=docker/dockerfile:1.7
# Inspector — three-stage build targeting a small runtime image.
# Stage 0 fetches the pinned Talqeen data (SDKs, pack, fonts).
# Stage 1 builds the Vite frontend with Node.
# Stage 2 compiles a minimal static ffmpeg (mp3→PCM only) so the runtime
#   doesn't ship the ~130MB video codec chain (x265/aom/SvtAv1Enc/jxl/vpx)
#   that distro ffmpeg packages pull in but inspector never uses.
# Stage 3 is a lean python:3.11-alpine runtime that ships the Flask app.
# Reviewers run `docker compose up` and visit :5000.
#
# Build context is the REPO ROOT (one level above inspector/). `qua_shared/`
# ships alongside `inspector/` so the in-process imports of
# `qua_shared.timestamps_pipeline` (timestamps validator) and similar resolve.
#
# Build:    docker build -t inspector:dev -f inspector/Dockerfile .   # from repo root
# Run dev:  docker run --rm -p 5000:5000 -v "$PWD/data:/data" inspector:dev

# --- Stage 0: talqeen-quran-data SDK wheel, pack, fonts + TS SDK tarball (required) ---
# The Hafs words (Digital Khatt v2), ayah-end markers and surah metadata come from
# the Talqeen data pack through its SDK, both fetched from the PRIVATE
# QUD-Technologies/talqeen-ai-data repo at qua_shared/quran_data_pin.py's revision
# (the installer reads the pin, so there is no second copy of it here). Unlike
# qua-domain this is not optional: the Inspector has no Hafs text without it, so a
# build without the TALQEEN_DATA_READ_TOKEN secret fails here. The same installer
# fetches the release assets the frontend needs: the pack's fonts (/pack/fonts,
# served by /api/static/quran-data/) and the TS SDK tarball (/ts, installed by
# the frontend stage's npm ci), each sha256-checked against the pin. This stage
# comes first because the frontend stage copies from it.
FROM python:3.11-alpine AS quran-data-build
RUN apk add --no-cache git
WORKDIR /build
COPY qua_shared/__init__.py qua_shared/quran_data_pin.py ./qua_shared/
COPY scripts/devenv/install_quran_data.py ./scripts/devenv/
RUN --mount=type=secret,id=TALQEEN_DATA_READ_TOKEN,required=true \
    TALQEEN_DATA_READ_TOKEN="$(cat /run/secrets/TALQEEN_DATA_READ_TOKEN)" \
      python scripts/devenv/install_quran_data.py --wheel-dir /wheels --pack-dir /pack --ts-dir /ts && \
    rm -rf /root/.cache


# --- Stage 1: build frontend ---
FROM node:24-alpine AS frontend-build
WORKDIR /app
RUN apk add --no-cache git openssh-client
# Copy the full frontend before `npm ci`: the root `postinstall` hook runs
# `paraglide:compile`, which needs `project.inlang/` + the message sources.
COPY inspector/frontend ./
# @qud-technologies/quran-data (the Mushaf view's data SDK) installs from the
# release tarball stage 0 fetched; package.json points at .quran-data/.
COPY --from=quran-data-build /ts/ ./.quran-data/
RUN --mount=type=secret,id=PRIVATE_REPO_TOKEN,required=false \
    if [ -s /run/secrets/PRIVATE_REPO_TOKEN ]; then \
      TOKEN="$(cat /run/secrets/PRIVATE_REPO_TOKEN)" && \
      AUTH="$(printf 'x-access-token:%s' "$TOKEN" | base64 -w0)" && \
      git config --global http.https://github.com/.extraheader "AUTHORIZATION: basic $AUTH"; \
    fi && \
    node vendor/restore.mjs && \
    npm ci && \
    rm -f /root/.gitconfig
RUN npm run build


# --- Stage 1b: build the qua-domain wheel from the pinned monorepo commit ---
# qua-domain carries the four riwayat's exact scripts, coordinate indexes,
# fonts, and the Hafs->target word projection. It is a package inside the
# PRIVATE QUD-Technologies/qua monorepo, so it is fetched with a read-only SSH deploy key
# — the same arrangement stage 1 uses for the renderer package.
#
# `required=false` is load-bearing: a fork, or any build without the secret,
# still produces a working image. The wheel is simply absent and the Inspector
# runs Hafs-only (services/reference/editions.available() is False; a non-Hafs
# delivery fails loudly rather than rendering under Hafs coordinates).
FROM python:3.11-alpine AS qua-domain-build
RUN apk add --no-cache git openssh-client
WORKDIR /build
# QUA_COMMIT must match qua_shared/qua_domain_pin.py — HF Spaces build with no
# build args, so this literal IS the pin on the Space. A test asserts they agree.
ARG QUA_COMMIT=29e073322f8197f2af5ecdb95d09780d6655bec7
# -s, not -f on the secret: buildx mounts a secret that resolved to "" as an
# EMPTY FILE THAT EXISTS, so -f is true on a fork PR and the build dies on git
# auth instead of degrading to the Hafs-only image.
#
# The key is normalised rather than copied: OpenSSH refuses a private key with
# CRLF endings or no trailing newline ("error in libcrypto: unsupported"),
# offers no identity, and the fetch then fails as "Permission denied
# (publickey)" — which reads like an unauthorized key rather than a mangled
# one. A CI secret round-trip is exactly where that mangling happens.
RUN --mount=type=secret,id=PRIVATE_REPO_TOKEN,required=false \
    if [ -s /run/secrets/PRIVATE_REPO_TOKEN ]; then \
      TOKEN="$(cat /run/secrets/PRIVATE_REPO_TOKEN)" && \
      git config --global url."https://x-access-token:${TOKEN}@github.com/".insteadOf git@github.com: && \
      git init --quiet src && cd src && \
      git remote add origin git@github.com:QUD-Technologies/qua.git && \
      git sparse-checkout set --cone packages/quran-domain && \
      git fetch --quiet --depth 1 --filter=blob:none origin "$QUA_COMMIT" && \
      git checkout --quiet FETCH_HEAD && \
      pip wheel --no-deps --no-cache-dir --wheel-dir /wheels ./packages/quran-domain && \
      cd / && rm -rf /root/.gitconfig /build/src; \
    else \
      echo "PRIVATE_REPO_TOKEN absent or empty — building Hafs-only (no qua-domain)"; \
    fi && \
    mkdir -p /wheels


# --- Stage 2: compile minimal static ffmpeg (mp3 decode + s16le/wav output) ---
# Alpine 3.23 is current stable (released Apr 2026, supported until 2028).
# Note on libmp3lame: Alpine dropped the `lame-static` subpackage across all
# current branches (verified 3.20 / 3.22 / 3.23) and `lame-dev` ships only the
# dynamic `.so` — no static archive. ffmpeg's `--extra-ldflags="-static"` link
# step then silently drops libmp3lame and the resulting binary has the `mp3`
# muxer but no `libmp3lame` encoder, which makes `segment-clip`'s `-c:a
# libmp3lame` invocation fail with "Default encoder for format mp3 ... is
# probably disabled. Error selecting an encoder" and the route returns
# 200 OK / 0 bytes (browser hangs). Solution: build libmp3lame from source
# with `--enable-static --disable-shared` so the `.a` is present at link time.
FROM alpine:3.23 AS ffmpeg-build
RUN apk add --no-cache build-base yasm nasm curl tar xz \
                       openssl-dev openssl-libs-static zlib-dev zlib-static

# Build libmp3lame 3.100 statically. Source is ~3 MB; compiles in <30 s. The
# .a lands at /usr/lib/libmp3lame.a so ffmpeg's `--extra-libs="-lmp3lame"` +
# `--extra-ldflags="-static"` find it.
ARG LAME_VERSION=3.100
RUN curl -fsSL "https://downloads.sourceforge.net/project/lame/lame/${LAME_VERSION}/lame-${LAME_VERSION}.tar.gz" \
        | tar -xz -C /tmp && \
    cd /tmp/lame-${LAME_VERSION} && \
    ./configure --prefix=/usr --enable-static --disable-shared --disable-frontend && \
    make -j"$(nproc)" && make install && \
    cd / && rm -rf /tmp/lame-${LAME_VERSION}

ARG FFMPEG_VERSION=7.1
# ffmpeg.org resets connections from the HF builders; the GitHub mirror of the
# release tag is the same source tree.
RUN mkdir -p /tmp/ffmpeg-${FFMPEG_VERSION} && \
    curl -fsSL --retry 5 --retry-all-errors \
      "https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n${FFMPEG_VERSION}.tar.gz" \
    | tar -xz -C /tmp/ffmpeg-${FFMPEG_VERSION} --strip-components=1
WORKDIR /tmp/ffmpeg-${FFMPEG_VERSION}
# --disable-everything then whitelist only what services/peaks.py +
# routes/audio/clip.py use:
#   decoder/demuxer: mp3 input
#   encoder/muxer:   pcm_s16le + wav output for peaks (s16le muxer is silently
#                    dropped unless pcm_s16le muxer is listed alongside in
#                    ffmpeg 7.1) AND libmp3lame + mp3 muxer for clip.py
#                    (segment-clip route re-encodes the requested window to
#                    mono 96k mp3 — without libmp3lame the route returns
#                    200 OK / 0 bytes silently and browser playback hangs)
#   filter:          aresample (for `-ar` sample-rate conversion), aformat, anull
#   protocol:        file + pipe (local I/O) + http,https,tcp,tls (CDN reads —
#                    lets `_ffmpeg_decode_segment` decode remote-only chapters
#                    directly via HTTP Range without the Python ID3v2 + byte-
#                    arithmetic workaround. Costs ~8.5 MB statically-linked
#                    OpenSSL, measured.)
# ffprobe is built alongside ffmpeg; needed by services/peaks.py stream probe.
RUN ./configure \
      --prefix=/ff \
      --disable-everything \
      --disable-autodetect \
      --disable-doc --disable-htmlpages --disable-manpages --disable-podpages --disable-txtpages \
      --disable-debug --disable-ffplay \
      --disable-shared --enable-static \
      --enable-small \
      --enable-openssl \
      --enable-libmp3lame \
      --enable-decoder=mp3,mp3float,pcm_s16le \
      --enable-demuxer=mp3,wav,s16le \
      --enable-encoder=libmp3lame,pcm_s16le \
      --enable-muxer=mp3,s16le,wav,pcm_s16le \
      --enable-filter=aresample,aformat,anull \
      --enable-protocol=file,pipe,http,https,tcp,tls \
      --enable-parser=mpegaudio \
      --extra-ldflags="-static" \
      --extra-libs="-lmp3lame" \
      --pkg-config-flags="--static" && \
    make -j"$(nproc)" && make install && \
    strip /ff/bin/ffmpeg /ff/bin/ffprobe


# --- Stage 3: Python runtime ---
FROM python:3.11-alpine AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    INSPECTOR_DATA_DIR=/app/data \
    INSPECTOR_QUA_DATA_PATH=/app/data \
    INSPECTOR_BUCKET_MOUNT=/data/inspector-bucket \
    INSPECTOR_BUCKET_REPO=QUD-Technologies/quranic-inspector-bucket-dev \
    INSPECTOR_PARSED_CACHE_BYTES=134217728 \
    INSPECTOR_BEHIND_PROXY=1 \
    INSPECTOR_AUTO_DETECT=1 \
    INSPECTOR_AUTOMATIONS=1 \
    GUNICORN_WORKERS=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY --from=ffmpeg-build /ff/bin/ffmpeg /ff/bin/ffprobe /usr/local/bin/

# Python deps — single source of truth in inspector/requirements.txt.
# pip/setuptools/wheel are purged — not needed at runtime.
COPY inspector/requirements.txt ./inspector/requirements.txt
# The qua-domain wheel (stage 1b) is an OPTIONAL 11.7 MB of edition data. Stage
# 1b always creates /wheels, so this directory copy succeeds whether or not the
# build had a deploy key. An absent wheel is a Hafs-only image; a wheel that is
# present and will not install is a build failure, not a silent downgrade.
#
# The wheel is KEPT at /opt/wheels after the install: HF Jobs run on a stock
# python image, so services/admin/jobs/base.py stages it to the aligner bucket
# and every job pip-installs it — a non-Hafs publish/cut needs the same
# projection the Space uses. INSPECTOR_QUA_DOMAIN_WHEEL_DIR points there.
COPY --from=qua-domain-build /wheels/ /opt/wheels/
# talqeen-quran-data (stage 0), installed with its dependencies (flatbuffers,
# brotli) from PyPI; the built wheel needs no credentials.
COPY --from=quran-data-build /wheels/ /opt/talqeen/
RUN pip install --no-cache-dir -r ./inspector/requirements.txt && \
    pip install --no-cache-dir /opt/talqeen/talqeen_quran_data-*.whl && \
    rm -rf /opt/talqeen && \
    if ls /opt/wheels/qua_domain-*.whl >/dev/null 2>&1; then \
      pip install --no-cache-dir --no-deps /opt/wheels/qua_domain-*.whl; \
    else \
      echo "no qua-domain wheel — Hafs-only image"; \
    fi && \
    pip uninstall -y pip setuptools wheel && \
    find /usr/local/lib/python3.11 -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# App code. Selective COPY — inspector/frontend/{src,public,node config} and
# tests/ aren't needed at runtime (only the built dist/ from stage 1 is).
# app.py inserts /app (repo root) onto sys.path[0] at startup so that
# `from qua_shared.X import Y` resolves.
COPY inspector/app.py inspector/config.py inspector/constants.py ./inspector/
COPY inspector/adapters/ ./inspector/adapters/
COPY inspector/domain/ ./inspector/domain/
COPY inspector/routes/ ./inspector/routes/
COPY inspector/services/ ./inspector/services/
# Native report code and its guarded migration must ship atomically. The
# runtime schema assertion also catches a stale image, while this build check
# catches an incomplete Docker context before deployment.
RUN test -f ./inspector/services/db/migrations/0028_ts_reports_native_targets.sql
COPY inspector/utils/ ./inspector/utils/
# qua_shared/ is shared with the CI build pipeline. Inspector imports
# ``qua_shared.timestamps_shards`` for ``SCHEMA_VERSION`` + ``derive_url_template``
# when composing the bucket-mode manifest. ``qua_jobs/`` ships the HF-Job
# entrypoints (publish_hf / publish_hf_batch / cut_release / shard) that
# ``services/admin/jobs/base.stage_job_code`` uploads to the aligner-bucket on
# launch. ``.github/config/repo.yml``, ``docs/templates/`` (release-body +
# dataset-card render-templates) + ``LICENSE`` / ``LICENSE-DATA`` are read by config_loader (the
# inspector release preview + the job-side cut_release/publish_hf renderers).
COPY qua_shared/ ./qua_shared/
COPY qua_jobs/ ./qua_jobs/
COPY .github/config/ ./.github/config/
COPY docs/templates/ ./docs/templates/
COPY LICENSE ./LICENSE
COPY LICENSE-DATA ./LICENSE-DATA
# The on-disk data surface in deployed mode: phoneme_sub_costs.json (read via
# config.py's _QUA_DATA; INSPECTOR_QUA_DATA_PATH=/app/data) and the Quran data
# pack from stage 0, which qua_shared.quran_text opens at /app/data. Everything
# else (state, catalog, audit, per-reciter reciters/) lives in the bucket mounted
# at /data/inspector-bucket. The pack comes from the build stage, not the build
# context, so no binary has to survive the Space's LFS handling.
COPY data/phoneme_sub_costs.json ./data/
COPY --from=quran-data-build /pack/quran-core.qref ./data/quran-core.qref
# The pack's fonts (stage 0): served to the browser by /api/static/quran-data/;
# DigitalKhattV2.otf is also the font the release jobs publish (stage_job_code
# checks data/fonts/DigitalKhattV2.otf before a launch).
COPY --from=quran-data-build /pack/fonts/ ./data/fonts/
# The justified-line drawings and glyph outlines (stage 0): served brotli-encoded by
# /api/static/quran-data/justify/; without them the Mushaf view falls back to plain lines.
COPY --from=quran-data-build /pack/justify/ ./data/justify/
COPY --from=frontend-build /app/dist ./inspector/frontend/dist
# Cross-stage COPY preserves the source modes; cached Vite output can contain
# owner-only directories on the HF builder. The runtime user must be able to
# traverse and read the static tree.
RUN chmod -R a+rX ./inspector/frontend

# Data volume is mounted here at runtime (see docker-compose.yml). Inside the
# container every data path derives from DATA_DIR via inspector/config.py.
# Run as non-root. uid/gid 1000 matches the common host-user convention so
# bind-mounted /data stays readable without chown gymnastics. The inspector
# cache dir (see config.py) needs to be writable by this user.
RUN addgroup -g 1000 -S inspector && \
    adduser -u 1000 -S -G inspector -D inspector && \
    chown -R inspector:inspector /app /home/inspector
USER inspector

EXPOSE 7860

# Production WSGI server. -w 1 is load-bearing — state_store, per-slug
# mutex, signed-cookie session, and the role cache all assume one process.
# The app asserts at import time and refuses to boot on any multi-worker
# signal.
#
# --threads 16: thread pool sized for CPU-basic. The parsed-cache + bucket-
#   read workload is mostly I/O-bound.
# No --max-requests: a recycle kills the in-process background threads
#   (timestamps runs, align pipeline), and one bulk client can trip it in
#   minutes. Watch the Space's memory if a slow leak ever shows up.
# --timeout 60: peaks computation can take ~30 s on cold cache.
# --graceful-timeout 30: allows in-flight saves to finish on SIGTERM.
CMD ["gunicorn", "-k", "gthread", "-w", "1", "--threads", "16", \
\
     "--timeout", "60", "--graceful-timeout", "30", \
     "--bind", "0.0.0.0:7860", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "--chdir", "/app/inspector", \
     "app:app"]
