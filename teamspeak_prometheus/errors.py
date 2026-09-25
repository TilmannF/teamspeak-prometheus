"""Exceptions raised by the exporter."""

from __future__ import annotations


class ExporterError(Exception):
    """Base class for errors raised by this exporter."""


class ServerQueryError(ExporterError):
    """ServerQuery answered a command with a non-zero error id.

    The message names the command, never its parameters: ``login`` carries the
    password.
    """

    def __init__(self, command: str, error_id: int, message: str) -> None:
        super().__init__(f'{command} failed: error id={error_id} {message}')
        self.command = command
        self.error_id = error_id
        self.message = message


class LoginFailed(ServerQueryError):
    """TeamSpeak rejected the ServerQuery credentials (error id 520)."""
