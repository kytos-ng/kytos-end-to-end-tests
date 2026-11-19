"""End-to-end tests for the telemetry_int NApp REST API.

Covers enable/disable/redeploy semantics, the ``evc_ids: []`` "all EVCs"
behaviour of every endpoint accepting it, the observability endpoints,
consistency checking and the negative paths. Shapes and convergence live in 95,
mef_eline-driven changes in 97.

Control plane only: status codes and bodies, the ``metadata.telemetry`` state
machine, flow counts split by cookie owner, and telemetry_int's own
``expected_flows`` / ``check_consistency`` as the oracle.

Out of scope on this backend:

- proxy-port scenarios needing a working loop. of_lldp detects the
  ``amlight_intlab`` loops, but kytos-init.sh sets ``LLDP_LOOP_ACTIONS`` to
  disable them, so the proxy-port group only exercises the API surface and
  metadata handlers, which work with a DOWN loop plus ``force=true``. Working
  loops need ``ignored_loops`` on the switches, which file 98 will set up.
- ``flow_manager.flow.error`` -> ``status_reason: ["ofpt_error"]``: provoking a
  genuine OFPT_ERROR is too fragile.
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
        self.wait_nni_links_up(self.net)
        self.delete_all_evcs()
        self.wait_no_evc_flows(self.switches)

    def create_evc(self, vlan_id, uni_a=UNI_A1, uni_z=UNI_Z1, **kwargs):
        """Create a dynamic EVPL and wait for it to be fully provisioned.

        The failover path is part of that wait: enabling INT while mef_eline is
        still provisioning it loses the failover INT subset for good.
        """
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
        self.wait_evc_failover_ready(evc_id)
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
        assert response.status_code == 200, response.text
        assert evc_id in response.json(), response.text

        self.assert_telemetry_metadata(
            evc_id, False, status="DOWN", status_reason=["disabled"]
        )
        self.assert_no_int_flows(self.switches, evc_id)
        self.assert_evc_flow_counts(
            self.switches,
            evc_id,
            self.mef_only(before),
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
        # the enable's flow mods must land first, else the redeploy races them
        self.wait_int_ready(evc_id)

        response = self.int_redeploy([evc_id])
        assert response.status_code == 201, response.text
        assert evc_id in response.json(), response.text

        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.assert_proxy_port_enabled(evc_id, False)
        self.assert_int_consistent(evc_id)
        self.assert_int_flows_match_expected(evc_id, self.switches)

    def test_125_redeploy_negatives(self):
        """Redeploy rejects an EVC that does not have INT enabled."""
        evc_id = self.create_evc(315)

        response = self.int_redeploy([evc_id])
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
                self.mef_only(before[evc_id]),
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
        total_installed = sum(self.count_all_evc_flows(sw).int for sw in self.switches)
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

    def test_170_disable_right_after_enable_leaves_no_int_flows(self):
        """Disabling INT right after the enable returns removes all the INT
        flows it installed.

        The enable used to return with its INT flows only queued to
        flow_manager, so a disable sent right after could read the stored
        flows before they were stored, and leave them behind for good. Several
        EVCs and rounds widen that window."""
        evc_ids = []
        for vlan_id in range(960, 966):
            evc_id = self.create_evc(vlan_id)
            self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)
            evc_ids.append(evc_id)

        for _ in range(5):
            response = self.int_enable(evc_ids)
            assert response.status_code == 201, response.text
            response = self.int_disable(evc_ids)
            assert response.status_code == 200, response.text

            time.sleep(5)
            for evc_id in evc_ids:
                stored = self.get_stored_flows(
                    self.int_cookie_prefix, evc_id, state=("installed", "pending")
                )
                assert not stored, f"EVC {evc_id} INT flows left stored: {stored}"
                self.assert_no_int_flows(self.switches, evc_id)
                self.assert_telemetry_metadata(
                    evc_id, False, status="DOWN", status_reason=["disabled"]
                )

    def enable_switch_and_links(self, dpid):
        """Enable a switch with its interfaces and links, which disabling it
        disabled."""
        response = requests.post(
            f"{self.topo_api}/switches/{dpid}/enable", timeout=self.timeout
        )
        assert response.status_code == 201, response.text
        response = requests.post(
            f"{self.topo_api}/interfaces/switch/{dpid}/enable", timeout=self.timeout
        )
        assert response.status_code == 200, response.text
        response = requests.get(f"{self.topo_api}/links/", timeout=self.timeout)
        assert response.status_code == 200, response.text
        for link_id, link in response.json()["links"].items():
            switches = {link["endpoint_a"]["switch"], link["endpoint_b"]["switch"]}
            if dpid in switches and not link["enabled"]:
                response = requests.post(
                    f"{self.topo_api}/links/{link_id}/enable", timeout=self.timeout
                )
                assert response.status_code == 201, response.text

    def test_172_int_flows_send_failure_sets_failed_status_and_recovers(self):
        """INT flows that flow_manager rejects leave INT enabled DOWN with
        ``flows_install_failed`` on every EVC in the request, reported
        inconsistent by the consistency check. An EVC is redeployed when it
        resumes, else the consistency check ``fix`` redeploys it.

        A static EVC that loses its only path keeps it installed (mef_eline
        EP041), so with its transit switch s5 disabled its mef_eline flows
        stay stored there, and flow_manager rejects the whole bulk INT install,
        which also carries the flows of a static EVC on ``PRIMARY``, since a
        switch in it is disabled."""
        dpid_s5 = "00:00:00:00:00:00:00:05"

        def static_path(path):
            return [
                {"endpoint_a": {"id": link[0]}, "endpoint_b": {"id": link[1]}}
                for link in path
            ]

        resuming_id = self.create_evc(
            972, dynamic_backup_path=False, primary_path=static_path(self.BACKUP)
        )
        self.wait_evc_paths(resuming_id, current=self.BACKUP)
        steady_id = self.create_evc(
            973, dynamic_backup_path=False, primary_path=static_path(self.PRIMARY)
        )
        self.wait_evc_paths(steady_id, current=self.PRIMARY)
        evc_ids = [resuming_id, steady_id]

        try:
            response = requests.post(
                f"{self.topo_api}/switches/{dpid_s5}/disable", timeout=self.timeout
            )
            assert response.status_code == 201, response.text
            self.wait_evc_active(resuming_id, active=False)
            assert self.get_stored_flows(self.mef_cookie_prefix, resuming_id)

            response = self.int_enable(evc_ids)
            assert response.status_code == 500, response.text
            assert "is disabled" in response.text, response.text
            response = self.int_check_consistency(evc_ids)
            assert response.status_code == 200, response.text
            results = response.json()
            for evc_id in evc_ids:
                self.assert_telemetry_metadata(
                    evc_id, True, status="DOWN", status_reason=["flows_install_failed"]
                )
                stored = self.get_stored_flows(
                    self.int_cookie_prefix, evc_id, state=("installed", "pending")
                )
                assert not stored, f"EVC {evc_id} INT flows stored: {stored}"
                assert results[evc_id]["outcome"] == "inconsistent", results
                assert results[evc_id]["status_reason"] == [
                    "flows_install_failed"
                ], results
        finally:
            self.enable_switch_and_links(dpid_s5)

        self.wait_evc_active(resuming_id)
        self.wait_evc_paths(resuming_id, current=self.BACKUP)
        self.assert_telemetry_metadata(
            resuming_id, True, status="UP", status_reason=[], timeout=90
        )
        self.assert_int_flows_match_expected(resuming_id, self.switches)
        self.assert_int_consistent(resuming_id)

        self.wait_telemetry_settled(steady_id)
        self.assert_telemetry_metadata(
            steady_id, True, status="DOWN", status_reason=["flows_install_failed"]
        )
        response = self.int_check_consistency([steady_id], inconsistent_action="fix")
        assert response.status_code == 200, response.text
        assert response.json()[steady_id]["outcome"] == "inconsistent", response.text
        self.assert_telemetry_metadata(steady_id, True, status="UP", status_reason=[])
        self.assert_int_flows_match_expected(steady_id, self.switches)
        self.assert_int_consistent(steady_id)

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

    def test_217_int_trace_walks_int_flows(self):
        """Negative control for ``assert_int_trace_matches_control``.

        Every INT trace in these suites walks the same dpids as its control
        trace, which on its own can't show the INT flows were selected at all.
        Deleting s1's table X INT flows leaves its table 0 INT flows jumping to
        a table where nothing matches, so the INT trace must not reach s6
        while the control trace still does. If it doesn't, the INT
        traces were walking the mef_eline flows all along.
        """
        evc_id = self.create_int_evc(517)
        expected = [self.DPID_S1, self.DPID_S6]
        self.assert_int_trace_matches_control(self.DPID_S1, 1, 517, expected)

        # an EVPL keeps its table X flows on table 2
        self.delete_int_flows_stored(self.DPID_S1, evc_id, table_id=2)
        int_steps = self.assert_no_int_trace(self.DPID_S1, 1, 517, expected)
        assert self.DPID_S6 not in self.trace_dpids(int_steps), int_steps
        control = self.trace(self.DPID_S1, 1, dl_vlan=517)
        assert self.trace_dpids(control) == expected, control

        response = self.int_check_consistency([evc_id], inconsistent_action="fix")
        assert response.status_code == 200, response.text
        self.assert_int_consistent(evc_id)
        self.assert_int_trace_matches_control(self.DPID_S1, 1, 517, expected)

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
        before = self.count_evc_flows(switch, evc_id).int
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
            if self.count_evc_flows(switch, evc_id).int == before:
                break
            time.sleep(3)
        else:
            raise AssertionError(
                f"flow_manager never restored the INT flows on {switch.name}: "
                f"{self.count_evc_flows(switch, evc_id).int} != {before}"
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

    @pytest.mark.xfail(
        reason="returns 500: with proxy_port_enabled false the source flow "
        "builder dereferences the missing dst_uni['proxy_port'] (KeyError) "
        "before ProxyPortRequired can be raised",
        strict=True,
    )
    def test_303_intra_switch_evc_proxy_port_enabled_false_requires_proxy_port(
        self,
    ):
        """An intra-switch EVC needs proxy ports even with the flag off.

        Same contract as test_300: a 409, and nothing installed.
        """
        evc_id = self.create_intra_evc(903)

        response = self.int_enable([evc_id], proxy_port_enabled=False)
        assert response.status_code == 409, response.text
        assert "intra-EVC must use proxy ports" in self.error_description(response)

        self.assert_no_int_flows(self.switches, evc_id)

    def test_305_intra_switch_expected_flows_requires_proxy_port(self):
        """expected_flows 409 on an intra-switch EVC without proxy ports, and
        check_consistency skips it, since it doesn't have INT enabled."""
        evc_id = self.create_intra_evc(905)

        response = self.int_expected_flows_response([evc_id])
        assert response.status_code == 409, response.text
        assert "proxy ports" in self.error_description(response)

        response = self.int_check_consistency([evc_id])
        assert response.status_code == 200, response.text
        assert response.json() == {}, response.text

    def test_310_proxy_port_enabled_true_without_metadata(self):
        """``proxy_port_enabled: true`` needs proxy ports on both UNIs."""
        evc_id = self.create_evc(910)

        response = self.int_enable([evc_id], proxy_port_enabled=True)
        assert response.status_code == 409, response.text
        assert "must have both proxy ports set" in self.error_description(response)
        self.assert_no_int_flows(self.switches, evc_id)

    def test_315_unknown_evc_id(self):
        """Unknown EVC ids are 404."""
        assert self.int_enable([self.UNKNOWN_EVC_ID]).status_code == 404
        assert self.int_disable([self.UNKNOWN_EVC_ID]).status_code == 404
        assert (
            self.int_expected_flows_response([self.UNKNOWN_EVC_ID]).status_code == 404
        )

    @pytest.mark.xfail(
        reason="returns 409: redeploy_int runs _validate_has_int() before the "
        "not found check, and an unknown id maps to {}, so EVCHasNoINT is "
        "raised instead of EVCNotFound",
        strict=True,
    )
    def test_316_unknown_evc_id_redeploy(self):
        """Redeploying an unknown EVC id is a 404, like the other endpoints."""
        response = self.int_redeploy([self.UNKNOWN_EVC_ID])
        assert response.status_code == 404, response.text

    def test_317_unknown_evc_id_forced_disable(self):
        """A forced disable tries even when the EVC isn't found, per openapi.yml,
        so an unknown id is a 200, alone or along with an INT EVC."""
        response = self.int_disable([self.UNKNOWN_EVC_ID], force=True)
        assert response.status_code == 200, response.text

        evc_id = self.create_int_evc(318)
        response = self.int_disable([self.UNKNOWN_EVC_ID, evc_id], force=True)
        assert response.status_code == 200, response.text
        self.assert_telemetry_metadata(
            evc_id, False, status="DOWN", status_reason=["disabled"]
        )
        self.assert_no_int_flows(self.switches, evc_id)

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
        """Every evc_ids endpoint rejects a malformed or missing payload with a 400.

        This is the one place that bypasses INTHelper and calls requests
        directly: the whole point is to send bodies the facade's wrappers would
        never build.
        """
        url = f"{self.int_api}/{path}"
        send = getattr(requests, method)
        timeout = self.timeout

        for payload in (
            {},
            {"evc_ids": "abc"},
            {"evc_ids": [1, 2]},
            {"evc_ids": ["023766c105686749"]},
            {"evc_ids": ["3766C105686749"]},
            {"evc_ids": ["3766c10568674"]},
            {"evc_ids": ["zzzzzzzzzzzzzz"]},
        ):
            response = send(url, json=payload, timeout=timeout)
            assert response.status_code == 400, (payload, response.text)

        response = send(url, timeout=timeout)
        assert response.status_code == 400, response.text

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
        the loop ends up disabled. That makes the API surface testable without
        a working loop: an unforced POST must refuse a DOWN proxy port, and
        ``force=true`` must accept it. No data plane is asserted.
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

    def add_proxy_ports_down(self):
        """Set proxy ports, whose loop is DOWN on this backend, on
        UNI_A1/UNI_Z1. The caller must remove both in a ``finally``, including
        when this fails half way."""
        self.require_looped_interface(f"{self.DPID_S1}:{self.LOOP_PORT_A}")
        self.require_looped_interface(f"{self.DPID_S6}:{self.LOOP_PORT_Z}")
        for intf_id, port in (
            (self.UNI_A1, self.LOOP_PORT_A),
            (self.UNI_Z1, self.LOOP_PORT_Z),
        ):
            response = self.int_add_proxy_port(intf_id, port, force=True)
            assert response.status_code == 200, response.text

    def enable_int_with_proxy_port_down(self, vlan_id, **kwargs):
        """Set proxy ports on UNI_A1/UNI_Z1 and enable INT on a new EVC.

        The loop is DOWN on this backend, so the EVC lands on
        ``status_reason: ["proxy_port_down"]``, which registers it against the
        proxy port. The caller must remove both proxy ports in a ``finally``,
        including when this fails half way.
        """
        self.add_proxy_ports_down()
        evc_id = self.create_evc(vlan_id, **kwargs)
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
        return evc_id

    def assert_proxy_port_down_kept(self, evc_id, duration=20):
        """Assert an EVC that fell back to mef_eline for its proxy port gets no
        INT flow and stays DOWN ``proxy_port_down`` for ``duration`` seconds,
        so a late handling of a mef_eline event is caught."""
        deadline = time.time() + duration
        while time.time() < deadline:
            stored = self.get_stored_flows(
                self.int_cookie_prefix, evc_id, state=("installed", "pending")
            )
            assert not stored, stored
            time.sleep(2)
        self.assert_no_int_flows(self.switches, evc_id)
        self.assert_telemetry_metadata(
            evc_id, True, status="DOWN", status_reason=["proxy_port_down"]
        )

    def test_354_proxy_port_down_evc_has_no_int_flows(self):
        """An EVC enabled while its proxy port is down falls back to mef_eline:
        no INT flows are installed."""
        try:
            evc_id = self.enable_int_with_proxy_port_down(924)
            self.assert_no_int_flows(self.switches, evc_id)
        finally:
            self.int_del_proxy_port(self.UNI_A1, force=True)
            self.int_del_proxy_port(self.UNI_Z1, force=True)

    def test_355_proxy_port_delete_while_in_use(self):
        """A proxy port in use by an INT EVC cannot be deleted without force."""
        try:
            evc_id = self.enable_int_with_proxy_port_down(925)

            response = self.int_del_proxy_port(self.UNI_A1)
            assert response.status_code == 409, response.text
            body = response.json()
            assert "is in use on 1 EVCs" in body["description"], body
            assert body["evc_ids"] == [evc_id], body

            response = self.int_del_proxy_port(self.UNI_A1, force=True)
            assert response.status_code == 200, response.text
            listed = {entry["uni"]["id"] for entry in self.int_list_proxy_ports()}
            assert self.UNI_A1 not in listed, listed
        finally:
            self.int_del_proxy_port(self.UNI_A1, force=True)
            self.int_del_proxy_port(self.UNI_Z1, force=True)

    def test_356_forced_proxy_port_delete_falls_back_to_mef_eline(self):
        """Force-deleting an in-use proxy port disables INT on its EVC.

        The EVC is DOWN with ``proxy_port_down``, not UP, and it must still be disabled,
        its INT flows removed from both the switches and flow_manager, its mef_eline
        flows kept, and the EVC released from the proxy port on the other UNI too.
        """
        try:
            evc_id = self.enable_int_with_proxy_port_down(926)

            response = self.int_del_proxy_port(self.UNI_A1, force=True)
            assert response.status_code == 200, response.text

            self.assert_telemetry_metadata(
                evc_id,
                False,
                status="DOWN",
                status_reason=["proxy_port_metadata_removed"],
            )
            self.assert_no_int_flows(self.switches, evc_id)
            stored = self.get_stored_flows(
                self.int_cookie_prefix, evc_id, state=("installed", "pending")
            )
            assert not stored, stored

            mef_flows = {
                sw.name: self.count_evc_flows(sw, evc_id).mef for sw in self.switches
            }
            assert sum(mef_flows.values()), mef_flows

            # disable_int discards the EVC from both UNIs' proxy ports
            response = self.int_del_proxy_port(self.UNI_Z1)
            assert response.status_code == 200, response.text
        finally:
            self.int_del_proxy_port(self.UNI_A1, force=True)
            self.int_del_proxy_port(self.UNI_Z1, force=True)

    def test_357_proxy_port_down_evc_failover_installs_no_int(self):
        """A dynamic EVC's failover doesn't bring INT back while it's DOWN for
        its proxy port (kytos-ng/telemetry_int#194).

        ``failover_link_down`` and the following ``failover_old_path`` and
        ``failover_deployed`` carry path flows, which used to get their INT
        flows installed although the EVC fell back to mef_eline.
        """
        try:
            evc_id = self.enable_int_with_proxy_port_down(927)
            self.assert_no_int_flows(self.switches, evc_id)

            self.net.net.configLinkStatus("s1", "s6", "down")
            self.net.wait_kytos_links("s1", "s6", status="DOWN")
            self.wait_evc_paths(evc_id, current=self.BACKUP)

            self.assert_proxy_port_down_kept(evc_id)
        finally:
            self.int_del_proxy_port(self.UNI_A1, force=True)
            self.int_del_proxy_port(self.UNI_Z1, force=True)

    def test_358_proxy_port_down_static_evc_reactivation_installs_no_int(self):
        """A static EVC that stops and resumes forwarding doesn't bring INT
        back while it's DOWN for its proxy port (kytos-ng/telemetry_int#194).

        Resuming is ``static.ingress_installed`` (mef_eline EP041), which used
        to install the INT source, push_int included, with no INT sink to pop
        it.
        """
        primary = [
            {"endpoint_a": {"id": link[0]}, "endpoint_b": {"id": link[1]}}
            for link in self.PRIMARY
        ]
        try:
            evc_id = self.enable_int_with_proxy_port_down(
                928, dynamic_backup_path=False, primary_path=primary
            )
            self.assert_no_int_flows(self.switches, evc_id)

            self.net.net.configLinkStatus("s1", "s6", "down")
            self.net.wait_kytos_links("s1", "s6", status="DOWN")
            self.wait_evc_active(evc_id, active=False)
            self.net.net.configLinkStatus("s1", "s6", "up")
            self.net.wait_kytos_links("s1", "s6", status="UP")
            self.wait_evc_active(evc_id)
            self.wait_evc_paths(evc_id, current=self.PRIMARY)

            self.assert_proxy_port_down_kept(evc_id)
        finally:
            self.int_del_proxy_port(self.UNI_A1, force=True)
            self.int_del_proxy_port(self.UNI_Z1, force=True)

    def test_359_proxy_port_down_evc_redeploy_installs_no_int(self):
        """Redeploying INT on an EVC that's DOWN for its proxy port keeps it
        fallen back to mef_eline (kytos-ng/telemetry_int#194)."""
        try:
            evc_id = self.enable_int_with_proxy_port_down(929)
            self.assert_no_int_flows(self.switches, evc_id)

            response = self.int_redeploy([evc_id])
            assert response.status_code == 201, response.text
            assert evc_id in response.json(), response.text

            self.assert_proxy_port_down_kept(evc_id)
        finally:
            self.int_del_proxy_port(self.UNI_A1, force=True)
            self.int_del_proxy_port(self.UNI_Z1, force=True)

    def test_360_proxy_port_down_unused_with_proxy_port_enabled_false(self):
        """An EVC enabled with ``proxy_port_enabled: false`` doesn't use its
        UNIs' proxy ports, so their DOWN loop doesn't set it DOWN
        ``proxy_port_down``: it gets its INT flows."""
        try:
            self.add_proxy_ports_down()
            evc_id = self.create_evc(930)
            response = self.int_enable([evc_id], proxy_port_enabled=False)
            assert response.status_code == 201, response.text

            self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
            self.assert_proxy_port_enabled(evc_id, False)
            self.assert_int_consistent(evc_id)
            self.assert_int_flows_match_expected(evc_id, self.switches)
        finally:
            self.int_del_proxy_port(self.UNI_A1, force=True)
            self.int_del_proxy_port(self.UNI_Z1, force=True)

    def enable_proxy_port_loop(self, dpid, port_a, port_b, timeout=90):
        """Make the ``dpid`` ``port_a``--``port_b`` loop UP, as a working proxy port.

        kytos-init.sh's ``LLDP_LOOP_ACTIONS`` disables loops, so this ignores it
        with the switch ``ignored_loops`` metadata, waits for of_lldp to stop it,
        and writes the ``looped`` metadata of_lldp would have before enabling
        both ports, so the proxy port is resolvable when the loop link comes UP,
        which is what ``handle_pp_link_up`` reacts to. ``restart_kytos_clean``
        wipes it all for the next tests.
        """
        intf_a, intf_b = f"{dpid}:{port_a}", f"{dpid}:{port_b}"
        url = f"{self.topo_api}/switches/{dpid}/metadata"
        response = requests.get(url, timeout=self.timeout)
        assert response.status_code == 200, response.text
        ignored = response.json()["metadata"].get("ignored_loops", [])
        if [port_a, port_b] not in ignored:
            ignored = ignored + [[port_a, port_b]]
        response = requests.post(
            url, json={"ignored_loops": ignored}, timeout=self.timeout
        )
        assert response.status_code in (200, 201), response.text

        deadline = time.time() + timeout
        while any(
            "looped" in self.get_interface(intf_id).get("metadata", {})
            for intf_id in (intf_a, intf_b)
        ):
            assert time.time() < deadline, f"loop {intf_a}--{intf_b} never stopped"
            time.sleep(2)

        response = requests.post(
            f"{self.topo_api}/interfaces/{intf_a}/metadata",
            json={"looped": {"port_numbers": [port_a, port_b]}},
            timeout=self.timeout,
        )
        assert response.status_code in (200, 201), response.text
        self.wait_interface_looped(intf_a, timeout=timeout)

        for intf_id in (intf_a, intf_b):
            response = requests.post(
                f"{self.topo_api}/interfaces/{intf_id}/enable", timeout=self.timeout
            )
            assert response.status_code in (200, 201), response.text
        self.wait_loop_link_up(intf_a, intf_b, timeout=timeout)

    def wait_loop_link_up(self, intf_a, intf_b, timeout=90):
        """Enable the topology link between two interfaces and wait until
        it's UP. Disabling an interface, as of_lldp does on a loop, also
        disables its link, and enabling the interface back doesn't enable it.
        """
        deadline = time.time() + timeout
        link = None
        while time.time() < deadline:
            response = requests.get(f"{self.topo_api}/links/", timeout=self.timeout)
            assert response.status_code == 200, response.text
            link = next(
                (
                    link
                    for link in response.json()["links"].values()
                    if {link["endpoint_a"]["id"], link["endpoint_b"]["id"]}
                    == {intf_a, intf_b}
                ),
                None,
            )
            if link and link.get("status") == "UP":
                return link
            if link and not link.get("enabled"):
                response = requests.post(
                    f"{self.topo_api}/links/{link['id']}/enable",
                    timeout=self.timeout,
                )
                assert response.status_code == 201, response.text
            time.sleep(2)
        raise AssertionError(f"loop link {intf_a}--{intf_b} never came UP: {link}")

    def wait_proxy_port_status(self, uni_id, status="UP", timeout=90):
        """Wait until ``GET /v1/uni/proxy_port`` reports ``uni_id`` with ``status``."""
        deadline = time.time() + timeout
        entry = None
        while time.time() < deadline:
            entry = next(
                (i for i in self.int_list_proxy_ports() if i["uni"]["id"] == uni_id),
                None,
            )
            if entry and entry["proxy_port"].get("status") == status:
                return entry
            time.sleep(2)
        raise AssertionError(f"proxy port of {uni_id} never reached {status}: {entry}")

    def test_361_proxy_port_down_redeploy_then_loop_up_reinstalls_int(self):
        """An EVC DOWN for its proxy port stays fallen back on redeploy, and
        gets INT back once its loops are UP (kytos-ng/telemetry_int#194, #195).

        The loop link up is handled by ``handle_pp_link_up``, now holding the
        EVC locks."""
        try:
            evc_id = self.enable_int_with_proxy_port_down(931)
            self.assert_no_int_flows(self.switches, evc_id)

            response = self.int_redeploy([evc_id])
            assert response.status_code == 201, response.text
            self.assert_proxy_port_down_kept(evc_id, duration=10)

            self.enable_proxy_port_loop(self.DPID_S1, self.LOOP_PORT_A, 16)
            self.enable_proxy_port_loop(self.DPID_S6, self.LOOP_PORT_Z, 16)
            self.wait_proxy_port_status(self.UNI_A1, "UP")
            self.wait_proxy_port_status(self.UNI_Z1, "UP")

            self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
            self.wait_int_ready(evc_id)
            for switch in self.switches:
                if switch.name in ("s1", "s6"):
                    assert self.count_evc_flows(switch, evc_id).int, switch.name
            self.assert_int_consistent(evc_id)
        finally:
            self.int_del_proxy_port(self.UNI_A1, force=True)
            self.int_del_proxy_port(self.UNI_Z1, force=True)
