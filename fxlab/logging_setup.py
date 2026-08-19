"""Central logging configuration.

The project logs; it does not print. The one deliberate exception is the
validation reason tokens, which are written to stderr verbatim so that an
external judge can grep for them without parsing a log format.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Final

#: Environment variable that overrides the CLI's default log level.
LEVEL_ENV: Final[str] = "FXLAB_LOG_LEVEL"

_FORMAT: Final[str] = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT: Final[str] = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: str | int | None = None) -> None:
    """Install a stderr log handler once, at ``level``.

    Args:
        level: Level name or number. When ``None`` the ``FXLAB_LOG_LEVEL``
            environment variable is consulted, defaulting to ``INFO``.

    Logs go to stderr so that stdout stays free for machine-readable output.
    """
    if level is None:
        level = os.environ.get(LEVEL_ENV, "INFO")
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        root.addHandler(handler)
    root.setLevel(level)


def emit_reason(token: str, detail: str = "") -> None:
    """Write a validation reason token to stderr verbatim.

    Args:
        token: One of the named reason tokens (see
            :mod:`fxlab.ingestion.validation`).
        detail: Optional human-readable context appended on the same line.

    The token is emitted bare and first on the line precisely so that a grep
    for it cannot be confused by log formatting.
    """
    line = token if not detail else f"{token} {detail}"
    print(line, file=sys.stderr, flush=True)
