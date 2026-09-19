# syntax=docker/dockerfile:1
# IRIS — Air-Gapped IT Infrastructure Management System
# One-command deploy: docker build -t iris . && docker run -p 8000:8000 iris
#
# Multi-stage build: builder downloads wheels, prod installs from them.
# No internet required at runtime.

# ── Builder stage ──────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip download -r requirements.txt -d /wheels \
    && pip install --no-cache-dir --dry-run -r requirements.txt \
    || true   # dry-run may warn but we just need wheels cached

# ── Production stage ───────────────────────────────────────────────────
FROM python:3.12-slim AS production

# System deps: fonts, tesseract (optional), chromium (for playwright)
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-liberation \
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
        libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy wheels and install
COPY --from=builder /wheels /wheels
COPY . .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && python -m playwright install chromium 2>/dev/null || true

# Expose port
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# Run
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
