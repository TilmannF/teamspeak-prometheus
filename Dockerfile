FROM python:3.14-alpine

WORKDIR /app

COPY requirements.txt app.py healthcheck.py ./
COPY teamspeak_prometheus ./teamspeak_prometheus

# pip only installs; the exporter never runs it. Left in, its vendored packages
# are what image scanners flag (1.0.0: msgpack, setuptools). A multi-stage
# build would still start from this base, pip included, so it goes here.
# tests/container_test.sh fails if it comes back.
RUN pip install --no-cache-dir -r requirements.txt \
  && pip uninstall --yes --quiet pip \
  && adduser -D -H -u 10001 exporter

USER 10001

EXPOSE 8000

# Healthy means the metrics endpoint answers, on whatever port the exporter
# really uses: healthcheck.py resolves it from the running exporter's flags and
# the environment. Whether TeamSpeak is reachable is reported by
# teamspeak_exporter_poll_success instead, so a TeamSpeak outage does not get
# the exporter restarted. See docs/architecture.md, "Container healthcheck".
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD ["python", "/app/healthcheck.py"]

CMD ["python", "/app/app.py"]
