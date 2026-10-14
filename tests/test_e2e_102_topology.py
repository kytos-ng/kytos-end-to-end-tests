import json
import time

import pytest
import requests

from tests.helpers import NetworkTest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER


class TestE2ETopologyMetadataInjection:
    """Abuse-case coverage for crafted metadata keys on topology.

    Switch metadata is stored as a sub-document under the ``metadata`` field.
    MongoDB historically treats a dot in a field name as a path separator, so a
    crafted key such as ``"a.b.c"`` could, if forwarded carelessly into a query
    operator, be expanded into a nested document ``{"a": {"b": {"c": ...}}}``
    instead of being kept as a literal key.

    The expected secure behaviour is that a dotted key is either rejected or
    stored and returned verbatim as a single literal key, never silently turned
    into a nested structure, and that the request never triggers a 500.
    """

    net = None

    def setup_method(self, method):
        """Start the controller clean before each test."""
        self.net.start_controller(clean_config=True, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start(start_controller=False)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def get_metadata(self, switch_id):
        """Return the metadata dict of a switch."""
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata' % switch_id
        response = requests.get(api_url, timeout=30)
        assert response.status_code == 200, response.text
        return response.json()['metadata']

    def test_010_dotted_metadata_key_stored_literally(self):
        """A dotted metadata key must round-trip as a single literal key and
        must not be expanded into a nested document."""
        switch_id = "00:00:00:00:00:00:00:01"

        payload = {"a.b.c": "v1", "plain": "ok"}
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata' % switch_id
        response = requests.post(
            api_url, data=json.dumps(payload), timeout=30,
            headers={'Content-type': 'application/json'})

        # Whatever the decision, it must never be an unhandled server error.
        assert response.status_code != 500, response.text
        assert response.status_code in (201, 400), response.text

        if response.status_code == 400:
            # Rejecting the crafted key outright is acceptable.
            return

        metadata = self.get_metadata(switch_id)
        # The plain key is stored as usual.
        assert metadata.get("plain") == "ok"
        # The dotted key is kept verbatim, not split into nested objects.
        assert metadata.get("a.b.c") == "v1", metadata
        assert "a" not in metadata, metadata

    @pytest.mark.xfail(
        reason="A dotted metadata key is accepted and kept literal in memory, "
        "but after a controller restart it is reloaded from MongoDB as a "
        "nested document (e.g. {'x.y': 'z'} becomes {'x': {'y': 'z'}}), so "
        "the in-memory and persisted forms diverge. Pending issue; see "
        "PR discussion for where to report.",
        strict=False,
    )
    def test_020_dotted_metadata_key_survives_restart(self):
        """If a dotted key is accepted, it must persist unchanged across a
        controller restart (no silent nesting on reload from the database)."""
        switch_id = "00:00:00:00:00:00:00:01"

        payload = {"x.y": "z"}
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata' % switch_id
        response = requests.post(
            api_url, data=json.dumps(payload), timeout=30,
            headers={'Content-type': 'application/json'})
        assert response.status_code != 500, response.text
        if response.status_code != 201:
            return

        # Restart preserving persisted state.
        self.net.start_controller(clean_config=False, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(10)

        metadata = self.get_metadata(switch_id)
        assert metadata.get("x.y") == "z", metadata
        assert "x" not in metadata, metadata
