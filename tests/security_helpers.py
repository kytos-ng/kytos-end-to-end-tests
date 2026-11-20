"""Helpers for the security e2e tests (fuzzing and crafted input)."""
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests


def find_leaks(text):
    """Return the kinds of internal details exposed in a response body."""
    patterns = {
        "python traceback": r"Traceback \(most recent call last\)",
        "source file and line": r'File "[^"]+\.py", line \d+',
        "python install path":
            r"site-packages|/usr/(?:local/)?lib/python|/opt/[\w./-]*venv",
        "kytos install path": r"/var/lib/kytos|/etc/kytos",
        "backend library name":
            r"pymongo|mongodb|starlette|uvicorn|openapi_core|jsonschema",
    }
    return [name for name, regex in patterns.items()
            if re.search(regex, text, re.IGNORECASE)]


def assert_controller_running(kytos_api="http://127.0.0.1:8181/api/kytos"):
    """Kytos must still be up after whatever the test sent."""
    response = requests.get(f"{kytos_api}/core/status/", timeout=10)
    assert response.status_code == 200, response.text
    assert response.json()["response"] == "running", response.text


def load_napp_schema(napp, base_url, username="kytos"):
    """Load a NApp's own openapi.yml for positive and negative generation.

    Skips the test module when schemathesis or the spec is not available.
    """
    schemathesis = pytest.importorskip("schemathesis")
    base = os.environ.get("NAPPS_PATH") or os.environ.get("VIRTUAL_ENV") or ""
    path = f"{base}/var/lib/kytos/napps/{username}/{napp}/openapi.yml"
    if not os.path.isfile(path):
        pytest.skip(f"OpenAPI spec not found at {path}",
                    allow_module_level=True)
    return schemathesis.from_path(
        path,
        base_url=base_url,
        data_generation_methods=[
            schemathesis.DataGenerationMethod.positive,
            schemathesis.DataGenerationMethod.negative,
        ],
    )


def fuzz_settings():
    """Deterministic Hypothesis settings; FUZZ_MAX_EXAMPLES sets the depth."""
    from hypothesis import HealthCheck, settings
    return settings(
        max_examples=int(os.environ.get("FUZZ_MAX_EXAMPLES", "50")),
        deadline=None,
        derandomize=True,
        database=None,
        suppress_health_check=list(HealthCheck),
    )


def no_internal_details_leaked(ctx, response, case):
    """Schemathesis check: a response must not expose internals."""
    leaks = find_leaks(response.text)
    if leaks:
        raise AssertionError(
            f"{case.method} {case.formatted_path} leaked internal details "
            f"({', '.join(leaks)}): {response.text[:300]!r}"
        )


def run_case(case):
    """Send one generated request: no 5xx and no leaked internals."""
    from schemathesis.checks import not_a_server_error
    response = case.call(timeout=15)
    case.validate_response(
        response, checks=(not_a_server_error, no_internal_details_leaked))


class MaintenanceWindows:
    """Maintenance windows on made-up switches (ignore_no_exists), so no
    real switch is ever put into maintenance."""

    api = "http://127.0.0.1:8181/api/kytos/maintenance/v1"
    time_fmt = "%Y-%m-%dT%H:%M:%S+0000"

    @staticmethod
    def fake_switch():
        """A random dpid that no Mininet topology uses."""
        return "00:00:ee:" + ":".join(f"{b:02x}" for b in secrets.token_bytes(5))

    @classmethod
    def utc_in(cls, **delta):
        """A timestamp ``delta`` from now in the NApp's format."""
        when = datetime.now(timezone.utc) + timedelta(**delta)
        return when.strftime(cls.time_fmt)

    @classmethod
    def create(cls, start, end=None):
        """Create a window on a fresh made-up switch and return its id."""
        payload = {
            "description": "security test window",
            "start": start,
            "switches": [cls.fake_switch()],
            "force": True,
            "ignore_no_exists": True,
        }
        if end is not None:
            payload["end"] = end
        response = requests.post(cls.api, json=payload, timeout=15)
        assert response.status_code == 201, response.text
        return response.json()["mw_id"]

    @classmethod
    def get(cls, mw_id):
        """Return the window, or None if it does not exist."""
        response = requests.get(f"{cls.api}/{mw_id}", timeout=15)
        if response.status_code == 404:
            return None
        assert response.status_code == 200, response.text
        return response.json()

    @classmethod
    def create_running(cls, end=None, start_delay=3):
        """Create a window that starts in a few seconds and wait until it runs.

        Without ``end`` the NApp stores its default end (datetime.max).
        """
        mw_id = cls.create(cls.utc_in(seconds=start_delay), end)
        deadline = time.monotonic() + start_delay + 30
        window = None
        while time.monotonic() < deadline:
            window = cls.get(mw_id)
            if window and window["status"] == "running":
                return mw_id
            time.sleep(0.5)
        raise AssertionError(f"window {mw_id} never started: {window}")

    @classmethod
    def create_pending(cls):
        """Create a window far enough in the future to stay pending."""
        return cls.create(cls.utc_in(days=3650), cls.utc_in(days=3651))

    @classmethod
    def remove(cls, mw_id):
        """Best-effort cleanup: end a running window, delete a pending one."""
        try:
            window = cls.get(mw_id)
        except AssertionError:
            return
        if window and window["status"] == "running":
            requests.patch(f"{cls.api}/{mw_id}/end", timeout=15)
        elif window and window["status"] == "pending":
            requests.delete(f"{cls.api}/{mw_id}", timeout=15)
