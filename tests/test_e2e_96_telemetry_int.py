"""End-to-end tests for the telemetry_int NApp REST API.

Covers enable/disable/redeploy semantics, the ``evc_ids: []`` "all EVCs"
behaviour of every endpoint accepting it, the observability endpoints,
consistency checking and the negative paths. Shapes and convergence live in 95,
mef_eline-driven changes in 97.

Control plane only: status codes and bodies, the ``metadata.telemetry`` state
machine, flow counts split by cookie owner, and telemetry_int's own
``expected_flows`` / ``check_consistency`` as the oracle.

Out of scope on this backend:

- proxy-port scenarios needing a working loop. p4ofswitch has no proxy-port
  support and ``of_lldp`` disables the ``amlight_intlab`` loops regardless, so
  the proxy-port group only exercises the API surface and metadata handlers,
  which work with a DOWN loop plus ``force=true``.
- ``flow_manager.flow.error`` -> ``status_reason: ["ofpt_error"]``: provoking a
  genuine OFPT_ERROR is too fragile. 95 uses that signal as a
  pipeline-capability detector instead.
- ``redeploy`` returning 500 via ``ProxyPortRequired``: unreachable without an
  INT-enabled intra-switch EVC, which needs proxy ports.
"""

import os
import time

import pytest
import requests

from .helpers import NetworkTest
from .int_helpers import INTHelper


