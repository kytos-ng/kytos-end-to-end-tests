import time

import pytest

from tests.helpers import NetworkTest
from tests.security_helpers import (assert_controller_running, fuzz_settings,
                                    load_napp_schema, run_case)

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER


class TestE2ETopologyFuzz:
    """FUZZ-03: fuzz every topology endpoint from the NApp's own openapi.yml.

    Valid and invalid generated requests must never get a 5xx or a response
    that leaks internals (traceback, file paths, library names), and the
    controller must keep running. Kytos is restarted clean before each
    endpoint because several operations are destructive.
    """

    net = None
    schema = load_napp_schema("topology", base_url=f"{KYTOS_API}/topology")
    interface_metadata = r"/interfaces/\{interface_id\}/metadata$"
    interface_tags = r"/interfaces/\{interface_id\}/(tag_ranges|special_tags)$"
    metadata_xfail = (
        "A non-integer port in interface_id raises ValueError in the topology "
        "handler and returns 500. See kytos-ng/topology#297."
    )
    tags_xfail = (
        "A non-integer port in interface_id (get_interface_by_id) or an empty "
        "tag range (get_tag_ranges) raises in kytos core and returns 500. "
        "See kytos-ng/kytos#649."
    )

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start(start_controller=False)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    @pytest.fixture(autouse=True)
    def clean_controller(self):
        """Restart Kytos clean before each endpoint; it must survive after."""
        self.net.restart_kytos_clean()
        time.sleep(10)
        yield
        assert_controller_running(KYTOS_API)

    @schema.exclude(path_regex=interface_metadata).exclude(
        path_regex=interface_tags).parametrize()
    @fuzz_settings()
    def test_fuzz_topology(self, case):
        """Every other topology endpoint handles hostile input cleanly."""
        run_case(case)

    @schema.include(path_regex=interface_metadata).parametrize()
    @fuzz_settings()
    def test_fuzz_interface_metadata(self, request, case):
        """GET/POST interface metadata handle a malformed interface_id."""
        request.applymarker(pytest.mark.xfail(reason=self.metadata_xfail,
                                              strict=False))
        run_case(case)

    @schema.include(path_regex=interface_tags).parametrize()
    @fuzz_settings()
    def test_fuzz_interface_tags(self, request, case):
        """tag_ranges and special_tags handle hostile input cleanly."""
        request.applymarker(pytest.mark.xfail(reason=self.tags_xfail,
                                              strict=False))
        run_case(case)
