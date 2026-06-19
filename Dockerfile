FROM python:3.11-slim

WORKDIR /app

# Prevent glibc memory fragmentation (OOM Fix)
ENV MALLOC_ARENA_MAX=2
ENV PYTHONUNBUFFERED=1

# Copy the ghost-layer package source
COPY . /app/

# Install the package
RUN pip install --no-cache-dir .

# Run as non-root user for security
RUN useradd --no-create-home --shell /bin/false ghostuser \
    && mkdir -p /home/ghostuser/.ghost \
    && chown -R ghostuser:ghostuser /home/ghostuser/.ghost /app
USER ghostuser
ENV HOME=/home/ghostuser

# Default port for GhostGateway
EXPOSE 7391

# The upstream URL and key should be provided via environment variables at
# container runtime — never bake credentials into the image.
# e.g.:  docker run -e UPSTREAM_URL=https://api.example.com -e UPSTREAM_KEY=sk-...
ENV PORT=7391
ENV UPSTREAM_URL=""

# ENTRYPOINT reads UPSTREAM_URL from the environment; GHOST_UPSTREAM_KEY is
# read directly by the serve() command from the environment — it is never
# passed on the command line so it does not appear in `ps aux` output.
ENTRYPOINT ["sh", "-c", "ghost serve --host 0.0.0.0 --port \"$PORT\" --upstream \"$UPSTREAM_URL\""]
