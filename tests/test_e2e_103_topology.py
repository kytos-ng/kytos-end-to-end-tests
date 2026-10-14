import json
import time

import requests

from tests.helpers import NetworkTest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER


class TestE2ETopologyMetadataOperatorKey:
    """Abuse-case coverage for Mongo-operator metadata keys on topology.

    Switch metadata is persisted as a sub-document under the ``metadata``
    field. A crafted key that looks like a MongoDB update operator (``$set``,
    ``$where``, ``$inc``) must be treated as ordinary data: either rejected or
    stored and returned verbatim as a literal key. It must never be evaluated
    as an operator, and a crafted key must never cause a 500 or a server-side
    write error that leaves the controller inconsistent.
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

    def test_010_operator_metadata_key_handled_safely(self):
        """A metadata key equal to a Mongo operator must be rejected or kept
        as a literal key, and must not trigger a 500."""
        switch_id = "00:00:00:00:00:00:00:01"

        payload = {"$set": "pwned", "plain": "ok"}
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata' % switch_id
        response = requests.post(
            api_url, data=json.dumps(payload), timeout=30,
            headers={'Content-type': 'application/json'})

        # A crafted key must never cause an unhandled server error.
        assert response.status_code != 500, response.text
        assert response.status_code in (201, 400), response.text

        if response.status_code == 400:
            # Rejecting the operator key outright is acceptable.
            return

        metadata = self.get_metadata(switch_id)
        assert metadata.get("plain") == "ok"
        # The operator key, if accepted, is stored verbatim as a literal key.
        assert metadata.get("$set") == "pwned", metadata

    def test_020_operator_metadata_key_survives_restart(self):
        """If an operator-like key is accepted, it must persist unchanged
        across a controller restart and must not corrupt the stored document.
        """
        switch_id = "00:00:00:00:00:00:00:01"

        payload = {"$where": "1", "plain": "ok"}
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
        # The legitimate key must survive the round-trip intact.
        assert metadata.get("plain") == "ok", metadata
        assert metadata.get("$where") == "1", metadata
