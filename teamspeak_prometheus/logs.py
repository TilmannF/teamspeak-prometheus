"""The exporter's one logger, and how it is set up.

Every module logs through ``log``. The ``RedactingFilter`` sits on this logger,
and a filter on a logger does not apply to its child loggers: a module with its
own ``logging.getLogger(__name__)`` would bypass the censoring.
tests/test_structure.py enforces this.
"""

from __future__ import annotations

import logging

from teamspeak_prometheus.redaction import RedactingFilter

log = logging.getLogger('teamspeak_prometheus')


def configure_logging(level: str, secrets: list[str] | None = None) -> None:
    """Set up logging; ``secrets`` are redacted from every exporter log line."""

    logging.basicConfig(
        level=level, format='%(asctime)s %(levelname)s %(message)s', force=True
    )
    for existing in [f for f in log.filters if isinstance(f, RedactingFilter)]:
        log.removeFilter(existing)
    log.addFilter(RedactingFilter(secrets or []))
