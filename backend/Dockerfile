FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
ENV STARDECK_DATA_DIR=/data
EXPOSE 8000
# One process only: sign-in limits and other safeguards live in memory. Forwarded headers are
# trusted from the proxy in front (the backend port is never published).
ENV FORWARDED_ALLOW_IPS="*"
CMD ["uv", "run", "--frozen", "--no-dev", "uvicorn", "stardeck.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
