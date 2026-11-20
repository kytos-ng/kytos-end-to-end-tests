import time

import pytest

from tests.helpers import NetworkTest
from tests.security_helpers import (assert_controller_running, fuzz_settings,
                                    load_napp_schema, run_case)

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER


class TestE2EPathfinderFuzz:
    """FUZZ-07: fuzz POST /api/kytos/pathfinder/v3/ from its openapi.yml.

    Valid and invalid generated requests must never get a 5xx or a response
    that leaks internals, and the controller must keep running.
    """

    net = None
    # the pathfinder spec paths already include the /api/kytos prefix
    schema = load_napp_schema("pathfinder",
                              base_url=f"http://{CONTROLLER}:8181")

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER, topo_name="multi")
        cls.net.start(start_controller=False)
        cls.net.restart_kytos_clean()
        time.sleep(10)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    @pytest.fixture(autouse=True)
    def controller_survives(self):
        yield
        assert_controller_running(KYTOS_API)

    @schema.parametrize()
    @fuzz_settings()
    def test_fuzz_pathfinder(self, case):
        """Path requests with hostile input are handled cleanly."""
        run_case(case)
