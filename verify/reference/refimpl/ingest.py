"""Reference ingest entrypoint: ``python -m fxlab.ingest --config <cfg.toml>``."""

from __future__ import annotations

import argparse
import logging
import sys
import tomllib

from ._core import ValidationError, ingest


def main() -> int:
    """Decode and store the configured fixture hours."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ns = ap.parse_args()

    with open(ns.config, "rb") as fh:
        config = tomllib.load(fh)
    try:
        manifest = ingest(config)
    except ValidationError as exc:
        # The reason token is what the gate greps for; keep it on stderr verbatim.
        print(f"{exc.reason}: {exc.detail}", file=sys.stderr)
        logging.error("ingest rejected input: %s", exc)
        return 4
    logging.info("ingested %d hour(s)", len(manifest["hours"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
