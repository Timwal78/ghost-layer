FROM python:3.11-slim

WORKDIR /app

# Prevent glibc memory fragmentation (OOM Fix)
ENV MALLOC_ARENA_MAX=2
ENV PYTHONUNBUFFERED=1

# Copy the ghost-layer package source
COPY . /app/

# Install the package
RUN pip install --no-cache-dir .

# Default port for GhostGateway
EXPOSE 7391

# The upstream URL and key should be provided via environment variables
# e.g., UPSTREAM_URL=https://api.openai.com UPSTREAM_KEY=sk-...
ENV PORT=7391
ENV UPSTREAM_URL=""
ENV UPSTREAM_KEY=""

ENTRYPOINT ["sh", "-c", "ghost serve --host 0.0.0.0 --port $PORT --upstream $UPSTREAM_URL"]
