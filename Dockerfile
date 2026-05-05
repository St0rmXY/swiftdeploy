# ── SwiftDeploy API Service ───────────────────────────────────────────────────
FROM python:3.12-alpine

# Non-root user
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

# App source (stdlib only — no pip install needed)
COPY app/main.py .

# Drop all capabilities at runtime (enforced in compose too)
USER appuser

ENV MODE=stable \
    APP_VERSION=1.0.0 \
    APP_PORT=3000

EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost:3000/healthz || exit 1

CMD ["python", "main.py"]
