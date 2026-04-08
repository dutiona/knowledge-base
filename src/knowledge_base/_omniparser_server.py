"""Persistent HTTP server wrapping OmniParser for figure OCR/detection.

Designed to run under OmniParser's own ``.venv/bin/python`` so that heavy
ML dependencies (torch, transformers, ultralytics, easyocr) are isolated
from the knowledge-base environment.

Usage::

    /path/to/omniparser/.venv/bin/python -m knowledge_base._omniparser_server \
        --omniparser-path /path/to/omniparser --port 7862

Or via the installed entry point::

    omniparser-server --omniparser-path /path/to/omniparser
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import signal
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

logger = logging.getLogger("omniparser-server")

# Maximum request body size (50 MB).  A high-res page PNG is typically
# 2-10 MB base64-encoded; 50 MB provides generous headroom.
_MAX_BODY_BYTES = 50 * 1024 * 1024

# Sentinel printed to stdout when the server is ready to accept requests.
# The parent process (vision.py auto-start) watches for this line.
_READY_SENTINEL = "OMNIPARSER_READY"

# ---------------------------------------------------------------------------
# Global model handle — loaded once at startup, reused for every request.
# ---------------------------------------------------------------------------
_omniparser_instance = None  # set by _load_models()
_omniparser_path: str | None = None  # set by main(), used by subprocess fallback


def _load_models(omniparser_path: str) -> bool:
    """Attempt to import and instantiate OmniParser.

    Returns True on success.  On failure (e.g. parse.py is CLI-only and
    not importable as a library), logs a warning and returns False.  The
    caller should fall back to subprocess-based parsing which still
    amortises model load because the server process stays alive.
    """
    global _omniparser_instance  # noqa: PLW0603
    sys.path.insert(0, omniparser_path)
    try:
        from util.omniparser import Omniparser  # type: ignore[import-untyped]

        _omniparser_instance = Omniparser()
        logger.info("OmniParser models loaded via direct import")
        return True
    except Exception:
        logger.warning(
            "Could not import OmniParser as library — "
            "will use subprocess fallback per request (models still cached in server process)",
            exc_info=True,
        )
        return False


def _parse_image_direct(png_path: Path) -> dict:
    """Parse an image using the in-process OmniParser instance."""
    assert _omniparser_instance is not None
    with open(png_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()
    return _omniparser_instance.parse(image_b64)


def _parse_image_subprocess(png_path: Path) -> dict:
    """Parse an image via OmniParser's parse.py CLI (subprocess fallback).

    The server process already has models cached in memory from the import
    attempt, but since the Python API isn't available we shell out to
    parse.py which lives in the same venv — so the OS can share the
    already-warm GPU memory pages.
    """
    import subprocess

    assert _omniparser_path is not None
    parse_script = Path(_omniparser_path) / "parse.py"
    venv_python = Path(_omniparser_path) / ".venv" / "bin" / "python"

    json_fd, json_out = tempfile.mkstemp(suffix=".json")
    try:
        os.close(json_fd)
        subprocess.run(
            [str(venv_python), str(parse_script), str(png_path), "-j", json_out],
            timeout=300,
            capture_output=True,
            check=True,
        )
        with open(json_out) as f:
            return json.load(f)
    finally:
        Path(json_out).unlink(missing_ok=True)


def _parse_image(png_path: Path) -> dict:
    """Parse an image using whichever backend is available."""
    if _omniparser_instance is not None:
        return _parse_image_direct(png_path)
    return _parse_image_subprocess(png_path)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class OmniParserHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OmniParser."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(
                200,
                {
                    "status": "omniparser",
                    "models_loaded": _omniparser_instance is not None,
                },
            )
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/parse":
            self._send_json(404, {"error": "not found"})
            return

        # --- Read and validate body ---
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > _MAX_BODY_BYTES:
            self._send_json(413, {"error": f"body exceeds {_MAX_BODY_BYTES} bytes"})
            return

        try:
            body = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"error": f"invalid JSON: {exc}"})
            return

        if "image_base64" not in body:
            self._send_json(400, {"error": "missing required field: image_base64"})
            return

        # --- Decode image and parse ---
        png_fd, png_tmp = tempfile.mkstemp(suffix=".png")
        try:
            os.close(png_fd)
            image_bytes = base64.b64decode(body["image_base64"])
            Path(png_tmp).write_bytes(image_bytes)
            result = _parse_image(Path(png_tmp))
            self._send_json(200, result)
        except Exception as exc:
            logger.error("OmniParser failed: %s", exc, exc_info=True)
            self._send_json(500, {"error": str(exc)})
        finally:
            Path(png_tmp).unlink(missing_ok=True)

    def _send_json(self, status: int, data: dict) -> None:
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Route HTTP access logs through the module logger."""
        logger.debug(format, *args)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="OmniParser HTTP server")
    parser.add_argument(
        "--omniparser-path",
        required=True,
        help="Absolute path to OmniParser directory",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=7862, help="Bind port")
    args = parser.parse_args(argv)

    global _omniparser_path  # noqa: PLW0603
    _omniparser_path = args.omniparser_path

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    omni_dir = Path(args.omniparser_path)
    if not omni_dir.is_dir():
        logger.error("OmniParser directory not found: %s", omni_dir)
        sys.exit(1)

    logger.info("Loading OmniParser models from %s ...", omni_dir)
    _load_models(args.omniparser_path)

    server = HTTPServer((args.host, args.port), OmniParserHandler)

    # Graceful shutdown on SIGTERM/SIGINT
    def _shutdown(signum: int, _frame: object) -> None:
        logger.info("Received signal %d, shutting down", signum)
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Print readiness sentinel.  flush=True is critical — without it the
    # parent process waiting on stdout may never see the line due to
    # buffering.  PYTHONUNBUFFERED=1 is also set by the auto-start code
    # as a belt-and-suspenders measure.
    print(f"{_READY_SENTINEL} host={args.host} port={args.port}", flush=True)

    # Redirect stdout to logger after sentinel to prevent pipe fill when
    # spawned by a parent that only reads until the sentinel.
    sys.stdout = open(os.devnull, "w")  # noqa: SIM115, PTH123

    logger.info("Serving on %s:%d", args.host, args.port)
    server.serve_forever()
    logger.info("Server stopped")


if __name__ == "__main__":
    main()
