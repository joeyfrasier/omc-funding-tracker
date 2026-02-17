# OFM — OMC Funding Manager
# Multi-stage build: Python backend + Next.js frontend

# ============ Stage 1: Frontend build ============
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN corepack enable && corepack prepare pnpm@latest --activate && pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ============ Stage 2: Runtime ============
FROM python:3.12-slim
WORKDIR /app

# System deps (use Node 22 from nodesource instead of ancient Debian nodejs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt fastapi uvicorn

# App source
COPY *.py ./
COPY *.json ./
COPY templates/ ./templates/
COPY static/ ./static/

# Frontend (built)
COPY --from=frontend-build /app/frontend/.next ./frontend/.next
COPY --from=frontend-build /app/frontend/node_modules ./frontend/node_modules
COPY --from=frontend-build /app/frontend/package.json ./frontend/package.json
COPY frontend/next.config.ts ./frontend/
COPY frontend/postcss.config.mjs ./frontend/
COPY frontend/tsconfig.json ./frontend/
COPY frontend/src ./frontend/src

# Data directory
RUN mkdir -p /app/data

# Entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 3000 8000 8501

ENTRYPOINT ["/docker-entrypoint.sh"]
