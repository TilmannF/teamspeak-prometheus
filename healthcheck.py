"""Entry point of the container healthcheck: ``python /app/healthcheck.py``.

See teamspeak_prometheus.healthcheck.
"""

from teamspeak_prometheus.healthcheck import main

if __name__ == '__main__':
    raise SystemExit(main())
