FROM python:3.14-alpine

WORKDIR /app

COPY requirements.txt app.py ./

RUN pip install --no-cache-dir -r requirements.txt \
  && adduser -D -H -u 10001 exporter

USER 10001

EXPOSE 8000

# Healthy means the metrics endpoint answers. Whether TeamSpeak is reachable is
# reported by teamspeak_exporter_poll_success instead, so a TeamSpeak outage
# does not get the exporter restarted.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/metrics' % os.environ.get('METRICS_PORT', '8000'), timeout=4)"]

CMD ["python", "/app/app.py"]
