import time

import pytest
import requests

from tests.helpers import NetworkTest
from tests.security_helpers import (MaintenanceWindows, assert_controller_running,
                                    find_leaks)

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER


class TestE2EMaintenanceBounds:
    """FUZZ-05: crafted date and duration boundaries on maintenance windows.

    Each request must get a clean answer (4xx, or success where noted), never
    a 5xx and never a traceback or file path in the body, and the controller
    and the window list must keep working.
    """

    net = None
    windows = None
    running_1h = None
    overflow_xfail = (
        "Dates and durations that datetime cannot hold raise OverflowError, "
        "which the handlers do not catch, so the API returns 500. "
        "See kytos-ng/maintenance#140."
    )

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER, topo_name="looped")
        cls.net.start(start_controller=False)
        cls.net.restart_kytos_clean()
        time.sleep(10)
        cls.running_1h = MaintenanceWindows.create_running(
            end=MaintenanceWindows.utc_in(hours=1))
        cls.windows = [cls.running_1h]

    @classmethod
    def teardown_class(cls):
        for mw_id in cls.windows or []:
            MaintenanceWindows.remove(mw_id)
        cls.net.stop()

    def teardown_method(self, method):
        assert_controller_running(KYTOS_API)

    @staticmethod
    def assert_clean(response, allow_success=False):
        """No 5xx and no leaked internals; a 4xx unless success is fine."""
        answer = f"HTTP {response.status_code}: {response.text[:300]!r}"
        assert response.status_code < 500, answer
        assert not find_leaks(response.text), answer
        if not allow_success:
            assert 400 <= response.status_code < 500, answer

    @pytest.mark.xfail(reason=overflow_xfail, strict=False)
    def test_extend_window_without_end(self):
        """Extending a running window created without an end (its end is
        datetime.max) is refused or leaves the end unchanged."""
        mw_id = MaintenanceWindows.create_running(end=None)
        self.windows.append(mw_id)
        response = requests.patch(f"{MaintenanceWindows.api}/{mw_id}/extend",
                                  json={"minutes": 1}, timeout=15)
        self.assert_clean(response, allow_success=True)
        assert MaintenanceWindows.get(mw_id)["status"] == "running"

    @pytest.mark.xfail(reason=overflow_xfail, strict=False)
    @pytest.mark.parametrize("payload", [
        {"days": 999999999},
        {"minutes": 10**15},
        {"seconds": 2**63},
        {"hours": 10**12},
    ], ids=["days_at_timedelta_max", "minutes_past_c_int", "seconds_2_pow_63",
            "hours_huge"])
    def test_extend_overflow(self, payload):
        """An extension the spec allows (integer >= 0, no maximum) but
        datetime cannot hold gets a 4xx and leaves the window unchanged."""
        before = MaintenanceWindows.get(self.running_1h)
        response = requests.patch(
            f"{MaintenanceWindows.api}/{self.running_1h}/extend",
            json=payload, timeout=15)
        self.assert_clean(response)
        assert MaintenanceWindows.get(self.running_1h)["end"] == before["end"]

    @pytest.mark.xfail(reason=overflow_xfail, strict=False)
    @pytest.mark.parametrize("start,end", [
        ("9999-12-31T23:00:00-1000", None),
        ("9999-12-31T23:59:59-2359", None),
        (None, "9999-12-31T23:59:59-0100"),
    ], ids=["start_9999_minus_10h", "start_9999_minus_23h59",
            "end_9999_minus_1h"])
    def test_create_far_future_negative_offset(self, start, end):
        """A start or end that is valid locally but past year 9999 in UTC
        does not cause a 500."""
        payload = {
            "description": "far future negative offset",
            "start": start or MaintenanceWindows.utc_in(days=1),
            "switches": [MaintenanceWindows.fake_switch()],
            "force": True,
            "ignore_no_exists": True,
        }
        if end:
            payload["end"] = end
        response = requests.post(MaintenanceWindows.api, json=payload,
                                 timeout=15)
        self.assert_clean(response, allow_success=True)
        if response.status_code == 201:
            self.windows.append(response.json()["mw_id"])

    @pytest.mark.xfail(reason=overflow_xfail, strict=False)
    def test_update_end_far_future_negative_offset(self):
        """The same date through PATCH /v1/{id} does not cause a 500."""
        mw_id = MaintenanceWindows.create_pending()
        self.windows.append(mw_id)
        response = requests.patch(f"{MaintenanceWindows.api}/{mw_id}",
                                  json={"end": "9999-12-31T23:59:59-0100"},
                                  timeout=15)
        self.assert_clean(response, allow_success=True)

    def test_window_list_still_works(self):
        """No request above leaves a record that breaks listing windows."""
        response = requests.get(MaintenanceWindows.api, timeout=15)
        assert response.status_code == 200, response.text[:300]
        assert isinstance(response.json(), list)
