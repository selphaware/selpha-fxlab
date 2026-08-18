"""Guarded module runner used by the gate to execute deliverable entrypoints.

This is HARNESS machinery. The gate never imports the deliverable into its own
process -- it launches this runner as a subprocess so that a crash, a
``sys.exit``, or a rogue import cannot corrupt the judge.

Two jobs:

1. **Put the deliverable on ``sys.path`` explicitly.** The gate runs Python with
   ``-E -s`` so that ``PYTHONPATH`` and user site-packages cannot influence the
   result. This machine has ``PYTHONPATH=i:\\xstar`` set globally, which places
   an unrelated project ahead of the standard library and even shadows the
   stdlib ``test`` package -- exactly the kind of ambient state that makes a
   gate's verdict depend on the machine rather than the code.

2. **Block the network.** Fixture mode must be provably offline. Any attempt to
   reach a non-loopback address raises and is recorded in the report file, so
   the gate can fail the run *and say which host was contacted*.

Usage::

    python -E -s _run_guarded.py --deliverable-parent DIR --module M
                                 --report R [-- ...module args]
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import socket
import sys
import traceback

#: Populated by the guard whenever something tries to leave the machine.
_ATTEMPTS: list[dict[str, object]] = []


class NetworkAccessBlocked(RuntimeError):
    """Raised when guarded code attempts a non-loopback connection."""


def _is_loopback(host: object) -> bool:
    """Return True for addresses that are safe to allow in fixture mode."""
    if not isinstance(host, str):
        return False
    return host in {"127.0.0.1", "::1", "localhost", ""}


def _record(kind: str, target: object) -> None:
    _ATTEMPTS.append({"kind": kind, "target": repr(target)})


def install_network_guard() -> None:
    """Monkeypatch the socket layer so non-loopback egress raises.

    Patching at the ``socket`` level catches urllib, http.client, httpx,
    requests and anything else built on top of it, without the gate needing to
    know which HTTP library the deliverable chose.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def _check(address: object, kind: str) -> None:
        host = address[0] if isinstance(address, tuple) and address else address
        if not _is_loopback(host):
            _record(kind, address)
            raise NetworkAccessBlocked(
                f"fixture mode attempted network access to {address!r} via {kind}; "
                "the gate must be fully offline"
            )

    def connect(self, address):  # type: ignore[no-untyped-def]
        _check(address, "socket.connect")
        return real_connect(self, address)

    def connect_ex(self, address):  # type: ignore[no-untyped-def]
        _check(address, "socket.connect_ex")
        return real_connect_ex(self, address)

    def create_connection(address, *a, **kw):  # type: ignore[no-untyped-def]
        _check(address, "socket.create_connection")
        return real_create_connection(address, *a, **kw)

    def getaddrinfo(host, port, *a, **kw):  # type: ignore[no-untyped-def]
        if not _is_loopback(host):
            _record("socket.getaddrinfo", (host, port))
            raise NetworkAccessBlocked(
                f"fixture mode attempted DNS resolution of {host!r}; "
                "the gate must be fully offline"
            )
        return real_getaddrinfo(host, port, *a, **kw)

    socket.socket.connect = connect              # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex        # type: ignore[method-assign]
    socket.create_connection = create_connection  # type: ignore[assignment]
    socket.getaddrinfo = getaddrinfo              # type: ignore[assignment]


def main() -> int:
    """Run the requested module under the guard and write a JSON report."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--deliverable-parent", required=True)
    ap.add_argument("--module", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--allow-network", action="store_true",
                    help="harness self-test only: prove the guard actually fires")
    ap.add_argument("rest", nargs=argparse.REMAINDER)
    ns = ap.parse_args()

    if not ns.allow_network:
        install_network_guard()

    sys.path.insert(0, os.path.abspath(ns.deliverable_parent))

    module_args = ns.rest[1:] if ns.rest and ns.rest[0] == "--" else ns.rest
    sys.argv = [ns.module, *module_args]

    report: dict[str, object] = {
        "module": ns.module,
        "argv": sys.argv,
        "sys_path_head": sys.path[:4],
        "pythonpath_env": os.environ.get("PYTHONPATH"),
    }
    code = 0
    try:
        runpy.run_module(ns.module, run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except NetworkAccessBlocked as exc:
        report["error_type"] = "NetworkAccessBlocked"
        report["error"] = str(exc)
        traceback.print_exc()
        code = 90
    except BaseException as exc:  # noqa: BLE001 - the gate wants every failure
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
        traceback.print_exc()
        code = 91

    report["exit_code"] = code
    report["network_attempts"] = _ATTEMPTS
    with open(ns.report, "w", encoding="utf8") as fh:
        json.dump(report, fh, indent=2)
    return code


if __name__ == "__main__":
    sys.exit(main())
