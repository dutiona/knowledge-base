"""Tests for the OmniParser HTTP server handler."""

import base64
import json
from io import BytesIO
from unittest.mock import MagicMock, patch

from knowledge_base._omniparser_server import OmniParserHandler, _MAX_BODY_BYTES


def _make_handler(method: str, path: str, body: bytes = b"") -> OmniParserHandler:
    """Create an OmniParserHandler with a fake request."""
    request_line = f"{method} {path} HTTP/1.1\r\n"

    handler = OmniParserHandler.__new__(OmniParserHandler)
    handler.rfile = BytesIO(body)
    handler.wfile = BytesIO()
    handler.headers = {
        "Content-Length": str(len(body)),
        "Content-Type": "application/json",
    }
    handler.path = path
    handler.requestline = request_line.strip()
    handler.request_version = "HTTP/1.1"
    handler.command = method
    handler.client_address = ("127.0.0.1", 12345)
    handler.server = MagicMock()
    handler.close_connection = True

    # Capture responses
    return handler


class TestHealthEndpoint:
    def test_returns_200_with_omniparser_status(self):
        handler = _make_handler("GET", "/health")
        handler.do_GET()

        handler.wfile.seek(0)
        response = handler.wfile.read().decode()
        assert "200" in response
        assert '"status": "omniparser"' in response

    def test_models_loaded_reflects_state(self):
        handler = _make_handler("GET", "/health")

        with patch("knowledge_base._omniparser_server._omniparser_instance", None):
            handler.do_GET()

        handler.wfile.seek(0)
        response = handler.wfile.read().decode()
        assert '"models_loaded": false' in response


class TestParseEndpoint:
    def test_valid_request_returns_result(self):
        handler = _make_handler("POST", "/parse")

        image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        body = json.dumps(
            {"image_base64": base64.b64encode(image_bytes).decode()}
        ).encode()
        handler.rfile = BytesIO(body)
        handler.headers = {"Content-Length": str(len(body))}

        mock_result = {
            "elements": [{"text": "test"}],
            "image_size": {"w": 100, "h": 100},
        }
        with patch(
            "knowledge_base._omniparser_server._parse_image", return_value=mock_result
        ):
            handler.do_POST()

        handler.wfile.seek(0)
        response = handler.wfile.read().decode()
        assert "200" in response
        assert '"elements"' in response

    def test_invalid_json_returns_400(self):
        handler = _make_handler("POST", "/parse")

        body = b"not json at all"
        handler.rfile = BytesIO(body)
        handler.headers = {"Content-Length": str(len(body))}

        handler.do_POST()

        handler.wfile.seek(0)
        response = handler.wfile.read().decode()
        assert "400" in response

    def test_missing_image_base64_returns_400(self):
        handler = _make_handler("POST", "/parse")

        body = json.dumps({"wrong_field": "value"}).encode()
        handler.rfile = BytesIO(body)
        handler.headers = {"Content-Length": str(len(body))}

        handler.do_POST()

        handler.wfile.seek(0)
        response = handler.wfile.read().decode()
        assert "400" in response
        assert "image_base64" in response

    def test_oversized_body_returns_413(self):
        handler = _make_handler("POST", "/parse")

        handler.headers = {"Content-Length": str(_MAX_BODY_BYTES + 1)}

        handler.do_POST()

        handler.wfile.seek(0)
        response = handler.wfile.read().decode()
        assert "413" in response

    def test_parse_failure_returns_500(self):
        handler = _make_handler("POST", "/parse")

        image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        body = json.dumps(
            {"image_base64": base64.b64encode(image_bytes).decode()}
        ).encode()
        handler.rfile = BytesIO(body)
        handler.headers = {"Content-Length": str(len(body))}

        with patch(
            "knowledge_base._omniparser_server._parse_image",
            side_effect=RuntimeError("model crashed"),
        ):
            handler.do_POST()

        handler.wfile.seek(0)
        response = handler.wfile.read().decode()
        assert "500" in response

    def test_unknown_path_returns_404(self):
        handler = _make_handler("POST", "/unknown")
        handler.headers = {"Content-Length": "0"}

        handler.do_POST()

        handler.wfile.seek(0)
        response = handler.wfile.read().decode()
        assert "404" in response
