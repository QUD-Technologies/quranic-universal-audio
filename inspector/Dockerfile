# syntax=docker/dockerfile:1.7
# Inspector — three-stage build targeting a small runtime image.
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

# --- Stage 1: build frontend ---
FROM node:24-alpine AS frontend-build
WORKDIR /app
RUN apk add --no-cache git openssh-client
# Copy the full frontend before `npm ci`: the root `postinstall` hook runs
# `paraglide:compile`, which needs `project.inlang/` + the message sources.
COPY inspector/frontend ./
# HF Spaces leaves binary font transports as unsmudged Git-LFS pointers in the
# Docker context. The compressed font is therefore committed as base64 text.
# Strip certutil's two envelope lines, decode, and require real OpenType bytes
# before Vite copies public/ into dist/.
RUN sed '1d;$d' public/fonts/DigitalKhattV2.otf.gz.b64 | \
      base64 -d | gzip -dc > public/fonts/DigitalKhattV2.otf && \
    [ "$(head -c 4 public/fonts/DigitalKhattV2.otf)" = "OTTO" ]
RUN --mount=type=ssh,required=false \
    --mount=type=secret,id=CELLS_DEPLOY_KEY,required=false \
    mkdir -p -m 700 /root/.ssh && \
    if [ -f /run/secrets/CELLS_DEPLOY_KEY ]; then \
      cp /run/secrets/CELLS_DEPLOY_KEY /root/.ssh/id_ed25519 && \
      chmod 600 /root/.ssh/id_ed25519; \
    fi && \
    ssh-keyscan -H github.com >> /root/.ssh/known_hosts && \
    npm ci && \
    rm -f /root/.ssh/id_ed25519
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
RUN --mount=type=secret,id=QUA_DOMAIN_DEPLOY_KEY,required=false \
    mkdir -p -m 700 /root/.ssh && \
    if [ -s /run/secrets/QUA_DOMAIN_DEPLOY_KEY ]; then \
      tr -d '\r' < /run/secrets/QUA_DOMAIN_DEPLOY_KEY > /root/.ssh/id_ed25519 && \
      printf '\n' >> /root/.ssh/id_ed25519 && \
      chmod 600 /root/.ssh/id_ed25519 && \
      ssh-keyscan -H github.com >> /root/.ssh/known_hosts && \
      git init --quiet src && cd src && \
      git remote add origin git@github.com:QUD-Technologies/qua.git && \
      git sparse-checkout set --cone packages/quran-domain && \
      git fetch --quiet --depth 1 --filter=blob:none origin "$QUA_COMMIT" && \
      git checkout --quiet FETCH_HEAD && \
      pip wheel --no-deps --no-cache-dir --wheel-dir /wheels ./packages/quran-domain && \
      cd / && rm -rf /root/.ssh /build/src; \
    else \
      echo "QUA_DOMAIN_DEPLOY_KEY absent or empty — building Hafs-only (no qua-domain)"; \
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
RUN curl -fsSL "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" | tar -xJ -C /tmp
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
    INSPECTOR_BUCKET_REPO=hetchyy/quranic-inspector-bucket-dev \
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
RUN pip install --no-cache-dir -r ./inspector/requirements.txt && \
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
# dataset-card render-templates) + ``LICENSE`` are read by config_loader (the
# inspector release preview + the job-side cut_release/publish_hf renderers).
COPY qua_shared/ ./qua_shared/
COPY qua_jobs/ ./qua_jobs/
COPY .github/config/ ./.github/config/
COPY docs/templates/ ./docs/templates/
COPY LICENSE ./LICENSE
# Linguistic data read by services/data_loader.py via config.py's _QUA_DATA,
# plus surah_info.json read by services/data_loader.py at startup. These four
# files are the entire on-disk data surface in deployed mode — everything else
# (state, catalog, audit, per-reciter reciters/) lives in the bucket
# mounted at /data/inspector-bucket. INSPECTOR_QUA_DATA_PATH=/app/data tells
# config.py to look here instead of the default sibling-project path.
# qpc_hafs.json (~12 MB) still serves the Inspector's internal Quran-data path
# and ships gzipped because HF Spaces auto-LFS-promote
# files >10 MB at push time and the Space build doesn't smudge LFS — the
# uncompressed source would land as a 133-byte pointer. upload_inspector.py
# pre-gzips at stage time; the runtime data loader gunzips it on read.
COPY data/surah_info.json \
     data/qpc_hafs.json.gz \
     data/digital_khatt_v2_script.json \
     data/phoneme_sub_costs.json \
     ./data/
# The release jobs publish this exact font and stage_job_code validates the
# source path before launch. Keep it alongside the built frontend asset.
COPY --chmod=0644 inspector/frontend/public/fonts/DigitalKhattV2.otf.gz.b64 \
     ./inspector/frontend/public/fonts/DigitalKhattV2.otf.gz.b64
# Decode the text transport and fail if it is missing/malformed. stage_job_code
# must upload the real font bytes before an HF publish can launch.
RUN sed '1d;$d' ./inspector/frontend/public/fonts/DigitalKhattV2.otf.gz.b64 | \
      base64 -d | gzip -dc > ./inspector/frontend/public/fonts/DigitalKhattV2.otf && \
    rm ./inspector/frontend/public/fonts/DigitalKhattV2.otf.gz.b64 && \
    test -s ./inspector/frontend/public/fonts/DigitalKhattV2.otf && \
    [ "$(head -c 4 ./inspector/frontend/public/fonts/DigitalKhattV2.otf)" = "OTTO" ] && \
    ! head -c 40 ./inspector/frontend/public/fonts/DigitalKhattV2.otf | grep -q "version https://git-lfs"
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
# --max-requests + jitter: rolling worker recycle bounds slow memory leaks.
# --timeout 60: peaks computation can take ~30 s on cold cache.
# --graceful-timeout 30: allows in-flight saves to finish on SIGTERM.
CMD ["gunicorn", "-k", "gthread", "-w", "1", "--threads", "16", \
     "--max-requests", "5000", "--max-requests-jitter", "500", \
     "--timeout", "60", "--graceful-timeout", "30", \
     "--bind", "0.0.0.0:7860", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "--chdir", "/app/inspector", \
     "app:app"]
