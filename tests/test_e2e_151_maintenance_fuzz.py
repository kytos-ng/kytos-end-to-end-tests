import time

import pytest

from tests.helpers import NetworkTest
from tests.security_helpers import (MaintenanceWindows, assert_controller_running,
                                    fuzz_settings, load_napp_schema, run_case)

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER


class TestE2EMaintenanceFuzz:
    """FUZZ-05: fuzz every maintenance endpoint from the NApp's openapi.yml.

    Responses must never be a 5xx or leak internals, and the controller must
    keep running. A random ``{mw_id}`` only reaches "not found", so each
    request with one is also sent to a pending window and to a running window
    (not for ``/end``, which would finish it). A window that a request
    changed is replaced, so every request starts from the same state.
    """

    net = None
    schema = load_napp_schema("maintenance", base_url=f"{KYTOS_API}/maintenance")
    extend = r"/\{mw_id\}/extend$"
    extend_xfail = (
        "Extending by a duration the spec allows but datetime cannot hold "
        "raises OverflowError and returns 500. "
        "See kytos-ng/maintenance#140."
    )
    seed_fields = ("id", "status", "start", "end", "switches", "interfaces",
                   "links", "description")
    seeds = None
    seed_ids = None

    @classmethod
    def setup_class(cls):
        cls.seeds = {}
        cls.seed_ids = []
        cls.net = NetworkTest(CONTROLLER, topo_name="looped")
        cls.net.start(start_controller=False)
        cls.net.restart_kytos_clean()
        time.sleep(10)

    @classmethod
    def teardown_class(cls):
        for mw_id in cls.seed_ids:
            MaintenanceWindows.remove(mw_id)
        cls.net.stop()

    @pytest.fixture(autouse=True)
    def controller_survives(self):
        yield
        assert_controller_running(KYTOS_API)

    @classmethod
    def snapshot(cls, mw_id):
        window = MaintenanceWindows.get(mw_id)
        return {key: window.get(key) for key in cls.seed_fields} if window else None

    @classmethod
    def seed(cls, kind):
        """Id of a pending or running window in its original state."""
        if kind in cls.seeds:
            mw_id, snapshot = cls.seeds[kind]
            if cls.snapshot(mw_id) == snapshot:
                return mw_id
            MaintenanceWindows.remove(mw_id)
        if kind == "pending":
            mw_id = MaintenanceWindows.create_pending()
        else:
            mw_id = MaintenanceWindows.create_running(
                end=MaintenanceWindows.utc_in(hours=2), start_delay=2)
        cls.seed_ids.append(mw_id)
        cls.seeds[kind] = (mw_id, cls.snapshot(mw_id))
        return mw_id

    @classmethod
    def run_with_seeds(cls, case):
        """Run the case as generated, then against each seeded window."""
        run_case(case)
        if "mw_id" not in (case.path_parameters or {}):
            return
        kinds = ["pending"] if case.path.endswith("/end") else ["pending", "running"]
        for kind in kinds:
            variant = case.partial_deepcopy()
            variant.path_parameters = {**case.path_parameters,
                                       "mw_id": cls.seed(kind)}
            run_case(variant)

    @schema.exclude(path_regex=extend).parametrize()
    @fuzz_settings()
    def test_fuzz_maintenance(self, case):
        """Every maintenance endpoint except extend handles hostile input."""
        self.run_with_seeds(case)

    @schema.include(path_regex=extend).parametrize()
    @fuzz_settings()
    def test_fuzz_extend(self, request, case):
        """PATCH /v1/{mw_id}/extend handles hostile input cleanly."""
        request.applymarker(pytest.mark.xfail(reason=self.extend_xfail,
                                              strict=False))
        self.run_with_seeds(case)
