import time
from urllib.parse import quote

import pytest
import requests

from tests.helpers import NetworkTest
from tests.security_helpers import assert_controller_running, find_leaks

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER


class TestE2ETopologyMalformedIds:
    """REST-07: malformed dpid and interface_id values on topology endpoints.

    A malformed ID must get a clean 4xx: never a 5xx, never a 2xx (it must
    not match a real element) and no internals in the body. Only read, enable
    and disable endpoints are used, so the topology is not modified.
    """

    net = None
    topology_xfail = (
        "A non-integer port in interface_id raises ValueError in the topology "
        "handler and returns 500. See kytos-ng/topology#297."
    )
    core_xfail = (
        "A non-integer port in interface_id raises ValueError in "
        "get_interface_by_id and returns 500. See kytos-ng/kytos#649."
    )

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start(start_controller=False)
        cls.net.restart_kytos_clean()
        time.sleep(10)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    @staticmethod
    def assert_rejected(method, path, value):
        """Send ``value`` in ``path`` and expect a clean 4xx."""
        url = f"{KYTOS_API}/topology/v3{path.format(quote(value, safe=''))}"
        response = requests.request(
            method, url, json={} if method == "POST" else None,
            allow_redirects=False, timeout=15)
        answer = f"{method} {url[:120]} -> {response.status_code}: " \
                 f"{response.text[:300]}"
        assert response.status_code < 500, answer
        assert not 200 <= response.status_code < 300, f"accepted: {answer}"
        assert not find_leaks(response.text), answer
        assert_controller_running(KYTOS_API)

    @pytest.mark.parametrize("method,path", [
        ("GET", "/switches/{}/metadata"),
        ("POST", "/switches/{}/enable"),
        ("POST", "/switches/{}/disable"),
        ("POST", "/interfaces/switch/{}/enable"),
        ("POST", "/interfaces/switch/{}/disable"),
    ])
    @pytest.mark.parametrize("dpid", [
        "00:00:00:00:00:00:00:01:",
        "00:00:00:00:00:00:00:01:01",
        "00:00:00:01",
        "ff:ff:ff:ff:ff:ff:ff:ff:ff",
        "00:00:00:00:00:00:00:0g",
        "zz:zz:zz:zz:zz:zz:zz:zz",
        "0000000000000001",
        "00:00:00:00::00:00:01",
        " 00:00:00:00:00:00:00:01",
        "-1",
        "0x0000000000000001",
        ":".join(["\u0660\u0660"] * 7 + ["\u0660\u0661"]),
        "\u202e00:00:00:00:00:00:00:01",
        "00:00:00:00:00:00:00:01\x00",
        "00:00:00:00:00:00:00:01\nX-Injected: 1",
        "../../etc/passwd",
        '{"$ne": null}',
        "0" * 10000,
    ], ids=[
        "trailing_colon", "extra_octet", "too_short", "overflow_72bit",
        "non_hex_letter", "non_hex_word", "no_separators", "double_colon",
        "leading_space", "negative", "hex_prefix", "unicode_digits",
        "rtl_override", "nul_byte", "newline", "traversal", "nosql_operator",
        "oversized",
    ])
    def test_malformed_dpid(self, method, path, dpid):
        """A malformed dpid is rejected cleanly."""
        self.assert_rejected(method, path, dpid)

    @pytest.mark.parametrize("method,path", [
        ("GET", "/interfaces/{}/metadata"),
        ("POST", "/interfaces/{}/enable"),
        ("POST", "/interfaces/{}/disable"),
        ("GET", "/interfaces/{}/tag_ranges"),
    ])
    @pytest.mark.parametrize("interface_id", [
        "00:00:00:00:00:00:00:01:4294967296",
        "00:00:00:00:00:00:00:01:99999999999999999999999",
        "00:00:00:00:00:00:00:01:-1",
        "00:00:00:00:00:00:00:01:1:2",
        ":1",
    ], ids=["overflow_32bit", "huge_number", "negative_port", "extra_field",
            "port_only"])
    def test_malformed_interface_id(self, method, path, interface_id):
        """An interface_id with an out-of-range or misplaced port is
        rejected cleanly."""
        self.assert_rejected(method, path, interface_id)

    @pytest.mark.parametrize("method,path", [
        pytest.param("GET", "/interfaces/{}/metadata", marks=pytest.mark.xfail(
            reason=topology_xfail, strict=False)),
        pytest.param("POST", "/interfaces/{}/enable", marks=pytest.mark.xfail(
            reason=topology_xfail, strict=False)),
        pytest.param("POST", "/interfaces/{}/disable", marks=pytest.mark.xfail(
            reason=topology_xfail, strict=False)),
        pytest.param("GET", "/interfaces/{}/tag_ranges", marks=pytest.mark.xfail(
            reason=core_xfail, strict=False)),
    ])
    @pytest.mark.parametrize("interface_id", [
        "00:00:00:00:00:00:00:01:",
        "00:00:00:00:00:00:00:01:abc",
        "00:00:00:00:00:00:00:01:1.5",
        "00:00:00:00:00:00:00:01:0x1",
        "00:00:00:00:00:00:00:01:1\x00",
    ], ids=["empty_port", "non_numeric_port", "float_port", "hex_port",
            "nul_in_port"])
    def test_non_integer_port(self, method, path, interface_id):
        """An interface_id whose port is not an integer is rejected cleanly."""
        self.assert_rejected(method, path, interface_id)
