# sleeper-discord-bot — production image for the Raspberry Pi (ARM64).
#
# No web server and no exposed port: the process holds an outbound websocket to
# Discord and polls Sleeper over HTTPS. Nothing needs to reach it.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data

WORKDIR /app

# Dependencies first so this layer survives code-only rebuilds. discord.py and
# aiohttp both publish aarch64 wheels, so this needs no compiler on the Pi.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sleeperbot/ ./sleeperbot/
COPY scripts/healthcheck.py ./scripts/

# Run unprivileged, and create /data in the image so the named volume inherits
# an ownership the bot can actually write its SQLite state into.
RUN useradd --create-home --uid 1000 sleeper \
    && mkdir -p /data \
    && chown -R sleeper:sleeper /app /data
USER sleeper

VOLUME ["/data"]

# start_period covers the first poll, which includes the ~5 MB player catalog
# download on a cold volume.
HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
    CMD python scripts/healthcheck.py || exit 1

CMD ["python", "-m", "sleeperbot"]
