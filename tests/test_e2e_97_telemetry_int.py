"""End-to-end tests for telemetry_int reacting to mef_eline changes.

Every mutation goes through the mef_eline API; telemetry_int is only read. That
is the production shape -- INT is requested at EVC creation with
``metadata.telemetry_request`` and telemetry_int then follows the EVC through
kytos events alone. Shapes and convergence live in 95, the REST API in 96.

Flow counts are ``{switch_name: (n_mef_eline_flows, n_int_flows)}`` by cookie
owner (mef_eline 0xaa, telemetry_int 0xa8). ``DYNAMIC_EVPL_FLOWS`` is a dynamic
EVPL on ``PRIMARY`` with ``BACKUP`` failover; ``STATIC_PRIMARY_FLOWS`` is a
static EVC on ``PRIMARY``, which has no pre-provisioned failover and so touches
the two UNI switches only.

Out of scope: ``flow_manager.flow.error`` (provoking a real OFPT_ERROR is too
fragile) and anything needing working proxy ports: kytos-init.sh has of_lldp
disable the ``amlight_intlab`` loops, and file 98 will set up ``ignored_loops``.
"""

import os
import time

import pytest

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
class TestE2ETelemetryINTMefEline(INTHelper):
    """End-to-end tests for telemetry_int driven by mef_eline changes."""

    controller = "127.0.0.1"
    net = None
    UNI_A1 = "00:00:00:00:00:00:00:01:1"
    UNI_Z1 = "00:00:00:00:00:00:00:06:1"
    UNI_A2 = "00:00:00:00:00:00:00:01:2"
    UNI_Z2 = "00:00:00:00:00:00:00:06:2"
    DPID_S1 = "00:00:00:00:00:00:00:01"
    DPID_S2 = "00:00:00:00:00:00:00:02"
    DPID_S5 = "00:00:00:00:00:00:00:05"
    DPID_S6 = "00:00:00:00:00:00:00:06"
    PRIMARY = [["00:00:00:00:00:00:00:01:7", "00:00:00:00:00:00:00:06:7"]]
    BACKUP = [
        ["00:00:00:00:00:00:00:01:5", "00:00:00:00:00:00:00:05:5"],
        ["00:00:00:00:00:00:00:05:8", "00:00:00:00:00:00:00:06:8"],
    ]
    DYNAMIC_EVPL_FLOWS = {
        "s1": (3, 9),
        "s2": (0, 0),
        "s3": (0, 0),
        "s4": (0, 0),
        "s5": (2, 4),
        "s6": (3, 9),
    }
    STATIC_PRIMARY_FLOWS = {
        "s1": (2, 6),
        "s2": (0, 0),
        "s3": (0, 0),
        "s4": (0, 0),
        "s5": (0, 0),
        "s6": (2, 6),
    }

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

        Same reasoning as file 96: a reset that only deletes the EVCs leaves
        enough controller state behind that mef_eline eventually stops
        computing paths for new EVCs, and every wait then burns its budget.
        ``test_145`` still restarts the controller itself, keeping the database
        so its EVC survives -- that happens inside the test, after this.
        """
        self.net.config_all_links_up()
        self.net.restart_kytos_clean()
        time.sleep(5)
        # not wait_kytos_links(status="UP"): of_lldp disables the five
        # amlight_intlab loop interfaces, so those links are permanently DOWN
        self.wait_nni_links_up(self.net)
        # test_125 downs a UNI link, which is a host link and so is covered by
        # no link wait at all; the UNIs must be active before the next EVC
        for intf_id in (self.UNI_A1, self.UNI_Z1, self.UNI_A2, self.UNI_Z2):
            self.wait_interface_active(intf_id, active=True)
        self.delete_all_evcs()
        self.wait_no_evc_flows(self.switches)

    @staticmethod
    def path_payload(path):
        """Turn a ``[[endpoint_a_id, endpoint_b_id], ...]`` path into a payload."""
        return [
            {"endpoint_a": {"id": link[0]}, "endpoint_b": {"id": link[1]}}
            for link in path
        ]

    def create_evc(self, vlan_id, uni_a=UNI_A1, uni_z=UNI_Z1, **kwargs):
        """Create an EVPL through mef_eline and wait for it to become active."""
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

    def create_int_requested_evc(self, vlan_id, extra_metadata=None, **kwargs):
        """Create an EVC that asks for INT itself, via ``telemetry_request``.

        No telemetry_int REST call is made: ``mef_eline.deployed`` is what
        triggers ``enable_int``.
        """
        metadata = {"telemetry_request": {}}
        metadata.update(extra_metadata or {})
        return self.create_evc(vlan_id, metadata=metadata, **kwargs)

    def create_int_evc_via_api(self, vlan_id, **kwargs):
        """Create an EVC and enable INT through the telemetry_int API.

        Used by the lifecycle group, where the enablement path is not what is
        under test.
        """
        evc_id = self.create_evc(vlan_id, **kwargs)
        # wait for the failover *before* enabling: telemetry_int builds its
        # flows from the mef_eline flows that exist at enable time, so enabling
        # mid-provisioning leaves the failover subset to the
        # failover_deployed handler and races it (file 96 saw EVCs settle with
        # only the current-path INT flows installed)
        if kwargs.get("dynamic_backup_path", True):
            self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)
        response = self.int_enable([evc_id])
        assert response.status_code == 201, response.text
        # status UP is written before the flow mods land
        self.assert_telemetry_metadata(
            evc_id, True, status="UP", status_reason=[], timeout=90
        )
        self.assert_int_flows_match_expected(evc_id, self.switches)
        return evc_id

    def test_005_telemetry_request_enables_dynamic_evc(self):
        """A dynamic EVC asking for INT at creation gets it, with no REST call.

        ``on_evc_deployed`` takes its ``telemetry_request`` branch and calls
        ``enable_int(force=True, set_proxy_port_metadata=True)``, so the EVC
        also ends up with a ``proxy_port_enabled`` key -- None here, because
        the request did not ask for one.
        """
        evc_id = self.create_int_requested_evc(600)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)

        self.assert_telemetry_metadata(
            evc_id, True, status="UP", status_reason=[], prior={}
        )
        assert "telemetry_request" in self.get_evc(evc_id)["metadata"]
        if not self.lost_write_recorded():
            # both read the metadata the lost write would have set
            self.assert_proxy_port_enabled(evc_id, None)
            assert evc_id in self.int_list_evcs()

        self.assert_evc_flow_counts(self.switches, evc_id, self.DYNAMIC_EVPL_FLOWS)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(
            evc_id, [self.DPID_S1, self.DPID_S6], hop_dpids=[self.DPID_S5]
        )
        self.assert_int_consistent(evc_id)
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 600, [self.DPID_S1, self.DPID_S6]
        )
        self.xfail_lost_writes()

    def test_010_telemetry_request_enables_static_evc(self):
        """A static EVC asking for INT at creation gets it too.

        Worth its own test because the bootstrap happens on
        ``mef_eline.deployed``, and a static EVC deploys without any path
        computation -- so this proves the trigger does not depend on dynamic
        path selection.
        """
        evc_id = self.create_int_requested_evc(
            605,
            dynamic_backup_path=False,
            primary_path=self.path_payload(self.PRIMARY),
            backup_path=self.path_payload(self.BACKUP),
        )
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=[])

        self.assert_telemetry_metadata(
            evc_id, True, status="UP", status_reason=[], prior={}
        )
        if not self.lost_write_recorded():
            self.assert_proxy_port_enabled(evc_id, None)
        self.assert_evc_flow_counts(self.switches, evc_id, self.STATIC_PRIMARY_FLOWS)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(evc_id, [self.DPID_S1, self.DPID_S6])
        self.assert_int_consistent(evc_id)
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 605, [self.DPID_S1, self.DPID_S6]
        )
        self.xfail_lost_writes()

    def test_015_telemetry_request_with_proxy_port_enabled_false(self):
        """``proxy_port_enabled: false`` alongside the request is honoured.

        This is what an operator on a backend without proxy ports would
        actually set, so the flag has to survive the handler reading it off the
        EVC metadata and persisting it.
        """
        evc_id = self.create_int_requested_evc(
            610, extra_metadata={"proxy_port_enabled": False}
        )

        self.assert_telemetry_metadata(
            evc_id, True, status="UP", status_reason=[], prior={}
        )
        if not self.lost_write_recorded():
            self.assert_proxy_port_enabled(evc_id, False)
        self.assert_evc_flow_counts(self.switches, evc_id, self.DYNAMIC_EVPL_FLOWS)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(
            evc_id, [self.DPID_S1, self.DPID_S6], hop_dpids=[self.DPID_S5]
        )
        self.assert_int_consistent(evc_id)
        self.xfail_lost_writes()

    def test_020_telemetry_request_with_proxy_port_enabled_true_fails(self):
        """``proxy_port_enabled: true`` without proxy ports fails, and says so.

        ``enable_int`` raises ``ProxyPortRequired``; because this path has no
        HTTP response to carry the error, the handler records the exception
        class name in ``status_reason`` instead. The EVC itself must keep
        forwarding -- only INT failed.
        """
        evc_id = self.create_int_requested_evc(
            615, extra_metadata={"proxy_port_enabled": True}
        )

        self.assert_telemetry_metadata(
            evc_id, False, status="DOWN", status_reason=["ProxyPortRequired"]
        )
        assert evc_id not in self.int_list_evcs()

        self.assert_evc_flow_counts(
            self.switches,
            evc_id,
            {name: (counts[0], 0) for name, counts in self.DYNAMIC_EVPL_FLOWS.items()},
        )

    def test_025_telemetry_request_added_after_deploy_enables_int_on_redeploy(self):
        """A redeploy is what feeds ``telemetry_request`` to telemetry_int.

        ``telemetry_request`` is only read on ``mef_eline.deployed``, and adding
        it to a live EVC emits no such event -- ``add_metadata`` is
        ``extend_metadata`` then ``sync()``, with no ``deploy()`` on that path.

        The absence window in between is deliberately not asserted: the
        consistency loop and ``handle_evc_need_redeploy`` both deploy an EVC
        that is enabled but inactive, and a link flap is enough to put one
        there on this topology, so "nothing happened yet" races an event the
        test does not control.
        """
        evc_id = self.create_evc(620)
        assert self.get_evc_telemetry(evc_id) == {}

        response = self.mef_set_metadata(evc_id, {"telemetry_request": {}})
        assert response.status_code == 201, response.text
        assert "telemetry_request" in self.get_evc(evc_id)["metadata"]

        response = self.mef_redeploy_evc(evc_id)
        assert response.status_code == 202, response.text

        self.assert_telemetry_metadata(
            evc_id, True, status="UP", status_reason=[], timeout=90, prior={}
        )
        self.assert_evc_flow_counts(self.switches, evc_id, self.DYNAMIC_EVPL_FLOWS)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(
            evc_id, [self.DPID_S1, self.DPID_S6], hop_dpids=[self.DPID_S5]
        )
        self.assert_int_consistent(evc_id)
        if not self.lost_write_recorded():
            # compare reads the metadata, so a lost write fails it too
            assert self.int_compare() == [], self.int_compare()
        self.xfail_lost_writes()

    def test_030_telemetry_request_ignored_when_telemetry_present(self):
        """With ``telemetry`` already set, a redeploy redeploys rather than enables.

        ``on_evc_deployed`` checks ``telemetry`` first, so the
        ``telemetry_request`` branch must not run a second enable (which would
        raise EVCHasINT) for an EVC that already has INT.
        """
        evc_id = self.create_int_evc_via_api(625)
        before_flows = {
            sw.name: self.count_evc_flows(sw, evc_id) for sw in self.switches
        }

        response = self.mef_set_metadata(evc_id, {"telemetry_request": {}})
        assert response.status_code == 201, response.text
        prior = self.get_evc_telemetry(evc_id)
        response = self.mef_redeploy_evc(evc_id)
        assert response.status_code == 202, response.text

        self.assert_telemetry_rewritten(evc_id, prior)
        self.assert_evc_flow_counts(self.switches, evc_id, before_flows)
        self.assert_int_consistent(evc_id)
        self.xfail_lost_writes()

    def test_035_telemetry_request_removed_does_not_disable(self):
        """Removing ``telemetry_request`` leaves INT enabled.

        It is a one-shot trigger, not a desired-state flag. If anyone ever
        wires its removal into a disable, this test is what notices.
        """
        evc_id = self.create_int_requested_evc(630)
        self.assert_telemetry_metadata(
            evc_id, True, status="UP", status_reason=[], prior={}
        )
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        before_flows = {
            sw.name: self.count_evc_flows(sw, evc_id) for sw in self.switches
        }

        response = self.mef_del_metadata_key(evc_id, "telemetry_request")
        assert response.status_code == 200, response.text
        assert "telemetry_request" not in self.get_evc(evc_id)["metadata"]

        time.sleep(10)
        self.assert_evc_flow_counts(self.switches, evc_id, before_flows)
        self.assert_int_consistent(evc_id)
        if not self.lost_write_recorded():
            self.assert_telemetry_metadata(
                evc_id, True, status="UP", status_reason=[]
            )
        self.xfail_lost_writes()

    def test_100_evc_disabled_via_mef_eline(self):
        """Disabling the EVC withdraws every flow but keeps INT requested.

        ``mef_eline.undeployed`` makes telemetry_int remove its flows while
        leaving ``telemetry.enabled`` true: INT is still wanted, it just has
        nothing to instrument. The EVC therefore stays in ``GET /v1/evc``.
        """
        evc_id = self.create_int_evc_via_api(700)
        self.assert_int_consistent(evc_id)

        response = self.mef_patch_evc(evc_id, {"enabled": False})
        assert response.status_code == 200, response.text

        self.assert_telemetry_metadata(
            evc_id, True, status="DOWN", status_reason=["undeployed"]
        )
        self.assert_no_evc_flows(self.switches, evc_id)
        assert evc_id in self.int_list_evcs()
        self.assert_no_int_trace(self.DPID_S1, 1, 700, [self.DPID_S1, self.DPID_S6])

    def test_105_evc_reenabled_via_mef_eline(self):
        """Re-enabling the EVC reinstalls INT from metadata alone.

        On the way back up ``mef_eline.deployed`` fires with
        ``telemetry.enabled`` already true, so telemetry_int redeploys rather
        than enabling -- it has to rebuild every flow from scratch because they
        were all withdrawn.
        """
        evc_id = self.create_int_evc_via_api(705)

        response = self.mef_patch_evc(evc_id, {"enabled": False})
        assert response.status_code == 200, response.text
        self.assert_telemetry_metadata(
            evc_id, True, status="DOWN", status_reason=["undeployed"]
        )
        self.assert_no_evc_flows(self.switches, evc_id)

        response = self.mef_patch_evc(evc_id, {"enabled": True})
        assert response.status_code == 200, response.text
        self.wait_evc_active(evc_id)
        # the failover has to be back before s5 can be asserted as a hop switch;
        # assert_int_flows_match_expected is self-consistent and would pass
        # while it is still missing
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)

        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.assert_evc_flow_counts(self.switches, evc_id, self.DYNAMIC_EVPL_FLOWS)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(
            evc_id, [self.DPID_S1, self.DPID_S6], hop_dpids=[self.DPID_S5]
        )
        self.assert_int_consistent(evc_id)
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 705, [self.DPID_S1, self.DPID_S6]
        )

    def test_110_evc_deleted_via_mef_eline(self):
        """Deleting the EVC cleans up its INT flows and its INT bookkeeping."""
        evc_id = self.create_int_evc_via_api(710)
        self.assert_int_consistent(evc_id)

        response = self.mef_delete_evc(evc_id)
        assert response.status_code == 200, response.text

        self.assert_no_evc_flows(self.switches, evc_id)
        assert evc_id not in self.int_list_evcs()
        assert self.int_expected_flows_response([evc_id]).status_code == 404
        self.assert_compare_clean()
        assert evc_id in self.mef_list_evcs(archived=True)
        assert evc_id not in self.mef_list_evcs(archived=False)

    def test_115_evc_deleted_while_telemetry_request_only(self):
        """Deleting an EVC that enabled INT via ``telemetry_request`` is the same.

        The delete path must not depend on how INT was switched on.
        """
        evc_id = self.create_int_requested_evc(715)
        self.assert_telemetry_metadata(
            evc_id, True, status="UP", status_reason=[], prior={}
        )

        response = self.mef_delete_evc(evc_id)
        assert response.status_code == 200, response.text

        self.assert_no_evc_flows(self.switches, evc_id)
        assert evc_id not in self.int_list_evcs()
        self.assert_compare_clean()
        self.xfail_lost_writes()

    def test_120_evc_redeployed_via_mef_eline(self):
        """A mef_eline redeploy rebuilds the INT flows along with the mef ones.

        ``try_avoid_same_s_vlan=false`` keeps the path stable, so what is
        asserted is that INT survives the rebuild: still consistent, still
        matching expected_flows, still traceable.
        """
        evc_id = self.create_int_evc_via_api(720)
        self.wait_evc_paths(evc_id, current=self.PRIMARY)
        self.assert_evc_flow_counts(self.switches, evc_id, self.DYNAMIC_EVPL_FLOWS)
        prior = self.get_evc_telemetry(evc_id)

        response = self.mef_redeploy_evc(evc_id, try_avoid_same_s_vlan=False)
        assert response.status_code == 202, response.text
        self.wait_evc_active(evc_id)

        self.assert_telemetry_rewritten(evc_id, prior)
        self.assert_int_consistent(evc_id)
        self.assert_evc_flow_counts(self.switches, evc_id, self.DYNAMIC_EVPL_FLOWS)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 720, [self.DPID_S1, self.DPID_S6]
        )
        self.xfail_lost_writes()

    def test_125_uni_down_sets_uni_down_reason(self):
        """A UNI going down is recorded in metadata but does not remove flows.

        ``mef_eline.uni_active_updated`` is metadata-only in telemetry_int, so
        the INT flows must stay installed. If mef_eline ever starts undeploying
        the EVC on a UNI-down instead, the flow assertion below is what catches
        it -- the reason would become ``undeployed`` and the flows would go.
        """
        evc_id = self.create_int_evc_via_api(725)
        before = {sw.name: self.count_evc_flows(sw, evc_id) for sw in self.switches}

        # s1:1 -- h1:1 is the UNI link; a port-down is what mef_eline turns
        # into uni_active_updated (an administrative disable is a different path)
        self.net.net.configLinkStatus("s1", "h1", "down", port1=1, port2=1)
        self.wait_interface_active(self.UNI_A1, active=False)

        self.assert_telemetry_metadata(
            evc_id, True, status="DOWN", status_reason=["uni_down"]
        )
        self.assert_evc_flow_counts(self.switches, evc_id, before)

        self.net.net.configLinkStatus("s1", "h1", "up", port1=1, port2=1)
        self.wait_interface_active(self.UNI_A1, active=True)

        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.assert_int_consistent(evc_id)

    def test_130_evc_uni_changed_via_mef_eline(self):
        """Moving a UNI to another port moves the INT source flows with it.

        The INT source flows are derived from the mef_eline flows whose
        ``in_port`` is the UNI port, so a UNI change has to rebuild them on the
        new port and leave nothing behind on the old one.
        """
        evc_id = self.create_int_evc_via_api(730)
        self.assert_int_consistent(evc_id)
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 730, [self.DPID_S1, self.DPID_S6]
        )
        prior = self.get_evc_telemetry(evc_id)

        response = self.mef_patch_evc(
            evc_id,
            {
                "uni_a": {
                    "interface_id": self.UNI_A2,
                    "tag": {"tag_type": "vlan", "value": 730},
                }
            },
        )
        assert response.status_code == 200, response.text
        self.wait_evc_active(evc_id)

        # the PATCH redeploys asynchronously, so poll for the INT source flows
        # to have moved before asserting anything about them
        deadline = time.time() + 60
        while True:
            int_ports = {
                flow["flow"]["match"].get("in_port")
                for flow in self.get_stored_flows(self.int_cookie_prefix, evc_id).get(
                    self.DPID_S1, []
                )
            }
            if 2 in int_ports and 1 not in int_ports:
                break
            assert (
                time.time() < deadline
            ), f"INT flows never moved from s1:1 to s1:2: in_ports={int_ports}"
            time.sleep(2)

        self.assert_int_consistent(evc_id)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_trace_matches_control(
            self.DPID_S1, 2, 730, [self.DPID_S1, self.DPID_S6]
        )

        self.assert_telemetry_rewritten(evc_id, prior)
        self.xfail_lost_writes()

    def test_135_evc_vlan_changed_via_mef_eline(self):
        """Changing a UNI's VLAN moves the INT flows onto the new VLAN."""
        evc_id = self.create_int_evc_via_api(735)
        self.assert_int_consistent(evc_id)
        prior = self.get_evc_telemetry(evc_id)

        response = self.mef_patch_evc(
            evc_id,
            {
                "uni_z": {
                    "interface_id": self.UNI_Z1,
                    "tag": {"tag_type": "vlan", "value": 736},
                }
            },
        )
        assert response.status_code == 200, response.text
        self.wait_evc_active(evc_id)

        deadline = time.time() + 60
        while True:
            int_vlans = {
                flow["flow"]["match"].get("dl_vlan")
                for flow in self.get_stored_flows(self.int_cookie_prefix, evc_id).get(
                    self.DPID_S6, []
                )
                if flow["flow"]["match"].get("in_port") == 1
            }
            if int_vlans == {736}:
                break
            assert (
                time.time() < deadline
            ), f"INT flows never moved to vlan 736: {int_vlans}"
            time.sleep(2)

        self.assert_int_consistent(evc_id)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        _, int_result = self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 735, [self.DPID_S1, self.DPID_S6]
        )
        assert int_result[-1]["out"]["vlan"] == 736, int_result

        self.assert_telemetry_rewritten(evc_id, prior)
        self.xfail_lost_writes()

    def test_140_evc_path_changed_via_mef_eline(self):
        """Repointing a static EVC's primary_path moves the INT flows with it.

        This is a path change with no link failure involved, so it exercises
        the plain ``deployed`` redeploy rather than any failover handling.
        """
        evc_id = self.create_int_evc_via_api(
            740,
            dynamic_backup_path=False,
            primary_path=self.path_payload(self.PRIMARY),
        )
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=[])
        self.assert_evc_flow_counts(self.switches, evc_id, self.STATIC_PRIMARY_FLOWS)
        prior = self.get_evc_telemetry(evc_id)

        response = self.mef_patch_evc(
            evc_id, {"primary_path": self.path_payload(self.BACKUP)}
        )
        assert response.status_code == 200, response.text
        self.wait_evc_paths(evc_id, current=self.BACKUP, failover=[])

        self.assert_evc_flow_counts(
            self.switches,
            evc_id,
            {**self.STATIC_PRIMARY_FLOWS, "s5": (2, 4)},
        )
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(
            evc_id, [self.DPID_S1, self.DPID_S6], hop_dpids=[self.DPID_S5]
        )
        self.assert_int_consistent(evc_id)
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 740, [self.DPID_S1, self.DPID_S5, self.DPID_S6]
        )

        self.assert_telemetry_rewritten(evc_id, prior)
        self.xfail_lost_writes()

    def test_145_evcs_loaded_after_controller_restart(self):
        """INT survives a controller restart that keeps the database.

        Restarts *without* ``clean_config`` -- ``restart_kytos_clean()`` would
        drop the database and take the EVC with it -- but with ``del_flows``,
        so every switch comes back empty. Matching flow counts afterwards
        therefore prove the flows were reinstalled from flow_manager's stored
        flows rather than left over from before the restart.

        A failover after the restart then proves the new telemetry_int process
        handles events for an EVC it only knows from the database: it has to
        promote the failover INT subset and add the s2 hop flows.

        It does **not** exercise ``mef_eline.evcs_loaded``: telemetry_int's
        handler only calls ``load_uni_src_proxy_ports``, and a no-proxy-port
        EVC leaves ``srcs_pp`` / ``unis_src`` empty either way. Covering that
        needs proxy ports, which this backend does not have.
        """
        evc_id = self.create_int_evc_via_api(745)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)
        self.assert_evc_flow_counts(self.switches, evc_id, self.DYNAMIC_EVPL_FLOWS)
        self.assert_int_consistent(evc_id)

        self.net.start_controller(enable_all=True, del_flows=True)
        self.net.wait_switches_connect()
        self.wait_nni_links_up(self.net)

        # mef_eline reloads its EVCs after start-up; everything below waits on
        # that through the polling helpers before any bare lookup
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)
        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        assert evc_id in self.mef_list_evcs(archived=False)
        assert evc_id in self.int_list_evcs()
        self.assert_evc_flow_counts(
            self.switches, evc_id, self.DYNAMIC_EVPL_FLOWS, timeout=120
        )
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_consistent(evc_id)
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 745, [self.DPID_S1, self.DPID_S6]
        )

        self.net.net.configLinkStatus("s1", "s6", "down")
        self.net.wait_kytos_links("s1", "s6", status="DOWN")
        self.wait_evc_paths(evc_id, current=self.BACKUP)
        self.assert_evc_flow_counts(
            self.switches,
            evc_id,
            {**self.DYNAMIC_EVPL_FLOWS, "s2": (2, 4)},
        )
        self.assert_int_flows_match_expected(evc_id, self.switches)
        # s5 is now on current_path and s2 on the new failover_path
        self.assert_int_flow_actions(
            evc_id,
            [self.DPID_S1, self.DPID_S6],
            hop_dpids=[self.DPID_S5, self.DPID_S2],
        )
        self.assert_int_consistent(evc_id)
        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 745, [self.DPID_S1, self.DPID_S5, self.DPID_S6]
        )
