"""teamspeak-prometheus: a read-only TeamSpeak 3 exporter for Prometheus.

The exporter runs from ``app.py``; the container healthcheck from
``healthcheck.py``. Both are thin entry points into this package. See
docs/architecture.md for the module map.
"""

__version__ = '1.0.0'
