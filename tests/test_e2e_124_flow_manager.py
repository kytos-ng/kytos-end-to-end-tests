import copy
import json
import time

import requests

from tests.helpers import NetworkTest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER

DPID = "00:00:00:00:00:00:00:01"

BASE_FLOW = {
    "priority": 10,
    "match": {"in_port": 1},
    "actions": [{"action_type": "output", "port": 2}],
}


class TestE2EFlowManagerBoundary:
    """Abuse-case coverage for out-of-range numeric values on flow_manager.

    Python integers are unbounded, but OpenFlow 1.3 fields are fixed width
    (priority and timeouts are 16-bit, table_id is 8-bit, ports are 32-bit).
    A value that overflows its field must be rejected with a 4xx and must
    never cause a 500 or install a malformed flow.
    """

    net = None

    def setup_method(self, method):
        """Start from a clean controller before each test."""
        self.net.restart_kytos_clean()
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start(start_controller=False)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def _flow_with(self, **overrides):
        """Build a single-flow payload overriding top-level flow fields."""
        flow = copy.deepcopy(BASE_FLOW)
        flow.update(overrides)
        return {"flows": [flow]}

    def _post(self, payload):
        api_url = KYTOS_API + '/flow_manager/v2/flows/%s' % DPID
        return requests.post(api_url, data=json.dumps(payload), timeout=30,
                             headers={'Content-type': 'application/json'})

    def _stored_count(self):
        api_url = KYTOS_API + '/flow_manager/v2/flows/%s' % DPID
        response = requests.get(api_url, timeout=30)
        assert response.status_code == 200, response.text
        return len(response.json()[DPID]["flows"])

    def test_010_out_of_range_fields_are_rejected(self):
        """Overflowing fixed-width fields must return 4xx, never 500, and
        must not install a flow."""
        cases = {
            "priority": 70000,            # > 16-bit (max 65535)
            "idle_timeout": 100000,       # > 16-bit
            "hard_timeout": 100000,       # > 16-bit
            "table_id": 300,              # > 8-bit (max 255)
            "cookie": 2 ** 64,            # > 64-bit
        }
        before = self._stored_count()
        for field, value in cases.items():
            payload = self._flow_with(**{field: value})
            response = self._post(payload)
            assert response.status_code != 500, \
                f"{field}={value} returned 500: {response.text}"
            assert response.status_code == 400, \
                f"{field}={value} expected 400, got " \
                f"{response.status_code}: {response.text}"
        # No malformed flow should have been installed.
        assert self._stored_count() == before

    def test_020_out_of_range_output_port_is_rejected(self):
        """An output-action port above the 32-bit range must be rejected
        with 4xx (not 500) and must not install a flow."""
        before = self._stored_count()
        payload = self._flow_with(
            actions=[{"action_type": "output", "port": 2 ** 32}]
        )
        response = self._post(payload)
        assert response.status_code != 500, response.text
        assert response.status_code == 400, response.text
        assert self._stored_count() == before