@pytest.mark.skipif(
    os.environ.get("SWITCH_CLASS") not in ("NoviSwitch", "P4OfSwitch")
    or (
        os.environ.get("SWITCH_CLASS") == "NoviSwitch"
        and os.environ.get("NOVIVERSION") != "NW570.6.1"
    ),
    reason="telemetry_int e2e tests need an INT capable switch backend",
)
class TestE2ETelemetryINTApi(INTHelper):
    """End-to-end tests for the telemetry_int REST API."""

    controller = "127.0.0.1"
    net = None
    UNI_A1 = "00:00:00:00:00:00:00:01:1"
    UNI_Z1 = "00:00:00:00:00:00:00:06:1"
    UNI_A2 = "00:00:00:00:00:00:00:01:2"
    UNI_Z2 = "00:00:00:00:00:00:00:06:2"
    DPID_S1 = "00:00:00:00:00:00:00:01"
    DPID_S6 = "00:00:00:00:00:00:00:06"
    # s1:15 <-> s1:16 and s1:13 <-> s1:14 are physical loops in amlight_intlab;
    # s6 has the same pair. They are proxy-port candidates but of_lldp disables
    # them, so every test below uses force=true and asserts no data plane.
    LOOP_PORT_A = 15
    LOOP_PORT_A_ALT = 13
    LOOP_PORT_Z = 15
    # s1:5 is an NNI towards s5, i.e. a port that exists but is not looped
    NOT_LOOPED_PORT = 5
    UNKNOWN_INTF = "00:00:00:00:00:00:00:09:1"
    UNKNOWN_EVC_ID = "aaaaaaaaaaaaaa"
    PRIMARY = [["00:00:00:00:00:00:00:01:7", "00:00:00:00:00:00:00:06:7"]]
    BACKUP = [
        ["00:00:00:00:00:00:00:01:5", "00:00:00:00:00:00:00:05:5"],
        ["00:00:00:00:00:00:00:05:8", "00:00:00:00:00:00:00:06:8"],
    ]

    @classmethod
    def setup_class(cls):
        """Start the topology once and bring up a clean controller."""
        cls.net = NetworkTest(cls.controller, topo_name="amlight_intlab")
        cls.net.start(start_controller=False)
        cls.switches = [cls.net.net.get(f"s{i}") for i in range(1, 7)]
        cls.net.restart_kytos_clean()
        time.sleep(5)

    @classmethod
    def teardown_class(cls):
        """Called once after all tests in the class have finished."""
        cls.net.stop()

    def setup_method(self, method):  # pylint: disable=unused-argument
        """Restart kytos clean before every test.

        A lighter reset (delete the EVCs, wait for their flows to go) saves the
        40-70 s this costs but does not hold: from roughly the fifteenth test
        onwards mef_eline stops computing paths for new EVCs even though the
        reset reports a clean, fully UP topology.
        """
        self.net.config_all_links_up()
        self.net.restart_kytos_clean()
        time.sleep(5)
        # not wait_kytos_links(status="UP"): of_lldp disables the five
        # amlight_intlab loop interfaces, so those links are permanently DOWN
        self.wait_nni_links_up(self.net)
        self.delete_all_evcs()
        self.wait_no_evc_flows(self.switches)

    def create_evc(self, vlan_id, uni_a=UNI_A1, uni_z=UNI_Z1, **kwargs):
        """Create a dynamic EVPL and wait for it to become active."""
        payload = {
            "name": f"Vlan_{vlan_id}",
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": uni_a,
                "tag": {"tag_type": "vlan", "value": vlan_id},
            },
            "uni_z": {
                "interface_id": uni_z,
                "tag": {"tag_type": "vlan", "value": vlan_id},
            },
        }
        payload.update(kwargs)
        evc_id = self.mef_create_evc(payload)
        self.wait_evc_active(evc_id)
        return evc_id

    def create_int_evc(self, vlan_id, **kwargs):
        """Create a dynamic EVC and enable INT on it, returning only once it is
        fully provisioned.

        The failover path is waited for *before* INT is enabled, not after.
        telemetry_int builds its flows from the mef_eline flows that exist at
        enable time, so enabling while mef_eline is still provisioning the
        failover leaves the failover subset to the ``failover_deployed``
        handler and races it: the first run of this suite saw EVCs settle with
        only the current-path INT flows installed (12 of the 22 that
        ``expected_flows`` listed, with the s5 hop carrying mef flows and no
        INT ones) and stay that way. Waiting first makes the enable see a
        complete set.

        ``status: UP`` is also written before the flow mods land, so the flow
        assertion polls.
        """
        evc_id = self.create_evc(vlan_id, **kwargs)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)
        response = self.int_enable([evc_id])
        assert response.status_code == 201, response.text
        self.assert_telemetry_metadata(
            evc_id, True, status="UP", status_reason=[], timeout=90
        )
        self.assert_int_flows_match_expected(evc_id, self.switches)
        return evc_id

    def create_intra_evc(self, vlan_id=900):
        """Create an intra-switch EVC (both UNIs on s1), without proxy ports."""
        return self.create_evc(vlan_id, uni_a=self.UNI_A1, uni_z=self.UNI_A2)

    def wait_telemetry_settled(self, evc_id, timeout=45):
        """Wait until the EVC's telemetry metadata stops changing."""
        deadline = time.time() + timeout
        previous = None
        while time.time() < deadline:
            current = self.get_evc_telemetry(evc_id)
            if current and current == previous:
                return current
            previous = current
            time.sleep(3)
        return self.get_evc_telemetry(evc_id)

    def test_100_enable_idempotency(self):
        """Enabling INT twice conflicts, unless force is used.

        ``force`` is the documented escape hatch for re-running an enable over
        an EVC that telemetry_int already considers INT enabled, so it must
        succeed and leave the EVC consistent rather than duplicating flows.
        """
        evc_id = self.create_int_evc(300)
        self.assert_int_consistent(evc_id)

        response = self.int_enable([evc_id])
        assert response.status_code == 409, response.text
        assert "INT is already enabled" in self.error_description(response)

        response = self.int_enable([evc_id], force=True)
        assert response.status_code == 201, response.text
        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.assert_int_consistent(evc_id)
        self.assert_int_flows_match_expected(evc_id, self.switches)

    def test_105_disable_idempotency(self):
        """Disabling INT removes only the 0xa8 flows, and is a 409 when repeated.

        Splitting the flow counts by cookie owner is the whole point here: the
        EVC must keep forwarding on its mef_eline (0xaa) flows after INT is
        disabled, and a regression that tore those down too would be invisible
        to a combined count.
        """
        evc_id = self.create_int_evc(305)
        before = {sw.name: self.count_evc_flows(sw, evc_id) for sw in self.switches}
        assert any(counts[1] for counts in before.values()), before

        response = self.int_disable([evc_id])
        # the handler returns the default 200; openapi.yml documents 201
        assert response.status_code == 200, response.text
        assert evc_id in response.json(), response.text

        self.assert_telemetry_metadata(
            evc_id, False, status="DOWN", status_reason=["disabled"]
        )
        self.assert_no_int_flows(self.switches, evc_id)
        self.assert_evc_flow_counts(
            self.switches,
            evc_id,
            {name: (counts[0], 0) for name, counts in before.items()},
        )
        assert evc_id not in self.int_list_evcs()

        response = self.int_disable([evc_id])
        assert response.status_code == 409, response.text
        assert "INT isn't enabled" in self.error_description(response)

        response = self.int_disable([evc_id], force=True)
        assert response.status_code == 200, response.text

    def test_120_redeploy(self):
        """Redeploying INT rebuilds the flows and keeps the EVC consistent.

        Also pins that redeploy does not rewrite ``proxy_port_enabled``, unlike
        enable, which always sets it.
        """
        evc_id = self.create_evc(310)
        response = self.int_enable([evc_id], proxy_port_enabled=False)
        assert response.status_code == 201, response.text
        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.assert_proxy_port_enabled(evc_id, False)
        # enable returns once the metadata is written; the flow mods follow
        self.wait_int_flows_installed(evc_id)

        response = self.int_redeploy([evc_id])
        assert response.status_code == 201, response.text
        assert evc_id in response.json(), response.text

        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.assert_proxy_port_enabled(evc_id, False)
        self.assert_int_consistent(evc_id)
        self.assert_int_flows_match_expected(evc_id, self.switches)

    def test_125_redeploy_negatives(self):
        """Redeploy rejects EVCs that do not have INT enabled, and unknown ids."""
        evc_id = self.create_evc(315)

        response = self.int_redeploy([evc_id])
        assert response.status_code == 409, response.text
        assert "INT isn't enabled" in self.error_description(response)

        # 409, not 404: redeploy_int calls _validate_has_int() before
        # _validate_map_enable_evcs(), and an unknown id prefetches as {},
        # which has no telemetry metadata -- so EVCHasNoINT wins the race to
        # raise and the caller never reaches the not-found check.
        response = self.int_redeploy([self.UNKNOWN_EVC_ID])
        assert response.status_code == 409, response.text
        assert "INT isn't enabled" in self.error_description(response)

    def test_130_enable_proxy_port_enabled_false(self):
        """``proxy_port_enabled: false`` skips all proxy-port validation.

        This is the production default for inter-switch EVCs since 2025.2.0 and
        the only mode p4ofswitch can run in, so it is worth asserting
        explicitly rather than relying on the unset-and-therefore-None path.
        """
        evc_id = self.create_evc(320)

        response = self.int_enable([evc_id], proxy_port_enabled=False)
        assert response.status_code == 201, response.text

        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.assert_proxy_port_enabled(evc_id, False)
        self.assert_int_consistent(evc_id)
        self.assert_int_flows_match_expected(evc_id, self.switches)

    def test_140_enable_empty_evc_ids_enables_all_non_int(self):
        """``enable {"evc_ids": []}`` enables INT on every EVC that lacks it."""
        evc_ids = [
            self.create_evc(400),
            self.create_evc(401, uni_a=self.UNI_A2, uni_z=self.UNI_Z2),
        ]

        response = self.int_enable([])
        assert response.status_code == 201, response.text
        assert sorted(response.json()) == sorted(evc_ids), response.text
        for evc_id in evc_ids:
            self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
            self.assert_int_consistent(evc_id)
        assert sorted(self.int_list_evcs()) == sorted(evc_ids)

        extra = self.create_evc(402)
        response = self.int_enable([])
        assert response.status_code == 201, response.text
        assert response.json() == [extra], response.text
        self.assert_telemetry_metadata(extra, True, status="UP", status_reason=[])

        response = self.int_enable([])
        assert response.status_code == 200, response.text
        assert response.json() == [], response.text

    def test_145_disable_empty_evc_ids_disables_all_int(self):
        """``disable {"evc_ids": []}`` disables INT on every INT-enabled EVC."""
        evc_ids = [
            self.create_int_evc(410),
            self.create_int_evc(411, uni_a=self.UNI_A2, uni_z=self.UNI_Z2),
        ]
        before = {
            evc_id: {sw.name: self.count_evc_flows(sw, evc_id) for sw in self.switches}
            for evc_id in evc_ids
        }

        response = self.int_disable([])
        assert response.status_code == 200, response.text
        assert sorted(response.json()) == sorted(evc_ids), response.text

        assert self.int_list_evcs() == {}
        for evc_id in evc_ids:
            self.assert_telemetry_metadata(
                evc_id, False, status="DOWN", status_reason=["disabled"]
            )
            self.assert_no_int_flows(self.switches, evc_id)
            self.assert_evc_flow_counts(
                self.switches,
                evc_id,
                {name: (counts[0], 0) for name, counts in before[evc_id].items()},
            )

        response = self.int_disable([])
        assert response.status_code == 200, response.text
        assert response.json() == [], response.text

    def test_150_redeploy_empty_evc_ids_redeploys_all_int(self):
        """``redeploy {"evc_ids": []}`` targets only INT EVCs, and 404s on none.

        Redeploy is the one endpoint of the five that treats an empty result
        set as an error instead of returning an empty body.
        """
        int_evcs = [
            self.create_int_evc(420),
            self.create_int_evc(421, uni_a=self.UNI_A2, uni_z=self.UNI_Z2),
        ]
        plain_evc = self.create_evc(422)

        response = self.int_redeploy([])
        assert response.status_code == 201, response.text
        assert sorted(response.json()) == sorted(int_evcs), response.text
        assert plain_evc not in response.json()

        for evc_id in int_evcs:
            self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
            self.assert_int_consistent(evc_id)

        response = self.int_disable([])
        assert response.status_code == 200, response.text

        response = self.int_redeploy([])
        assert response.status_code == 404, response.text
        assert "There aren't INT EVCs to redeploy" in self.error_description(response)

    def test_155_expected_flows_empty_evc_ids_covers_all_int(self):
        """``expected_flows {"evc_ids": []}`` covers exactly the INT EVCs."""
        int_evcs = [
            self.create_int_evc(430),
            self.create_int_evc(431, uni_a=self.UNI_A2, uni_z=self.UNI_Z2),
        ]
        self.create_evc(432)

        expected = self.int_expected_flows([])
        assert sorted(expected) == sorted(int_evcs), sorted(expected)

        total_expected = sum(data["count_total"] for data in expected.values())
        total_installed = sum(self.count_all_evc_flows(sw)[1] for sw in self.switches)
        assert total_expected == total_installed, (
            f"expected_flows total {total_expected} != installed INT flows "
            f"{total_installed}"
        )

        response = self.int_disable([])
        assert response.status_code == 200, response.text
        assert self.int_expected_flows([]) == {}

    def test_160_check_consistency_empty_evc_ids_covers_all_int(self):
        """``check_consistency {"evc_ids": []}`` covers exactly the INT EVCs.

        Also drives the ``fix`` action over the whole set at once, which is how
        the periodic consistency job would be invoked in production.
        """
        int_evcs = [
            self.create_int_evc(440),
            self.create_int_evc(441, uni_a=self.UNI_A2, uni_z=self.UNI_Z2),
        ]
        self.create_evc(442)

        response = self.int_check_consistency([], outcome="consistent")
        assert response.status_code == 200, response.text
        assert sorted(response.json()) == sorted(int_evcs), response.text

        broken = int_evcs[0]
        self.delete_int_flows_stored(self.DPID_S1, broken)

        deadline = time.time() + 30
        while time.time() < deadline:
            response = self.int_check_consistency([], outcome="inconsistent")
            assert response.status_code == 200, response.text
            data = response.json()
            if list(data) == [broken]:
                break
            time.sleep(2)
        else:
            raise AssertionError(
                f"expected only {broken} to be inconsistent, got {list(data)}"
            )
        assert data[broken]["missing_flows"], data[broken]
        assert data[broken]["alien_flows"] == [], data[broken]

        response = self.int_check_consistency([], inconsistent_action="fix")
        assert response.status_code == 200, response.text
        for evc_id in int_evcs:
            self.assert_int_consistent(evc_id)

        response = self.int_disable([])
        assert response.status_code == 200, response.text
        response = self.int_check_consistency([])
        assert response.status_code == 200, response.text
        assert response.json() == {}, response.text

    def test_200_get_evc_listing(self):
        """``GET /v1/evc`` lists only the INT-enabled EVCs, in Circuit shape."""
        int_evc = self.create_int_evc(500)
        plain_evc = self.create_evc(501, uni_a=self.UNI_A2, uni_z=self.UNI_Z2)

        evcs = self.int_list_evcs()
        assert list(evcs) == [int_evc], list(evcs)
        assert plain_evc not in evcs

        evc = evcs[int_evc]
        assert evc["uni_a"]["interface_id"] == self.UNI_A1, evc
        assert evc["current_path"], evc
        assert evc["metadata"]["telemetry"]["enabled"] is True, evc

    def test_205_expected_flows_shape(self):
        """``expected_flows`` is internally consistent and matches stored flows.

        Every expected flow must carry the telemetry_int cookie prefix and
        owner, and the ``flow_id`` set must be exactly what flow_manager has
        stored -- that identity is what ``check_consistency`` relies on.
        """
        evc_id = self.create_int_evc(505)
        self.assert_int_consistent(evc_id)

        data = self.int_expected_flows([evc_id])[evc_id]
        assert data["count_total"] == len(data["flows"]), data
        by_table = {int(key): value for key, value in data["count_table"].items()}
        assert sum(by_table.values()) == data["count_total"], data

        for entry in data["flows"]:
            assert {"switch", "flow", "flow_id", "id"} <= set(entry), entry
            assert entry["flow"]["cookie"] >> 56 == self.int_cookie_prefix, entry
            assert entry["flow"]["owner"] == "telemetry_int", entry

        # installed+pending, matching the state filter telemetry_int itself uses:
        # comparing against installed only races a flow that is still going in
        assert {entry["flow_id"] for entry in data["flows"]} == self.stored_flow_ids(
            self.int_cookie_prefix, evc_id, state=("installed", "pending")
        )

    def test_210_check_consistency_consistent(self):
        """A healthy EVC is absent from the inconsistent outcome and vice versa."""
        evc_id = self.create_int_evc(510)

        response = self.int_check_consistency([evc_id], outcome="inconsistent")
        assert response.status_code == 200, response.text
        assert response.json() == {}, response.text

        response = self.int_check_consistency([evc_id], outcome="consistent")
        assert response.status_code == 200, response.text
        result = response.json()[evc_id]
        assert result["outcome"] == "consistent", result
        assert result["missing_flows"] == [], result
        assert result["alien_flows"] == [], result
        assert result["expected_flows"], result

    def test_215_check_consistency_missing_then_fix(self):
        """Missing INT flows are detected and reinstalled by ``fix``.

        The drift has to be injected through flow_manager: telemetry_int diffs
        its expected flows against flow_manager's *stored* flows, so a delete
        that bypasses flow_manager leaves nothing for it to notice.
        """
        evc_id = self.create_int_evc(515)
        self.assert_int_consistent(evc_id)

        self.delete_int_flows_stored(self.DPID_S1, evc_id)

        deadline = time.time() + 30
        while time.time() < deadline:
            response = self.int_check_consistency([evc_id], outcome="inconsistent")
            assert response.status_code == 200, response.text
            data = response.json()
            if evc_id in data and data[evc_id]["missing_flows"]:
                break
            time.sleep(2)
        else:
            raise AssertionError(f"missing flows were never reported: {data}")
        assert data[evc_id]["alien_flows"] == [], data[evc_id]

        response = self.int_check_consistency([evc_id], inconsistent_action="fix")
        assert response.status_code == 200, response.text

        self.assert_int_consistent(evc_id)
        self.assert_int_flows_match_expected(evc_id, self.switches)

    def test_220_check_consistency_alien_then_fix(self):
        """Unexpected INT-cookie flows are detected and removed by ``fix``."""
        evc_id = self.create_int_evc(520)
        self.assert_int_consistent(evc_id)

        self.install_alien_int_flow(self.DPID_S1, evc_id)

        deadline = time.time() + 30
        while time.time() < deadline:
            response = self.int_check_consistency([evc_id], outcome="inconsistent")
            assert response.status_code == 200, response.text
            data = response.json()
            if evc_id in data and data[evc_id]["alien_flows"]:
                break
            time.sleep(2)
        else:
            raise AssertionError(f"alien flows were never reported: {data}")
        assert data[evc_id]["missing_flows"] == [], data[evc_id]

        response = self.int_check_consistency([evc_id], inconsistent_action="fix")
        assert response.status_code == 200, response.text

        self.assert_int_consistent(evc_id)
        self.assert_int_flows_match_expected(evc_id, self.switches)

    def test_225_check_consistency_action_redeploy(self):
        """``inconsistent_action=redeploy`` repairs drift by rebuilding INT."""
        evc_id = self.create_int_evc(525)
        self.assert_int_consistent(evc_id)

        self.delete_int_flows_stored(self.DPID_S1, evc_id)
        deadline = time.time() + 30
        while time.time() < deadline:
            data = self.int_check_consistency([evc_id], outcome="inconsistent").json()
            if evc_id in data:
                break
            time.sleep(2)
        else:
            raise AssertionError("drift was never reported")

        response = self.int_check_consistency([evc_id], inconsistent_action="redeploy")
        assert response.status_code == 200, response.text

        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.assert_int_consistent(evc_id)
        self.assert_int_flows_match_expected(evc_id, self.switches)

    def test_230_check_consistency_action_disable(self):
        """``inconsistent_action=disable`` gives up and records why."""
        evc_id = self.create_int_evc(530)
        self.assert_int_consistent(evc_id)

        self.delete_int_flows_stored(self.DPID_S1, evc_id)
        deadline = time.time() + 30
        while time.time() < deadline:
            data = self.int_check_consistency([evc_id], outcome="inconsistent").json()
            if evc_id in data:
                break
            time.sleep(2)
        else:
            raise AssertionError("drift was never reported")

        response = self.int_check_consistency([evc_id], inconsistent_action="disable")
        assert response.status_code == 200, response.text

        self.assert_telemetry_metadata(
            evc_id, False, status="DOWN", status_reason=["consistency_check"]
        )
        self.assert_no_int_flows(self.switches, evc_id)

    def test_235_switch_side_delete_restored_by_flow_manager(self):
        """INT flows deleted behind flow_manager's back get reinstalled by it.

        telemetry_int must keep reporting the EVC as consistent throughout:
        it compares against stored flows, which this delete does not touch.
        flow_manager's own consistency routine is what repairs the switch, so
        this also guards against INT cookies ending up inside
        ``CONSISTENCY_COOKIE_IGNORED_RANGE``.
        """
        evc_id = self.create_int_evc(535)
        self.assert_int_consistent(evc_id)
        switch = self.switches[0]
        before = self.count_evc_flows(switch, evc_id)[1]
        assert before, "no INT flows to delete on s1"

        self.delete_int_flows_on_switch(switch, evc_id)

        response = self.int_check_consistency([evc_id], outcome="inconsistent")
        assert response.status_code == 200, response.text
        assert response.json() == {}, response.text
        self.assert_int_consistent(evc_id)

        # flow_manager's consistency check restores them; the interval is
        # CONSISTENCY_MIN_VERDICT_INTERVAL (14s in kytos-init.sh)
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.count_evc_flows(switch, evc_id)[1] == before:
                break
            time.sleep(3)
        else:
            raise AssertionError(
                f"flow_manager never restored the INT flows on {switch.name}: "
                f"{self.count_evc_flows(switch, evc_id)[1]} != {before}"
            )
        self.assert_int_flows_match_expected(evc_id, self.switches)

    def test_240_compare_reasons(self):
        """``GET /v1/evc/compare`` reports both kinds of disagreement.

        ``missing_some_int_flows`` when the metadata says INT is on but the
        flows are gone, and ``wrong_metadata_has_int_flows`` the other way
        round -- the second is reachable only by editing the EVC metadata
        directly, behind telemetry_int's back.
        """
        evc_id = self.create_int_evc(540)
        self.assert_compare_clean()

        # compare flags missing_some_int_flows only when the EVC has fewer INT
        # flows than mef_eline flows (22 vs 8 here), so one switch is not
        # enough: s1 alone leaves 13 INT flows. Both UNI switches leaves 4.
        self.delete_int_flows_stored(self.DPID_S1, evc_id)
        self.delete_int_flows_stored(self.DPID_S6, evc_id)
        deadline = time.time() + 30
        while time.time() < deadline:
            compared = self.int_compare()
            if compared:
                break
            time.sleep(2)
        else:
            raise AssertionError("compare never reported the missing flows")
        assert len(compared) == 1, compared
        assert compared[0]["id"] == evc_id, compared
        assert compared[0]["compare_reason"] == ["missing_some_int_flows"], compared

        response = self.int_check_consistency([evc_id], inconsistent_action="fix")
        assert response.status_code == 200, response.text
        self.assert_int_consistent(evc_id)

        response = self.mef_del_metadata_key(evc_id, "telemetry")
        assert response.status_code == 200, response.text

        deadline = time.time() + 30
        while time.time() < deadline:
            compared = self.int_compare()
            if compared and compared[0]["compare_reason"] == [
                "wrong_metadata_has_int_flows"
            ]:
                break
            time.sleep(2)
        else:
            raise AssertionError(
                f"compare never reported wrong_metadata_has_int_flows: {compared}"
            )

        response = self.int_disable([evc_id], force=True)
        assert response.status_code == 200, response.text
        self.assert_compare_clean()

    def test_300_intra_switch_evc_requires_proxy_port(self):
        """An intra-switch EVC cannot have INT without proxy ports.

        ``_validate_proxy_ports_symmetry`` raises ``ProxyPortRequired``, which
        the handler maps to 409.
        """
        evc_id = self.create_intra_evc()

        response = self.int_enable([evc_id])
        assert response.status_code == 409, response.text
        assert "intra-EVC must use proxy ports" in self.error_description(response)

        assert self.get_evc_telemetry(evc_id) == {}, self.get_evc_telemetry(evc_id)
        self.assert_no_int_flows(self.switches, evc_id)

    def test_303_intra_switch_evc_proxy_port_enabled_false_crashes(self):
        """Pins today's 500 when an intra EVC is enabled with the flag off.

        ``proxy_port_enabled: false`` makes ``_validate_map_enable_evcs`` skip
        every proxy-port check, so the clean ``ProxyPortRequired`` in
        ``_build_int_sink_flows_no_proxy_port`` is never reached: the source
        builder runs first and dereferences ``dst_uni["proxy_port"]``, which is
        absent, raising a bare ``KeyError`` that no handler catches. A 409 with
        the same message as test_300 would be the right answer; this asserts
        the current behaviour so a fix is a deliberate change. Nothing is
        installed, because the exception happens while flows are being built.
        """
        evc_id = self.create_intra_evc(903)

        response = self.int_enable([evc_id], proxy_port_enabled=False)
        assert response.status_code == 500, response.text

        self.assert_no_int_flows(self.switches, evc_id)

    def test_305_intra_switch_expected_flows_and_consistency_500(self):
        """Pins today's 500 on an intra-switch EVC without proxy ports.

        ``ProxyPortRequired`` is a ``ProxyPortConflict``, not a
        ``ProxyPortNotFound``, so it falls through the 404/409 clauses of
        ``expected_flows`` and ``check_consistency`` into the generic
        ``UnrecoverableError`` branch. A 409 would be the better answer; this
        asserts the current behaviour so a future fix is a deliberate change
        rather than a surprise.
        """
        evc_id = self.create_intra_evc(905)

        response = self.int_expected_flows_response([evc_id])
        assert response.status_code == 500, response.text
        assert "proxy ports" in self.error_description(response)

        response = self.int_check_consistency([evc_id])
        assert response.status_code == 500, response.text

    def test_310_proxy_port_enabled_true_without_metadata(self):
        """``proxy_port_enabled: true`` needs proxy ports on both UNIs."""
        evc_id = self.create_evc(910)

        response = self.int_enable([evc_id], proxy_port_enabled=True)
        assert response.status_code == 409, response.text
        assert "must have both proxy ports set" in self.error_description(response)
        self.assert_no_int_flows(self.switches, evc_id)

    def test_315_unknown_evc_id(self):
        """Unknown EVC ids are 404, except on redeploy; a forced disable crashes.

        ``force=true`` makes ``_validate_disable_evcs`` skip the not-found
        check, so ``disable_int`` carries on with an empty ``{}`` EVC and
        ``_discard_pps_evc_ids`` dereferences ``evc["uni_a"]`` outside its
        ``try`` -- a bare ``KeyError`` no handler maps, hence 500 rather than
        the 200 the force semantics suggest. Pinned as current behaviour so a
        fix is a deliberate change.
        """
        assert self.int_enable([self.UNKNOWN_EVC_ID]).status_code == 404
        assert self.int_disable([self.UNKNOWN_EVC_ID]).status_code == 404
        assert self.int_disable([self.UNKNOWN_EVC_ID], force=True).status_code == 500
        # redeploy is the exception: _validate_has_int() runs first, so an
        # unknown id is reported as "INT isn't enabled" (409), not not-found
        assert self.int_redeploy([self.UNKNOWN_EVC_ID]).status_code == 409
        assert (
            self.int_expected_flows_response([self.UNKNOWN_EVC_ID]).status_code == 404
        )

    @pytest.mark.parametrize(
        "path,method",
        [
            ("evc/enable", "post"),
            ("evc/disable", "post"),
            ("evc/redeploy", "patch"),
            ("evc/expected_flows", "post"),
            ("evc/check_consistency", "post"),
        ],
    )
    def test_320_malformed_payload(self, path, method):
        """Every evc_ids endpoint rejects a malformed payload with a 400.

        A request with no body at all is the one exception, and which code it
        returns depends on the installed openapi-core; see the comment below.

        This is the one place that bypasses INTHelper and calls requests
        directly: the whole point is to send bodies the facade's wrappers would
        never build.
        """
        url = f"{self.int_api}/{path}"
        send = getattr(requests, method)
        timeout = self.timeout

        for payload in ({}, {"evc_ids": "abc"}, {"evc_ids": [1, 2]}):
            response = send(url, json=payload, timeout=timeout)
            assert response.status_code == 400, (payload, response.text)

        # A request with no body at all depends on the openapi-core version,
        # so accept either outcome rather than pinning a dependency artefact:
        #   <= 0.18.x  StarletteOpenAPIRequest.mimetype does a bare
        #              self.request.headers["Content-Type"]; with neither body
        #              nor header the KeyError escapes
        #              avalidate_openapi_request -> 500
        #   >= 0.19.0  renamed to content_type, defaulting to
        #              "application/octet-stream" per RFC 2616 -> 400
        # This run was on <= 0.18.x and returned 500. 400 is the correct
        # answer and needs only a pin bump on the kytos side.
        response = send(url, timeout=timeout)
        assert response.status_code in (400, 500), response.text

    def test_325_check_consistency_bad_query_params(self):
        """check_consistency validates its query parameter enums."""
        evc_id = self.create_int_evc(915)

        response = self.int_check_consistency([evc_id], outcome="bogus")
        assert response.status_code == 400, response.text

        response = self.int_check_consistency([evc_id], inconsistent_action="bogus")
        assert response.status_code == 400, response.text

    def test_330_proxy_port_api_unknown_interface(self):
        """The proxy-port endpoints 404 on an interface that does not exist."""
        response = self.int_add_proxy_port(self.UNKNOWN_INTF, self.LOOP_PORT_A)
        assert response.status_code == 404, response.text
        assert "not found" in self.error_description(response)

        response = self.int_del_proxy_port(self.UNKNOWN_INTF)
        assert response.status_code == 404, response.text

    def test_335_proxy_port_api_not_looped(self):
        """A port that exists but is not looped cannot be a proxy port."""
        response = self.int_add_proxy_port(self.UNI_A1, self.NOT_LOOPED_PORT)
        assert response.status_code == 404, response.text
        assert "isn't looped" in self.error_description(response)

    def test_340_proxy_port_crud_api_only(self):
        """Proxy-port CRUD over the API, with the loop interfaces DOWN.

        ``of_lldp`` writes the ``looped`` metadata before acting on
        ``LLDP_LOOP_ACTIONS``, so ``ProxyPort.destination`` resolves even though
        the loop ends up disabled. That makes the API surface testable on
        p4ofswitch: an unforced POST must refuse a DOWN proxy port, and
        ``force=true`` must accept it. No data plane is asserted -- this backend
        has no proxy-port support.
        """
        looped = self.require_looped_interface(f"{self.DPID_S1}:{self.LOOP_PORT_A}")
        assert looped["port_numbers"], looped
        try:
            response = self.int_add_proxy_port(self.UNI_A1, self.LOOP_PORT_A)
            assert response.status_code == 409, response.text
            assert "isn't UP" in self.error_description(response)

            response = self.int_add_proxy_port(
                self.UNI_A1, self.LOOP_PORT_A, force=True
            )
            assert response.status_code == 200, response.text

            listed = {
                entry["uni"]["id"]: entry["proxy_port"]
                for entry in self.int_list_proxy_ports()
            }
            assert self.UNI_A1 in listed, listed
            assert listed[self.UNI_A1]["port_number"] == self.LOOP_PORT_A, listed

            response = self.int_add_proxy_port(
                self.UNI_A1, self.LOOP_PORT_A, force=True
            )
            assert response.status_code == 200, response.text

            self.require_looped_interface(f"{self.DPID_S1}:{self.LOOP_PORT_A_ALT}")
            response = self.int_add_proxy_port(
                self.UNI_A1, self.LOOP_PORT_A_ALT, force=True
            )
            assert response.status_code == 200, response.text
            listed = {
                entry["uni"]["id"]: entry["proxy_port"]
                for entry in self.int_list_proxy_ports()
            }
            assert listed[self.UNI_A1]["port_number"] == self.LOOP_PORT_A_ALT, listed

            response = self.int_del_proxy_port(self.UNI_A1, force=True)
            assert response.status_code == 200, response.text
            listed = {entry["uni"]["id"] for entry in self.int_list_proxy_ports()}
            assert self.UNI_A1 not in listed, listed

            response = self.int_del_proxy_port(self.UNI_A1, force=True)
            assert response.status_code == 200, response.text
        finally:
            self.int_del_proxy_port(self.UNI_A1, force=True)

    def test_345_proxy_port_dedicated_validation(self):
        """A proxy port cannot be shared between two UNIs."""
        self.require_looped_interface(f"{self.DPID_S1}:{self.LOOP_PORT_A}")
        try:
            response = self.int_add_proxy_port(
                self.UNI_A1, self.LOOP_PORT_A, force=True
            )
            assert response.status_code == 200, response.text

            response = self.int_add_proxy_port(
                self.UNI_A2, self.LOOP_PORT_A, force=True
            )
            assert response.status_code == 409, response.text
            assert "dedicated proxy_port" in self.error_description(response)
        finally:
            self.int_del_proxy_port(self.UNI_A1, force=True)

    def test_350_proxy_port_asymmetry_validation(self):
        """Adding a proxy port to one UNI of an INT EVC is an asymmetry.

        The check only fires for EVCs telemetry_int already knows about, which
        is why INT has to be enabled first.
        """
        # check the loop first: no point provisioning an EVC for a test that
        # this backend cannot run
        self.require_looped_interface(f"{self.DPID_S1}:{self.LOOP_PORT_A}")
        evc_id = self.create_int_evc(920)
        try:
            response = self.int_add_proxy_port(
                self.UNI_A1, self.LOOP_PORT_A, force=True
            )
            assert response.status_code == 409, response.text
            assert "asymmetry" in self.error_description(response)
            self.assert_int_consistent(evc_id)
        finally:
            self.int_del_proxy_port(self.UNI_A1, force=True)

    def test_355_proxy_port_delete_while_in_use(self):
        """A proxy port in use by an INT EVC cannot be deleted without force.

        With proxy-port metadata on both UNIs, ``_build_int_sink_flows`` takes
        the proxy-port branch even though validation was skipped, and since the
        loop is DOWN on this backend the EVC lands on
        ``status_reason: ["proxy_port_down"]``. That is enough to register the
        EVC against the proxy port, which is what the delete has to refuse.

        The forced delete that follows is asserted as a no-op on the telemetry
        metadata rather than as a fall back to mef_eline -- see the comment at
        that assertion. Exercising ``proxy_port_metadata_removed`` needs a
        proxy port whose loop is actually UP, which this backend does not give.
        """
        self.require_looped_interface(f"{self.DPID_S1}:{self.LOOP_PORT_A}")
        self.require_looped_interface(f"{self.DPID_S6}:{self.LOOP_PORT_Z}")
        try:
            # inside the try: setting the first proxy port and then failing to
            # set the second would otherwise leak metadata into 96's remaining
            # tests, with no finally to clean it up
            for intf_id, port in (
                (self.UNI_A1, self.LOOP_PORT_A),
                (self.UNI_Z1, self.LOOP_PORT_Z),
            ):
                response = self.int_add_proxy_port(intf_id, port, force=True)
                assert response.status_code == 200, response.text

            evc_id = self.create_evc(925)
            response = self.int_enable([evc_id])
            assert response.status_code == 201, response.text

            telemetry = self.wait_telemetry_settled(evc_id)
            # a rejection arrives asynchronously, after the optimistic metadata
            # write; give it time to land before trusting proxy_port_down
            if telemetry.get("enabled"):
                time.sleep(10)
                telemetry = self.get_evc_telemetry(evc_id)
            if not telemetry.get("enabled"):
                pytest.skip(
                    "backend rejected the proxy-port INT flows "
                    f"(telemetry={telemetry}); a proxy-port EVC cannot be "
                    "registered on this backend"
                )
            assert telemetry["status"] == "DOWN", telemetry
            assert telemetry["status_reason"] == ["proxy_port_down"], telemetry

            # A pp-down EVC still gets its INT flows installed:
            # install_int_flows splits only the *metadata* into
            # active/inactive/pp_down and calls _install_int_flows(stored_flows)
            # for all of them in the same gather. Asserted rather than assumed,
            # because it is what makes the stale state after the forced delete
            # below a real leak instead of a cosmetic one.
            pp_down_flows = self.wait_int_flows_installed(evc_id)

            response = self.int_del_proxy_port(self.UNI_A1)
            assert response.status_code == 409, response.text
            body = response.json()
            assert "is in use on 1 EVCs" in body["description"], body
            assert body["evc_ids"] == [evc_id], body

            response = self.int_del_proxy_port(self.UNI_A1, force=True)
            assert response.status_code == 200, response.text
            listed = {entry["uni"]["id"] for entry in self.int_list_proxy_ports()}
            assert self.UNI_A1 not in listed, listed

            # The forced delete does NOT fall the EVC back to mef_eline here,
            # and ``proxy_port_metadata_removed`` is unreachable on this
            # backend: handle_pp_metadata_removed only disables EVCs it finds
            # with api.get_evcs(metadata.telemetry.status="UP"), and this EVC
            # is DOWN precisely because the loop -- and so the proxy port -- is
            # down. The EVC is therefore left enabled, with a status_reason
            # still naming a proxy port that no longer exists, and with the
            # proxy-port INT flows it was given still installed. Worth an
            # upstream question; pinned here as current behaviour.
            self.assert_telemetry_metadata(
                evc_id, True, status="DOWN", status_reason=["proxy_port_down"]
            )
            assert (
                self.stored_flow_ids(
                    self.int_cookie_prefix, evc_id, state=("installed", "pending")
                )
                == pp_down_flows
            )
        finally:
            self.int_del_proxy_port(self.UNI_A1, force=True)
            self.int_del_proxy_port(self.UNI_Z1, force=True)
