import json
import time

import pytest
import requests

from tests.helpers import NetworkTest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER

XFAIL_REASON = (
    "A dotted metadata key is accepted and kept literal in memory, but after "
    "a controller restart it is reloaded from MongoDB as a nested document "
    "(e.g. {'x.y': 'z'} becomes {'x': {'y': 'z'}}), so the in-memory and "
    "persisted forms diverge. See kytos-ng/topology#296."
)


class TestE2ETopologyMetadataInjection:
    """Abuse-case coverage for crafted metadata keys on topology.

    Switch, interface and link metadata are each stored as a sub-document
    under a ``metadata`` field, persisted with a ``{f"metadata.{k}": v}``
    ``$set`` expression. MongoDB treats a dot in a field name as a path
    separator, so a crafted key such as ``"a.b.c"`` is expanded into a nested
    document ``{"a": {"b": {"c": ...}}}`` on reload instead of being kept as a
    literal key.

    The expected secure behaviour is that a crafted key (one containing dots,
    or one that looks like a Mongo operator such as ``$set``/``$where``) is
    either rejected or stored and returned verbatim as a single literal key,
    never silently turned into a nested structure or evaluated as an operator,
    and that the request never triggers a 500.
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

    def get_switch_metadata(self, switch_id):
        """Return the metadata dict of a switch."""
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata' % switch_id
        response = requests.get(api_url, timeout=30)
        assert response.status_code == 200, response.text
        return response.json()['metadata']

    def get_interface_metadata(self, interface_id):
        """Return the metadata dict of an interface."""
        api_url = KYTOS_API + \
            '/topology/v3/interfaces/%s/metadata' % interface_id
        response = requests.get(api_url, timeout=30)
        assert response.status_code == 200, response.text
        return response.json()['metadata']

    def get_link_metadata(self, link_id):
        """Return the metadata dict of a link."""
        api_url = KYTOS_API + '/topology/v3/links/%s/metadata' % link_id
        response = requests.get(api_url, timeout=30)
        assert response.status_code == 200, response.text
        return response.json()['metadata']

    def any_link_id(self):
        """Return any discovered link_id, or None if none is available."""
        api_url = KYTOS_API + '/topology/v3/links'
        response = requests.get(api_url, timeout=30)
        assert response.status_code == 200, response.text
        links = response.json()['links']
        return next(iter(links), None)

    # ---- switch metadata ------------------------------------------------

    def test_010_switch_dotted_key_stored_literally(self):
        """A dotted switch-metadata key must round-trip as a single literal
        key and must not be expanded into a nested document."""
        switch_id = "00:00:00:00:00:00:00:01"

        payload = {"a.b.c": "v1", "plain": "ok"}
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata' % switch_id
        response = requests.post(
            api_url, data=json.dumps(payload), timeout=30,
            headers={'Content-type': 'application/json'})

        assert response.status_code != 500, response.text
        assert response.status_code in (201, 400), response.text
        if response.status_code == 400:
            return

        metadata = self.get_switch_metadata(switch_id)
        assert metadata.get("plain") == "ok"
        assert metadata.get("a.b.c") == "v1", metadata
        assert "a" not in metadata, metadata

    @pytest.mark.xfail(reason=XFAIL_REASON, strict=False)
    def test_020_switch_dotted_key_survives_restart(self):
        """A dotted switch-metadata key must persist unchanged across a
        controller restart (no silent nesting on reload)."""
        switch_id = "00:00:00:00:00:00:00:01"

        payload = {"x.y": "z"}
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata' % switch_id
        response = requests.post(
            api_url, data=json.dumps(payload), timeout=30,
            headers={'Content-type': 'application/json'})
        assert response.status_code != 500, response.text
        if response.status_code != 201:
            return

        self.net.start_controller(clean_config=False, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(10)

        metadata = self.get_switch_metadata(switch_id)
        assert metadata.get("x.y") == "z", metadata
        assert "x" not in metadata, metadata

    # ---- interface metadata ---------------------------------------------

    def test_030_interface_dotted_key_stored_literally(self):
        """A dotted interface-metadata key must round-trip literally."""
        interface_id = "00:00:00:00:00:00:00:01:1"

        payload = {"a.b.c": "v1", "plain": "ok"}
        api_url = KYTOS_API + \
            '/topology/v3/interfaces/%s/metadata' % interface_id
        response = requests.post(
            api_url, data=json.dumps(payload), timeout=30,
            headers={'Content-type': 'application/json'})

        assert response.status_code != 500, response.text
        assert response.status_code in (201, 400), response.text
        if response.status_code == 400:
            return

        metadata = self.get_interface_metadata(interface_id)
        assert metadata.get("plain") == "ok"
        assert metadata.get("a.b.c") == "v1", metadata
        assert "a" not in metadata, metadata

    @pytest.mark.xfail(reason=XFAIL_REASON, strict=False)
    def test_040_interface_dotted_key_survives_restart(self):
        """A dotted interface-metadata key must persist unchanged across a
        controller restart (no silent nesting on reload)."""
        interface_id = "00:00:00:00:00:00:00:01:1"

        payload = {"x.y": "z"}
        api_url = KYTOS_API + \
            '/topology/v3/interfaces/%s/metadata' % interface_id
        response = requests.post(
            api_url, data=json.dumps(payload), timeout=30,
            headers={'Content-type': 'application/json'})
        assert response.status_code != 500, response.text
        if response.status_code != 201:
            return

        self.net.start_controller(clean_config=False, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(10)

        metadata = self.get_interface_metadata(interface_id)
        assert metadata.get("x.y") == "z", metadata
        assert "x" not in metadata, metadata

    # ---- link metadata --------------------------------------------------

    @pytest.mark.xfail(reason=XFAIL_REASON, strict=False)
    def test_050_link_dotted_key_survives_restart(self):
        """A dotted link-metadata key must persist unchanged across a
        controller restart (no silent nesting on reload)."""
        # Enable everything so links are discovered via LLDP.
        self.net.restart_kytos_clean()
        time.sleep(5)

        link_id = self.any_link_id()
        if not link_id:
            pytest.skip("no link discovered in the topology")

        payload = {"x.y": "z"}
        api_url = KYTOS_API + '/topology/v3/links/%s/metadata' % link_id
        response = requests.post(
            api_url, data=json.dumps(payload), timeout=30,
            headers={'Content-type': 'application/json'})
        assert response.status_code != 500, response.text
        if response.status_code != 201:
            return

        self.net.start_controller(clean_config=False, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(10)

        metadata = self.get_link_metadata(link_id)
        assert metadata.get("x.y") == "z", metadata
        assert "x" not in metadata, metadata

    # ---- operator-like metadata keys ------------------------------------

    def test_060_switch_operator_key_handled_safely(self):
        """A metadata key equal to a Mongo operator ($set) must be rejected
        or kept as a literal key, and must never trigger a 500."""
        switch_id = "00:00:00:00:00:00:00:01"

        payload = {"$set": "pwned", "plain": "ok"}
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata' % switch_id
        response = requests.post(
            api_url, data=json.dumps(payload), timeout=30,
            headers={'Content-type': 'application/json'})

        assert response.status_code != 500, response.text
        assert response.status_code in (201, 400), response.text
        if response.status_code == 400:
            return

        metadata = self.get_switch_metadata(switch_id)
        assert metadata.get("plain") == "ok"
        assert metadata.get("$set") == "pwned", metadata

    def test_070_switch_operator_key_survives_restart(self):
        """An operator-like metadata key, if accepted, must persist unchanged
        across a controller restart and must not corrupt the document."""
        switch_id = "00:00:00:00:00:00:00:01"

        payload = {"$where": "1", "plain": "ok"}
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata' % switch_id
        response = requests.post(
            api_url, data=json.dumps(payload), timeout=30,
            headers={'Content-type': 'application/json'})
        assert response.status_code != 500, response.text
        if response.status_code != 201:
            return

        self.net.start_controller(clean_config=False, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(10)

        metadata = self.get_switch_metadata(switch_id)
        assert metadata.get("plain") == "ok", metadata
        assert metadata.get("$where") == "1", metadata
