"""Regression tests for _api_exception_response sanitization.

Verifies that:
- Expected validation exceptions (ValueError, KeyError, etc.) surface fixed
  public messages in default 4xx responses without reflecting exception text.
- Unexpected / internal exceptions (RuntimeError, OSError, PermissionError,
  generic Exception) are redacted to a generic message.
- 5xx responses never leak exception details regardless of type.
- Custom public_message and hint pass through unchanged.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import import_web_app_module

flask = pytest.importorskip("flask", reason="Flask not installed; skipping")


@pytest.fixture()
def _app():
    """Provide the Flask app with a request context so jsonify works."""
    mod = import_web_app_module()
    mod.app.config["TESTING"] = True
    with mod.app.app_context():
        yield mod


def _call(mod, exc, status=400, public_message="Request failed", hint=None):
    """Invoke _api_exception_response and return (json_dict, http_status)."""
    resp, code = mod._api_exception_response(
        exc, status, public_message, hint=hint,
    )
    return resp.get_json(), code


# ---------------------------------------------------------------------------
# Safe validation exceptions SHOULD surface fixed public messages
# ---------------------------------------------------------------------------

class TestSafeTypesExposed:

    @pytest.mark.parametrize(("exc", "expected"), [
        (ValueError("Invalid SFID format /var/secrets/token"), "Invalid request"),
        (KeyError("missing_field /var/secrets/token"), "Invalid request"),
        (IndexError("BOM line 99 out of range /var/secrets/token"), "Invalid request"),
        (
            FileNotFoundError("Entity p_ghost not found /var/secrets/token"),
            "Requested resource was not found",
        ),
        (
            FileExistsError("Entity p_dup already exists /var/secrets/token"),
            "Requested resource already exists",
        ),
    ])
    def test_validation_error_surfaces_in_400(self, _app, exc, expected):
        data, code = _call(_app, exc, status=400)
        assert code == 400
        assert data["success"] is False
        assert data["error"] == expected
        assert "/var/secrets" not in data["error"]


# ---------------------------------------------------------------------------
# Unsafe/internal exception text MUST be redacted in 4xx responses
# ---------------------------------------------------------------------------

class TestUnsafeTypesRedacted:

    @pytest.mark.parametrize("exc", [
        RuntimeError("Pre-mutation git pull failed: https://token:ghp_SECRET@github.com/org/repo.git"),
        OSError("unexpected OS-level detail /var/secrets/key"),
        PermissionError("Permission denied: /etc/shadow"),
        IsADirectoryError("/home/user/repo/entities"),
        NotADirectoryError("/home/user/file.txt"),
        Exception("completely unexpected internal error"),
        TypeError("unsupported operand type(s)"),
    ])
    def test_internal_error_redacted_in_400(self, _app, exc):
        data, code = _call(_app, exc, status=400)
        assert code == 400
        assert data["success"] is False
        assert data["error"] == "Request failed"

    def test_runtime_error_with_git_stderr_redacted(self, _app):
        """Specifically reproduces the _run_repo_txn leak path."""
        exc = RuntimeError(
            "Pre-mutation git pull failed: "
            "fatal: could not read Username for 'https://github.com': "
            "terminal prompts disabled"
        )
        data, _ = _call(_app, exc, status=400)
        assert "github.com" not in data["error"]
        assert "terminal prompts" not in data["error"]
        assert data["error"] == "Request failed"

    def test_os_error_with_secret_path_redacted(self, _app):
        exc = OSError("[Errno 2] No such file: '/var/run/secrets/token'")
        data, _ = _call(_app, exc, status=400)
        assert "/var/run" not in data["error"]
        assert data["error"] == "Request failed"

    def test_upgrade_in_progress_runtime_error_surfaces_actionable_message(self, _app):
        exc = RuntimeError("Repository upgrade in progress; retry after upgrade completes.")
        data, code = _call(_app, exc, status=400)
        assert code == 400
        assert data["error"] == "Repository upgrade in progress; retry after upgrade completes."

    def test_repo_lock_timeout_runtime_error_surfaces_actionable_message(self, _app):
        exc = RuntimeError("Timed out waiting for repo lock after 30.0s")
        data, code = _call(_app, exc, status=400)
        assert code == 400
        assert data["error"] == "Another SmallFactory operation is in progress; retry shortly."

    def test_safe_exception_multiline_redacted(self, _app):
        exc = ValueError("first line\nsecond line with /secret/path")
        data, _ = _call(_app, exc, status=400)
        assert data["error"] == "Invalid request"
        assert "first line" not in data["error"]
        assert "second line" not in data["error"]


# ---------------------------------------------------------------------------
# 5xx responses: NEVER leak details regardless of exception type
# ---------------------------------------------------------------------------

class TestFiveHundredAlwaysRedacted:

    @pytest.mark.parametrize("exc", [
        ValueError("should not appear in 500"),
        RuntimeError("internal crash"),
        Exception("unexpected"),
    ])
    def test_500_always_generic(self, _app, exc):
        data, code = _call(_app, exc, status=500)
        assert code == 500
        assert data["error"] == "Request failed"


# ---------------------------------------------------------------------------
# Custom public_message and hint pass-through
# ---------------------------------------------------------------------------

class TestCustomMessageAndHint:

    def test_custom_public_message_used(self, _app):
        exc = RuntimeError("internal detail")
        data, code = _call(_app, exc, status=400, public_message="Bad entity request")
        assert code == 400
        assert data["error"] == "Bad entity request"

    def test_hint_included(self, _app):
        exc = ValueError("bad input")
        data, _ = _call(_app, exc, status=400, hint="Try format p_xxx")
        assert data["hint"] == "Try format p_xxx"

    def test_custom_message_not_overridden_by_safe_exc(self, _app):
        """When caller provides a specific public_message, it should be used
        even if the exception is a safe type."""
        exc = ValueError("internal validation detail")
        data, _ = _call(
            _app, exc, status=400,
            public_message="Entity update failed",
        )
        assert data["error"] == "Entity update failed"
