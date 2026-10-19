import json
import time

import requests

from tests.helpers import NetworkTest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER


class TestE2EMefElineInjection:
    """Abuse-case coverage for NoSQL operator injection on mef_eline.

    The bulk metadata endpoint (``POST /v2/evc/metadata``) forwards every entry
    of ``circuit_ids`` into ``UpdateOne({"_id": _id}, ...)``. If a client
    could smuggle a MongoDB operator object (for example ``{"$ne": null}``)
    in place of a circuit id string, the filter would become
    ``{"_id": {"$ne": null}}`` and
    match *every* EVC, turning a single request into a mass metadata write.

    The OpenAPI spec declares ``circuit_ids`` as an array of strings, so the
    expected secure behaviour is that such a request is rejected (4xx) and that
    no unrelated EVC is modified as a side effect.
    """

    net = None

    def setup_method(self, method):
        """Start from a clean controller before each test."""
        self.net.config_all_links_up()
        self.net.restart_kytos_clean()
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start(start_controller=False)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def create_evc(self, vlan_id):
        """Create an inter-switch EVC and return its circuit_id."""
        payload = {
            "name": "Vlan_%s" % vlan_id,
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {"tag_type": "vlan", "value": vlan_id}
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {"tag_type": "vlan", "value": vlan_id}
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        return response.json()['circuit_id']

    def get_metadata(self, circuit_id):
        """Return the metadata dict of an EVC."""
        api_url = KYTOS_API + '/mef_eline/v2/evc/%s/metadata' % circuit_id
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        return response.json()['metadata']

    def test_010_bulk_metadata_rejects_nosql_operator_in_circuit_ids(self):
        """A Mongo operator object in circuit_ids must be rejected, not
        evaluated as an operator against the database."""
        evc_a = self.create_evc(101)
        evc_b = self.create_evc(102)

        # circuit_ids carries a NoSQL operator object instead of an id string.
        payload = {
            "circuit_ids": [{"$ne": None}],
            "injected": "pwned",
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/metadata'
        response = requests.post(api_url, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})

        # Expected secure behaviour: the schema (array of strings) rejects it.
        assert response.status_code == 400, response.text
        # And it must never be mistaken for a 500 (unhandled server error).
        assert response.status_code != 500, response.text

        # No EVC may have received the injected key as a side effect.
        assert "injected" not in self.get_metadata(evc_a)
        assert "injected" not in self.get_metadata(evc_b)

    def test_020_bulk_metadata_operator_does_not_mass_write(self):
        """Even if the operator reached the query, it must not match EVCs that
        were never named: the untargeted EVC keeps its original metadata."""
        evc_a = self.create_evc(103)
        evc_b = self.create_evc(104)

        # Legitimately tag only evc_a so we have a known baseline.
        api_url = KYTOS_API + '/mef_eline/v2/evc/%s/metadata' % evc_a
        response = requests.post(api_url, data=json.dumps({"owner": "a"}),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text

        # Attempt a mass write via an operator that would match every _id.
        payload = {
            "circuit_ids": [{"$ne": None}],
            "mass": "all",
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/metadata'
        requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})

        # evc_b was never targeted and must remain untouched.
        metadata_b = self.get_metadata(evc_b)
        assert "mass" not in metadata_b
        assert metadata_b == {}
