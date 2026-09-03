# Build openclaw from source to avoid npm packaging gaps (some dist files are not shipped).
FROM node:22-bookworm AS openclaw-build

# Dependencies needed for openclaw build
RUN apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    curl \
    python3 \
    make \
    g++ \
  && rm -rf /var/lib/apt/lists/*

# Install Bun (openclaw build uses it)
RUN curl -fsSL https://bun.sh/install | bash
ENV PATH="/root/.bun/bin:${PATH}"

# Pin pnpm in the build stage as well. Without this, Corepack may download
# the latest pnpm release, making OpenClaw source builds slower and less stable.
RUN corepack enable && corepack prepare pnpm@10.23.0 --activate

WORKDIR /openclaw

# Pin to a known-good ref (tag/branch). Override in Railway template settings if needed.
ARG OPENCLAW_GIT_REF=v2026.5.22
RUN git clone --depth 1 --branch "${OPENCLAW_GIT_REF}" https://github.com/openclaw/openclaw.git .

# Disable pnpm minimum release age gate for OpenClaw source build.
# OpenClaw's workspace config can override global pnpm config, so patch both the
# project config and pnpm-workspace.yaml before running pnpm install.
RUN set -eux; \
  pnpm config set minimumReleaseAge 0 --location project || true; \
  if [ -f pnpm-workspace.yaml ]; then \
    sed -i -E 's/^([[:space:]]*minimumReleaseAge:).*/\1 0/' pnpm-workspace.yaml; \
    grep -n "minimumReleaseAge" pnpm-workspace.yaml || true; \
  fi

# Patch: relax version requirements for packages that may reference unpublished versions.
RUN set -eux; \
  find ./extensions -name 'package.json' -type f | while read -r f; do \
    sed -i -E 's/"openclaw"[[:space:]]*:[[:space:]]*">=[^"]+"/"openclaw": "*"/g' "$f"; \
    sed -i -E 's/"openclaw"[[:space:]]*:[[:space:]]*"workspace:[^"]+"/"openclaw": "*"/g' "$f"; \
  done

RUN pnpm install --no-frozen-lockfile
RUN pnpm build
ENV OPENCLAW_PREFER_PNPM=1
RUN pnpm ui:install && pnpm ui:build


# Runtime image
FROM node:22-bookworm
ENV NODE_ENV=production

# Runtime system dependencies:
# - python3 / python3-pip: required by Python-based skills such as imou-device-video
# - ffmpeg: provides ffmpeg and ffprobe for HLS/audio extraction
RUN apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates \
    tini \
    python3 \
    python3-pip \
    python3-venv \
    ffmpeg \
  && rm -rf /var/lib/apt/lists/*

# `openclaw update` expects pnpm. Provide it in the runtime image.
RUN corepack enable && corepack prepare pnpm@10.23.0 --activate

# Persist user-installed tools by default by targeting the Railway volume.
ENV NPM_CONFIG_PREFIX=/data/npm
ENV NPM_CONFIG_CACHE=/data/npm-cache
ENV PNPM_HOME=/data/pnpm
ENV PNPM_STORE_DIR=/data/pnpm-store
ENV PATH="/data/npm/bin:/data/pnpm:${PATH}"

WORKDIR /app

# Wrapper deps
COPY package.json package-lock.json* ./
RUN npm install --omit=dev && npm cache clean --force

# Python dependencies for skills and the PROROK read-only API
COPY requirements.txt* ./
RUN if [ -f requirements.txt ]; then \
      pip3 install --break-system-packages --no-cache-dir -r requirements.txt; \
    fi

# Copy built openclaw
COPY --from=openclaw-build /openclaw /openclaw

# Provide an openclaw executable
RUN printf '%s\n' '#!/usr/bin/env bash' 'exec node /openclaw/dist/entry.js "$@"' > /usr/local/bin/openclaw \
  && chmod +x /usr/local/bin/openclaw

COPY src ./src
COPY . /app

EXPOSE 8080

ENTRYPOINT ["tini", "--"]
CMD ["sh", "-c", "node scripts/apply-openclaw-gdoc-fallback.js || echo '[gdoc-fallback-patch] failed; starting server anyway'; exec node src/start-with-prorok-api.js"]
