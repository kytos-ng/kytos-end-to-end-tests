"""End-to-end tests for telemetry_int Napp validating INT flows stored on flow_manager.

These tests don't rely on data plane traffic, they validate that the flows
that telemetry_int installs (as seen by flow_manager stored_flows) are the ones
expected for multiple EVCs enabled at once: inter-switch EVCs, intra-switch EVCs
and a mix of both. Proxy ports are configured on all UNIs.
"""

import json
import os
import re
import time

import pytest
import requests

from .helpers import NetworkTest

CONTROLLER = "127.0.0.1"
KYTOS_API = f"http://{CONTROLLER}:8181/api"

MEF_COOKIE_PREFIX = "aa"
INT_COOKIE_PREFIX = "a8"

TABLE_EVPL = 2
PRIORITY_TABLE_0 = 20100
PRIORITY_TABLE_X = 20000
IPV4 = 2048
TCP = 6
UDP = 17

S1 = "00:00:00:00:00:00:00:01"
S6 = "00:00:00:00:00:00:00:06"

# UNI port -> proxy port (source, destination) on the same switch. The proxy
# ports are the loops of the amlight_intlab topology: 13<->14 and 15<->16
PROXY_PORTS = {
    S1: {1: (13, 14), 2: (15, 16)},
    S6: {1: (13, 14), 2: (15, 16)},
}
INTER_LINK_PORT = 7  # s1:7 -- s6:7

# actions that aren't relevant for the validations here
IGNORED_ACTIONS = ("set_vlan", "push_vlan", "pop_vlan", "set_queue")


def summarize_flow(flow):
    """Summarize a flow_manager flow into a comparable, hashable tuple.

    (table_id, priority, match, actions, goto_table)
    vlan related actions and set_queue are ignored, since they depend on
    dynamically allocated tags.
    """
    flow = flow["flow"]
    actions, goto_table = [], None
    for instruction in flow.get("instructions", []):
        if instruction["instruction_type"] == "apply_actions":
            for action in instruction["actions"]:
                if action["action_type"] in IGNORED_ACTIONS:
                    continue
                actions.append((action["action_type"], action.get("port")))
        elif instruction["instruction_type"] == "goto_table":
            goto_table = instruction["table_id"]
    return (
        flow["table_id"],
        flow["priority"],
        json.dumps(flow["match"], sort_keys=True),
        tuple(actions),
        goto_table,
    )


def expected_flow(table_id, match, actions, goto_table=None):
    """Build the expected summary of a flow, see summarize_flow."""
    priority = PRIORITY_TABLE_0 if table_id == 0 else PRIORITY_TABLE_X
    return (
        table_id,
        priority,
        json.dumps(match, sort_keys=True),
        tuple(actions),
        goto_table,
    )


def expected_source_flows(in_port, vlan, out_port):
    """Expected INT source flows: tcp and udp on table 0, and table X."""
    flows = [
        expected_flow(
            0,
            {"in_port": in_port, "dl_vlan": vlan, "dl_type": IPV4, "nw_proto": proto},
            [("push_int", None)],
            TABLE_EVPL,
        )
        for proto in (TCP, UDP)
    ]
    flows.append(
        expected_flow(
            TABLE_EVPL,
            {"in_port": in_port, "dl_vlan": vlan},
            [("add_int_metadata", None), ("output", out_port)],
        )
    )
    return flows


def expected_pos_proxy_sink_flows(proxy_dst_port, vlan, uni_port):
    """Expected INT sink flows after the proxy port: send_report, pop_int, output."""
    match = {"in_port": proxy_dst_port, "dl_vlan": vlan}
    return [
        expected_flow(0, match, [("send_report", None)], TABLE_EVPL),
        expected_flow(
            TABLE_EVPL, match, [("pop_int", None), ("output", uni_port)]
        ),
    ]


def expected_pre_proxy_sink_flows(nni_port, s_vlan, proxy_src_port):
    """Expected INT sink flows before the proxy port (inter EVCs only): tcp
    and udp on table 0 coming from the NNI, add_int_metadata and output to
    the proxy port."""
    return [
        expected_flow(
            0,
            {
                "in_port": nni_port,
                "dl_vlan": s_vlan,
                "dl_type": IPV4,
                "nw_proto": proto,
            },
            [("add_int_metadata", None), ("output", proxy_src_port)],
        )
        for proto in (TCP, UDP)
    ]


@pytest.mark.skipif(
    os.environ.get("SWITCH_CLASS") not in ("NoviSwitch", "P4OfSwitch")
    or (
        os.environ.get("SWITCH_CLASS") == "NoviSwitch"
        and os.environ.get("NOVIVERSION") != "NW570.6.1"
    ),
    reason="NoviSwitch does not support interface removal",
)
class TestE2ETelemetryINTFlows:
    """End-to-end tests for telemetry_int validating flow_manager flows."""

    net = None

    @classmethod
    def setup_class(cls):
        """Called once before all test methods within a class are run."""
        cls.net = NetworkTest(CONTROLLER, topo_name="amlight_intlab")
        cls.net.start(start_controller=False)

    @classmethod
    def teardown_class(cls):
        """Called once after all tests in the class have finished for cleanup."""
        cls.net.stop()

    def setup_method(self, method):  # pylint: disable=unused-argument
        """Restart Kytos clean and configure the proxy ports on s1 and s6."""
        self.net.config_all_links_up()
        self.net.restart_kytos_clean()
        time.sleep(5)
        self.config_proxy_ports()

    def config_proxy_ports(self):
        """Ignore the proxy loops on of_lldp, set the UNIs proxy_port metadata
        and wait until the proxy ports are detected as looped and enabled."""
        for dpid, unis in PROXY_PORTS.items():
            loops = [list(pp) for pp in unis.values()]
            response = requests.post(
                f"{KYTOS_API}/kytos/topology/v3/switches/{dpid}/metadata",
                json={"ignored_loops": loops},
                timeout=5,
            )
            assert response.status_code == 201, response.text
            for pp_src, pp_dst in loops:
                for port in (pp_src, pp_dst):
                    response = requests.post(
                        f"{KYTOS_API}/kytos/topology/v3/interfaces/{dpid}:{port}/enable",
                        timeout=5,
                    )
                    assert response.status_code == 200, response.text
            for uni_port, (pp_src, _) in unis.items():
                response = requests.post(
                    f"{KYTOS_API}/kytos/topology/v3/interfaces/{dpid}:{uni_port}/metadata",
                    json={"proxy_port": pp_src},
                    timeout=5,
                )
                assert response.status_code == 201, response.text

        # the loops are ignored by of_lldp, so the "looped" metadata that
        # telemetry_int needs to find the proxy port destination isn't set by it
        # (unless the loop was detected before being ignored), set it here
        response = requests.get(f"{KYTOS_API}/kytos/topology/v3/interfaces", timeout=5)
        interfaces = response.json()["interfaces"]
        expected = []
        for dpid, unis in PROXY_PORTS.items():
            for pp_src, pp_dst in unis.values():
                intf_id = f"{dpid}:{pp_src}"
                expected.append(intf_id)
                if "looped" in interfaces[intf_id]["metadata"]:
                    continue
                response = requests.post(
                    f"{KYTOS_API}/kytos/topology/v3/interfaces/{intf_id}/metadata",
                    json={
                        "looped": {
                            "port_numbers": [pp_src, pp_dst],
                            "detected_at": "2026-01-01T00:00:00",
                        }
                    },
                    timeout=5,
                )
                assert response.status_code == 201, response.text

        data = {}
        for _ in range(30):
            response = requests.get(
                f"{KYTOS_API}/kytos/topology/v3/interfaces", timeout=5
            )
            data = response.json()["interfaces"]
            if all(
                "looped" in data[intf_id]["metadata"] and data[intf_id]["active"]
                for intf_id in expected
            ):
                return
            time.sleep(2)
        pytest.fail(f"Proxy ports weren't looped and active: {expected}")

    def create_evc(self, vlan_id, uni_a, uni_z, **kwargs):
        """Create an EVC, return its ID."""
        payload = {
            "name": f"Vlan_{vlan_id}",
            "dynamic_backup_path": False,
            "uni_a": {"interface_id": uni_a, "tag": {"tag_type": "vlan", "value": vlan_id}},
            "uni_z": {"interface_id": uni_z, "tag": {"tag_type": "vlan", "value": vlan_id}},
        }
        payload.update(kwargs)
        response = requests.post(
            f"{KYTOS_API}/kytos/mef_eline/v2/evc/", json=payload, timeout=5
        )
        assert response.status_code == 201, response.text
        return response.json()["circuit_id"]

    def create_inter_evc(self, vlan_id):
        """Create an inter-switch EVC s1:1 -- s6:1 over the direct s1:7 -- s6:7 link."""
        return self.create_evc(
            vlan_id,
            f"{S1}:1",
            f"{S6}:1",
            primary_path=[
                {
                    "endpoint_a": {"id": f"{S1}:{INTER_LINK_PORT}"},
                    "endpoint_b": {"id": f"{S6}:{INTER_LINK_PORT}"},
                }
            ],
        )

    def create_intra_evc(self, vlan_id, dpid):
        """Create an intra-switch EVC dpid:1 -- dpid:2."""
        return self.create_evc(vlan_id, f"{dpid}:1", f"{dpid}:2")

    def get_stored_flows(self, evc_id, prefix):
        """Get installed flows of a given EVC and cookie prefix.

        Return a dict mapping dpid to a list of flows."""
        cookie = int(f"0x{prefix}{evc_id}", 16)
        response = requests.get(
            f"{KYTOS_API}/kytos/flow_manager/v2/stored_flows/",
            params=[
                ("cookie_range", cookie),
                ("cookie_range", cookie),
                ("state", "installed"),
            ],
            timeout=10,
        )
        assert response.status_code == 200, response.text
        return response.json()

    def get_int_flows(self, evc_id):
        """Get INT flows summaries per switch as sorted lists."""
        flows = self.get_stored_flows(evc_id, INT_COOKIE_PREFIX)
        return {dpid: sorted(map(summarize_flow, fl)) for dpid, fl in flows.items() if fl}

    def get_s_vlan(self, evc_id, dpid, nni_port):
        """Get the s-vlan of an inter EVC on a NNI, from the mef_eline flows."""
        for flow in self.get_stored_flows(evc_id, MEF_COOKIE_PREFIX).get(dpid, []):
            match = flow["flow"]["match"]
            if match.get("in_port") == nni_port and "dl_vlan" in match:
                return match["dl_vlan"]
        pytest.fail(f"s-vlan of EVC {evc_id} on {dpid}:{nni_port} not found")

    def expected_inter_int_flows(self, evc_id, vlan):
        """Expected INT flows of an inter EVC s1:1 -- s6:1 without hops."""
        s_vlan = self.get_s_vlan(evc_id, S1, INTER_LINK_PORT)
        expected = {}
        for dpid in (S1, S6):
            pp_src, pp_dst = PROXY_PORTS[dpid][1]
            expected[dpid] = (
                # INT source, from the UNI to the NNI
                expected_source_flows(1, vlan, INTER_LINK_PORT)
                # INT sink, from the NNI to the proxy port and then the UNI
                + expected_pre_proxy_sink_flows(INTER_LINK_PORT, s_vlan, pp_src)
                + expected_pos_proxy_sink_flows(pp_dst, vlan, 1)
            )
        return expected

    def expected_intra_int_flows(self, dpid, vlan):
        """Expected INT flows of an intra EVC dpid:1 -- dpid:2."""
        (pp1_src, pp1_dst), (pp2_src, pp2_dst) = (
            PROXY_PORTS[dpid][1],
            PROXY_PORTS[dpid][2],
        )
        return {
            dpid: (
                # source of each UNI outputs to the proxy port of the other UNI
                expected_source_flows(1, vlan, pp2_src)
                + expected_source_flows(2, vlan, pp1_src)
                # sink of each UNI after its proxy port
                + expected_pos_proxy_sink_flows(pp1_dst, vlan, 1)
                + expected_pos_proxy_sink_flows(pp2_dst, vlan, 2)
            )
        }

    def enable_int(self, evc_ids):
        """Enable INT on the EVC ids with a single request."""
        response = requests.post(
            f"{KYTOS_API}/kytos/telemetry_int/v1/evc/enable",
            json={"evc_ids": evc_ids},
            timeout=10,
        )
        assert response.status_code == 201, response.text

    def assert_int_flows(self, evc_id, expected, timeout=30):
        """Assert the installed INT flows of an EVC are the expected ones.

        expected: dict mapping dpid to a list of expected flows summaries.
        Poll since flows are installed asynchronously."""
        expected = {dpid: sorted(flows) for dpid, flows in expected.items()}
        actual = {}
        for _ in range(timeout):
            actual = self.get_int_flows(evc_id)
            if actual == expected:
                return
            time.sleep(1)
        for dpid in sorted(set(expected) | set(actual)):
            exp, act = expected.get(dpid, []), actual.get(dpid, [])
            assert act == exp, (
                f"EVC {evc_id} switch {dpid}\n"
                f"missing: {[f for f in exp if f not in act]}\n"
                f"unexpected: {[f for f in act if f not in exp]}"
            )

    def test_001_enable_multiple_inter_evcs(self):
        """Test enabling INT on multiple inter-switch EVCs in one request."""
        vlans = [301, 302, 303]
        evc_ids = [self.create_inter_evc(vlan) for vlan in vlans]
        time.sleep(10)

        for evc_id in evc_ids:
            assert self.get_int_flows(evc_id) == {}

        self.enable_int(evc_ids)

        for evc_id, vlan in zip(evc_ids, vlans):
            self.assert_int_flows(evc_id, self.expected_inter_int_flows(evc_id, vlan))

    def test_002_enable_multiple_intra_evcs(self):
        """Test enabling INT on multiple intra-switch EVCs in one request."""
        evcs = [
            (self.create_intra_evc(311, S1), S1, 311),
            (self.create_intra_evc(312, S1), S1, 312),
            (self.create_intra_evc(313, S6), S6, 313),
            (self.create_intra_evc(314, S6), S6, 314),
        ]
        time.sleep(10)

        for evc_id, _, _ in evcs:
            assert self.get_int_flows(evc_id) == {}

        self.enable_int([evc_id for evc_id, _, _ in evcs])

        for evc_id, dpid, vlan in evcs:
            self.assert_int_flows(evc_id, self.expected_intra_int_flows(dpid, vlan))

    def test_003_enable_multiple_inter_and_intra_evcs(self):
        """Test enabling INT on inter and intra EVCs mixed in one request."""
        inter = [(self.create_inter_evc(vlan), vlan) for vlan in (321, 322)]
        intra = [
            (self.create_intra_evc(323, S1), S1, 323),
            (self.create_intra_evc(324, S6), S6, 324),
        ]
        time.sleep(10)

        self.enable_int([evc_id for evc_id, _ in inter] + [e[0] for e in intra])

        for evc_id, vlan in inter:
            self.assert_int_flows(evc_id, self.expected_inter_int_flows(evc_id, vlan))
        for evc_id, dpid, vlan in intra:
            self.assert_int_flows(evc_id, self.expected_intra_int_flows(dpid, vlan))

    def disable_int(self, evc_ids, expected_status=200, **kwargs):
        """Disable INT on the EVC ids with a single request, return the response."""
        response = requests.post(
            f"{KYTOS_API}/kytos/telemetry_int/v1/evc/disable",
            json={"evc_ids": evc_ids, **kwargs},
            timeout=10,
        )
        assert response.status_code == expected_status, response.text
        return response

    def list_int_evcs(self):
        """GET /v1/evc, return the EVCs with INT enabled."""
        response = requests.get(f"{KYTOS_API}/kytos/telemetry_int/v1/evc", timeout=10)
        assert response.status_code == 200, response.text
        return response.json()

    def get_telemetry_metadata(self, evc_id):
        """Get the telemetry metadata of an EVC from mef_eline."""
        response = requests.get(
            f"{KYTOS_API}/kytos/mef_eline/v2/evc/{evc_id}", timeout=5
        )
        assert response.status_code == 200, response.text
        return response.json()["metadata"].get("telemetry")

    def count_mef_eline_flows(self, evc_id):
        """Count the installed mef_eline flows of an EVC."""
        flows = self.get_stored_flows(evc_id, MEF_COOKIE_PREFIX)
        return sum(len(fl) for fl in flows.values())

    def create_evcs(self):
        """Create 2 inter and 2 intra EVCs. Return a list of (evc_id, kind, dpid, vlan)
        where kind is 'inter' or 'intra'."""
        evcs = [
            (self.create_inter_evc(331), "inter", None, 331),
            (self.create_inter_evc(332), "inter", None, 332),
            (self.create_intra_evc(333, S1), "intra", S1, 333),
            (self.create_intra_evc(334, S6), "intra", S6, 334),
        ]
        time.sleep(10)
        return evcs

    def expected_int_flows(self, evc):
        """Expected INT flows of an EVC created by create_evcs."""
        evc_id, kind, dpid, vlan = evc
        if kind == "inter":
            return self.expected_inter_int_flows(evc_id, vlan)
        return self.expected_intra_int_flows(dpid, vlan)

    def assert_int_enabled_evcs(self, evcs):
        """Assert INT flows are installed and metadata is UP for the EVCs."""
        for evc in evcs:
            self.assert_int_flows(evc[0], self.expected_int_flows(evc))
            telemetry = self.get_telemetry_metadata(evc[0])
            assert telemetry["enabled"] is True, telemetry
            assert telemetry["status"] == "UP", telemetry

    def assert_int_disabled_evcs(self, evcs):
        """Assert INT flows were removed, mef_eline flows were kept and
        telemetry metadata is DOWN/disabled for the EVCs."""
        for evc in evcs:
            self.assert_int_flows(evc[0], {})
            telemetry = self.get_telemetry_metadata(evc[0])
            assert telemetry["enabled"] is False, telemetry
            assert telemetry["status"] == "DOWN", telemetry
            assert telemetry["status_reason"] == ["disabled"], telemetry
            assert self.count_mef_eline_flows(evc[0]) > 0

    #####################################################
    ## GET /v1/evc
    #####################################################

    def test_010_list_evcs_none_enabled(self):
        """Test GET v1/evc when there aren't EVCs with INT."""
        assert self.list_int_evcs() == {}

        evcs = self.create_evcs()
        time.sleep(2)
        # EVCs exist on mef_eline but none has INT enabled
        assert self.list_int_evcs() == {}
        assert all(self.get_telemetry_metadata(evc[0]) is None for evc in evcs)

    def test_011_list_evcs_enabled(self):
        """Test GET v1/evc lists only the EVCs with INT enabled and follows
        enable and disable operations."""
        evcs = self.create_evcs()
        ids = [evc[0] for evc in evcs]

        # one EVC
        self.enable_int([ids[0]])
        self.assert_int_enabled_evcs(evcs[:1])
        data = self.list_int_evcs()
        assert list(data) == [ids[0]], data
        telemetry = data[ids[0]]["metadata"]["telemetry"]
        assert telemetry["enabled"] is True, telemetry
        assert telemetry["status"] == "UP", telemetry
        assert telemetry["status_reason"] == [], telemetry

        # multiple EVCs, inter and intra
        self.enable_int(ids[1:3])
        self.assert_int_enabled_evcs(evcs[1:3])
        data = self.list_int_evcs()
        assert sorted(data) == sorted(ids[:3]), data
        assert ids[3] not in data
        for evc_id in ids[:3]:
            assert data[evc_id]["id"] == evc_id
            assert data[evc_id]["metadata"]["telemetry"]["enabled"] is True

        # disabled EVC isn't listed anymore
        self.disable_int([ids[1]])
        self.assert_int_disabled_evcs(evcs[1:2])
        data = self.list_int_evcs()
        assert sorted(data) == sorted([ids[0], ids[2]]), data

        # EVC deleted isn't listed
        response = requests.delete(
            f"{KYTOS_API}/kytos/mef_eline/v2/evc/{ids[0]}", timeout=5
        )
        assert response.status_code == 200, response.text
        time.sleep(5)
        assert list(self.list_int_evcs()) == [ids[2]]

    #####################################################
    ## POST /v1/evc/disable
    #####################################################

    def test_020_disable_one_evc(self):
        """Test disabling INT with a payload with just one evc_id."""
        evcs = self.create_evcs()
        self.enable_int([evc[0] for evc in evcs])
        self.assert_int_enabled_evcs(evcs)

        response = self.disable_int([evcs[0][0]])
        assert response.json() == [evcs[0][0]], response.text

        self.assert_int_disabled_evcs(evcs[:1])
        # the other EVCs aren't affected
        self.assert_int_enabled_evcs(evcs[1:])
        assert sorted(self.list_int_evcs()) == sorted(evc[0] for evc in evcs[1:])

    def test_021_disable_multiple_evcs(self):
        """Test disabling INT with a payload with multiple evc_ids."""
        evcs = self.create_evcs()
        self.enable_int([evc[0] for evc in evcs])
        self.assert_int_enabled_evcs(evcs)

        # an inter and an intra EVC
        to_disable = [evcs[1], evcs[2]]
        response = self.disable_int([evc[0] for evc in to_disable])
        assert sorted(response.json()) == sorted(evc[0] for evc in to_disable)

        self.assert_int_disabled_evcs(to_disable)
        self.assert_int_enabled_evcs([evcs[0], evcs[3]])
        assert sorted(self.list_int_evcs()) == sorted([evcs[0][0], evcs[3][0]])

    def test_022_disable_all_evcs_empty_payload(self):
        """Test disabling INT with an empty evc_ids, which disables all INT EVCs."""
        evcs = self.create_evcs()
        self.enable_int([evc[0] for evc in evcs[:3]])
        self.assert_int_enabled_evcs(evcs[:3])

        response = self.disable_int([])
        assert sorted(response.json()) == sorted(evc[0] for evc in evcs[:3])

        self.assert_int_disabled_evcs(evcs[:3])
        assert self.list_int_evcs() == {}
        # the EVC that never had INT remains untouched
        assert self.get_telemetry_metadata(evcs[3][0]) is None
        assert self.get_int_flows(evcs[3][0]) == {}

        # nothing else to disable
        response = self.disable_int([])
        assert response.json() == [], response.text

    def test_023_disable_evc_without_int(self):
        """Test disabling INT on an EVC without INT is a conflict, and that
        a request with valid and invalid EVCs doesn't disable any EVC."""
        evcs = self.create_evcs()
        self.enable_int([evcs[0][0]])
        self.assert_int_enabled_evcs(evcs[:1])

        # EVC that never had INT
        response = self.disable_int([evcs[1][0]], expected_status=409)
        assert evcs[1][0] in response.text, response.text

        # one EVC with INT, other without
        self.disable_int([evcs[0][0], evcs[1][0]], expected_status=409)
        self.assert_int_enabled_evcs(evcs[:1])

        # EVC with INT already disabled
        self.disable_int([evcs[0][0]])
        self.assert_int_disabled_evcs(evcs[:1])
        self.disable_int([evcs[0][0]], expected_status=409)

    def test_024_disable_evc_not_found(self):
        """Test disabling INT on an EVC that doesn't exist."""
        evcs = self.create_evcs()
        self.enable_int([evcs[0][0]])
        self.assert_int_enabled_evcs(evcs[:1])

        # single and multiple evc_ids
        self.disable_int(["aaaaaaaaaaaaaa"], expected_status=404)
        self.disable_int([evcs[0][0], "aaaaaaaaaaaaaa"], expected_status=404)
        self.assert_int_enabled_evcs(evcs[:1])

        # force bypasses the EVC not found validation
        self.disable_int(["aaaaaaaaaaaaaa", evcs[0][0]], force=True)
        self.assert_int_disabled_evcs(evcs[:1])

    def test_025_disable_invalid_payload(self):
        """Test disabling INT with an invalid payload."""
        for payload in ({}, {"evc_ids": "abc"}, {"evc_ids": [1]}):
            response = requests.post(
                f"{KYTOS_API}/kytos/telemetry_int/v1/evc/disable",
                json=payload,
                timeout=5,
            )
            assert response.status_code == 400, response.text

    def test_026_reenable_after_disable(self):
        """Test INT can be enabled again after disabling it."""
        evcs = self.create_evcs()
        ids = [evc[0] for evc in evcs]
        self.enable_int(ids)
        self.assert_int_enabled_evcs(evcs)
        self.disable_int(ids)
        self.assert_int_disabled_evcs(evcs)
        assert self.list_int_evcs() == {}

        self.enable_int(ids)
        self.assert_int_enabled_evcs(evcs)
        assert sorted(self.list_int_evcs()) == sorted(ids)

    #####################################################
    ## PATCH /v1/evc/redeploy
    #####################################################

    def redeploy_int(self, evc_ids, expected_status=201):
        """Redeploy INT on the EVC ids with a single request, return the response."""
        response = requests.patch(
            f"{KYTOS_API}/kytos/telemetry_int/v1/evc/redeploy",
            json={"evc_ids": evc_ids},
            timeout=15,
        )
        assert response.status_code == expected_status, response.text
        return response

    def switch_int_flows(self, dpid, evc_id):
        """Dump the INT flows of an EVC from the switch, with their actions.

        Return a dict mapping the flow (table, priority and match, without
        counters or duration) to its actions string."""
        switch = self.net.net.get(f"s{int(dpid[-2:], 16)}")
        flows = {}
        for line in switch.dpctl("dump-flows").splitlines():
            if f"cookie=0x{INT_COOKIE_PREFIX}{evc_id}" not in line:
                continue
            table = re.search(r"table=(\d+)", line).group(1)
            match, actions = line.split("priority=", 1)[1].split(" actions=", 1)
            flows[(table, match.strip())] = actions.strip()
        return flows

    def switches_int_flows(self, evc_ids):
        """Dump the INT flows of EVCs from all switches that have them."""
        return {
            (dpid, evc_id): flows
            for evc_id in evc_ids
            for dpid in (f"00:00:00:00:00:00:00:0{i}" for i in range(1, 7))
            if (flows := self.switch_int_flows(dpid, evc_id))
        }

    def wait_switches_int_flows(self, evc_ids, condition, timeout=30):
        """Wait until condition(snapshot) is true, return the last snapshot."""
        snapshot = {}
        for _ in range(timeout):
            snapshot = self.switches_int_flows(evc_ids)
            if condition(snapshot):
                break
            time.sleep(1)
        return snapshot

    def find_stored_int_flow(self, evc_id, dpid, table_id, in_port):
        """Find a stored INT flow on flow_manager."""
        for flow in self.get_stored_flows(evc_id, INT_COOKIE_PREFIX)[dpid]:
            match = flow["flow"]["match"]
            if flow["flow"]["table_id"] == table_id and match["in_port"] == in_port:
                return flow["flow"]
        pytest.fail(f"INT flow not found {evc_id} {dpid} table {table_id} {in_port}")

    def tamper_flow_actions(self, evc_id, dpid, table_id, in_port, wrong_port):
        """Overwrite a INT flow via flow_manager with a wrong output action."""
        flow = self.find_stored_int_flow(evc_id, dpid, table_id, in_port)
        flow = {
            k: v
            for k, v in flow.items()
            if k in ("owner", "cookie", "match", "table_id", "table_group", "priority")
        }
        flow["instructions"] = [
            {
                "instruction_type": "apply_actions",
                "actions": [{"action_type": "output", "port": wrong_port}],
            }
        ]
        response = requests.post(
            f"{KYTOS_API}/kytos/flow_manager/v2/flows/{dpid}",
            json={"flows": [flow]},
            timeout=10,
        )
        assert response.status_code == 202, response.text

    def delete_flow(self, evc_id, dpid, table_id, in_port):
        """Delete a INT flow via flow_manager."""
        flow = self.find_stored_int_flow(evc_id, dpid, table_id, in_port)
        response = requests.post(
            f"{KYTOS_API}/kytos/flow_manager/v2/delete/{dpid}",
            json={
                "flows": [
                    {
                        "cookie": flow["cookie"],
                        "cookie_mask": 0xFFFFFFFFFFFFFFFF,
                        "table_id": flow["table_id"],
                        "match": flow["match"],
                    }
                ]
            },
            timeout=10,
        )
        assert response.status_code == 202, response.text

    def tamper_and_redeploy(self, evcs, redeploy_ids):
        """Tamper each EVC INT flows, check on the switches, redeploy and
        check the flows were fixed.

        evcs: list of (evc_id, tampers) where tampers is a list of
        (dpid, table_id, in_port, wrong_port); wrong_port None deletes the flow.
        """
        evc_ids = [evc_id for evc_id, _ in evcs]
        original = self.switches_int_flows(evc_ids)
        assert original, "INT flows not found on the switches"

        # tamper flows through flow_manager
        for evc_id, tampers in evcs:
            for dpid, table_id, in_port, port in tampers:
                if port is None:
                    self.delete_flow(evc_id, dpid, table_id, in_port)
                else:
                    self.tamper_flow_actions(evc_id, dpid, table_id, in_port, port)

        # the flows on the switches are actually wrong
        def tamper_problems(snapshot):
            problems = []
            for evc_id, tampers in evcs:
                for dpid, table_id, in_port, port in tampers:
                    key = (dpid, evc_id)
                    current = snapshot.get(key, {})
                    candidates = [
                        flow_key
                        for flow_key in original[key]
                        if flow_key[0] == str(table_id)
                        and f"in_port={in_port}" in flow_key[1].split(",")
                    ]
                    assert candidates, f"flow not found {key} {table_id} {in_port}"
                    for flow_key in candidates:
                        if port is None and flow_key in current:
                            problems.append(f"not deleted: {key} {flow_key}")
                        elif port is not None and (
                            f"output:{port}" not in current.get(flow_key, "")
                            or current[flow_key] == original[key][flow_key]
                        ):
                            problems.append(f"not changed: {key} {flow_key}")
            return problems

        snapshot = self.wait_switches_int_flows(
            evc_ids, lambda s: not tamper_problems(s)
        )
        assert not tamper_problems(snapshot), f"{tamper_problems(snapshot)}\n{snapshot}"
        assert snapshot != original

        # redeploy
        response = self.redeploy_int(redeploy_ids)
        assert sorted(response.json()) == sorted(redeploy_ids), response.text

        # the actions on the switches are the same as originally
        fixed = self.wait_switches_int_flows(evc_ids, lambda s: s == original)
        assert fixed == original, f"original: {original}\nafter redeploy: {fixed}"

    def test_030_redeploy_one_inter_evc(self):
        """Test redeploy with one evc_id after tampering an inter EVC flows."""
        evc_id = self.create_inter_evc(341)
        time.sleep(10)
        self.enable_int([evc_id])
        expected = self.expected_inter_int_flows(evc_id, 341)
        self.assert_int_flows(evc_id, expected)

        pp_dst = PROXY_PORTS[S6][1][1]
        self.tamper_and_redeploy(
            [
                (
                    evc_id,
                    [
                        # source: wrong output port
                        (S1, TABLE_EVPL, 1, 3),
                        # sink after proxy: flow removed
                        (S6, TABLE_EVPL, pp_dst, None),
                    ],
                )
            ],
            [evc_id],
        )
        self.assert_int_flows(evc_id, expected)
        telemetry = self.get_telemetry_metadata(evc_id)
        assert telemetry["enabled"] is True and telemetry["status"] == "UP"

    def test_031_redeploy_multiple_evcs(self):
        """Test redeploy with multiple evc_ids after tampering inter and intra
        EVCs flows."""
        inter_id = self.create_inter_evc(342)
        intra_id = self.create_intra_evc(343, S1)
        untouched_id = self.create_intra_evc(344, S6)
        time.sleep(10)
        self.enable_int([inter_id, intra_id, untouched_id])
        expected = {
            inter_id: self.expected_inter_int_flows(inter_id, 342),
            intra_id: self.expected_intra_int_flows(S1, 343),
            untouched_id: self.expected_intra_int_flows(S6, 344),
        }
        for evc_id, flows in expected.items():
            self.assert_int_flows(evc_id, flows)

        untouched_before = self.switches_int_flows([untouched_id])
        self.tamper_and_redeploy(
            [
                (
                    inter_id,
                    [
                        (S6, TABLE_EVPL, 1, 3),
                        (S1, TABLE_EVPL, PROXY_PORTS[S1][1][1], None),
                    ],
                ),
                (
                    intra_id,
                    [
                        (S1, TABLE_EVPL, 2, 3),
                        (S1, TABLE_EVPL, PROXY_PORTS[S1][1][1], None),
                    ],
                ),
            ],
            [inter_id, intra_id],
        )
        for evc_id, flows in expected.items():
            self.assert_int_flows(evc_id, flows)
        # EVC not part of the redeploy is untouched
        assert self.switches_int_flows([untouched_id]) == untouched_before

    def test_032_redeploy_evc_without_int(self):
        """Test redeploy on an EVC without INT, or without any INT EVCs."""
        evc_id = self.create_inter_evc(345)
        time.sleep(10)

        self.redeploy_int([evc_id], expected_status=409)
        # empty evc_ids and there aren't INT EVCs
        self.redeploy_int([], expected_status=404)
        assert self.get_int_flows(evc_id) == {}

    #####################################################
    ## GET /v1/evc/compare
    #####################################################

    def compare_int_evcs(self):
        """GET /v1/evc/compare, return a dict mapping EVC id to the response item."""
        response = requests.get(
            f"{KYTOS_API}/kytos/telemetry_int/v1/evc/compare", timeout=15
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert isinstance(data, list), data
        return {item["id"]: item for item in data}

    def set_telemetry_enabled_metadata(self, evc_id, enabled):
        """Overwrite the telemetry metadata of an EVC via mef_eline, without
        touching the flows."""
        response = requests.post(
            f"{KYTOS_API}/kytos/mef_eline/v2/evc/{evc_id}/metadata",
            json={
                "telemetry": {
                    "enabled": enabled,
                    "status": "UP" if enabled else "DOWN",
                    "status_reason": [],
                    "status_updated_at": "2026-01-01T00:00:00",
                }
            },
            timeout=5,
        )
        assert response.status_code == 201, response.text

    def delete_stored_int_flows(self, evc_id, flows):
        """Delete INT flows (as returned by flow_manager stored_flows) from the
        switches through flow_manager."""
        flows_per_dpid = {}
        for flow in flows:
            flows_per_dpid.setdefault(flow["switch"], []).append(
                {
                    "cookie": flow["flow"]["cookie"],
                    "cookie_mask": 0xFFFFFFFFFFFFFFFF,
                    "table_id": flow["flow"]["table_id"],
                    "match": flow["flow"]["match"],
                }
            )
        for dpid, dpid_flows in flows_per_dpid.items():
            response = requests.post(
                f"{KYTOS_API}/kytos/flow_manager/v2/delete/{dpid}",
                json={"flows": dpid_flows},
                timeout=10,
            )
            assert response.status_code == 202, response.text

    def all_stored_int_flows(self, evc_id):
        """List all stored INT flows of an EVC."""
        return [
            flow
            for flows in self.get_stored_flows(evc_id, INT_COOKIE_PREFIX).values()
            for flow in flows
        ]

    def wait_int_flows_count(self, evc_id, count, timeout=30):
        """Wait until the number of INT flows installed on the switches is count."""
        total = None
        for _ in range(timeout):
            total = sum(
                len(flows) for flows in self.switches_int_flows([evc_id]).values()
            )
            if total == count:
                return
            time.sleep(1)
        pytest.fail(f"Expected {count} INT flows on switches for {evc_id}, got {total}")

    def wait_compare(self, expected, timeout=30):
        """Wait until the compare response reflects the expected
        {evc_id: compare_reason}, return the last response."""
        result = {}
        for _ in range(timeout):
            result = self.compare_int_evcs()
            if {k: v["compare_reason"] for k, v in result.items()} == expected:
                return result
            time.sleep(1)
        assert {k: v["compare_reason"] for k, v in result.items()} == expected
        return result

    def test_040_compare_consistent(self):
        """Test GET v1/evc/compare is empty when INT EVCs and EVCs without INT
        are all consistent."""
        assert self.compare_int_evcs() == {}

        evcs = self.create_evcs()
        # none enabled, no INT flows
        assert self.compare_int_evcs() == {}

        self.enable_int([evc[0] for evc in evcs[:3]])
        self.assert_int_enabled_evcs(evcs[:3])
        assert self.compare_int_evcs() == {}

        # disabled EVCs have no flows nor enabled metadata
        self.disable_int([evcs[0][0]])
        self.assert_int_disabled_evcs(evcs[:1])
        assert self.compare_int_evcs() == {}

    def test_041_compare_wrong_metadata_has_int_flows(self):
        """Test GET v1/evc/compare reports EVCs with INT flows installed but
        without INT enabled on the metadata."""
        evcs = self.create_evcs()
        ids = [evc[0] for evc in evcs]
        self.enable_int(ids[:3])
        self.assert_int_enabled_evcs(evcs[:3])
        flows_before = self.switches_int_flows(ids)

        # an inter and an intra EVC lose the telemetry enabled metadata
        for evc_id in (ids[0], ids[2]):
            self.set_telemetry_enabled_metadata(evc_id, False)

        result = self.wait_compare(
            {
                ids[0]: ["wrong_metadata_has_int_flows"],
                ids[2]: ["wrong_metadata_has_int_flows"],
            }
        )
        assert result[ids[0]]["name"] == "Vlan_331", result
        assert result[ids[2]]["name"] == "Vlan_333", result

        # the flows are still on the switches, compare only reports
        assert self.switches_int_flows(ids) == flows_before
        for evc_id in (ids[0], ids[2]):
            self.assert_int_flows(
                evc_id, self.expected_int_flows(evcs[ids.index(evc_id)])
            )

    def test_042_compare_missing_some_int_flows(self):
        """Test GET v1/evc/compare reports INT EVCs with fewer INT flows than
        mef_eline flows."""
        evcs = self.create_evcs()
        ids = [evc[0] for evc in evcs]
        self.enable_int(ids[:3])
        self.assert_int_enabled_evcs(evcs[:3])

        # inter EVC: all INT flows removed from the switches
        inter_flows = self.all_stored_int_flows(ids[0])
        assert len(inter_flows) == 14, inter_flows
        self.delete_stored_int_flows(ids[0], inter_flows)
        self.wait_int_flows_count(ids[0], 0)

        # intra EVC (2 mef_eline flows): only 1 of the 10 INT flows is kept
        intra_flows = self.all_stored_int_flows(ids[2])
        assert len(intra_flows) == 10, intra_flows
        self.delete_stored_int_flows(ids[2], intra_flows[1:])
        self.wait_int_flows_count(ids[2], 1)
        assert self.count_mef_eline_flows(ids[2]) == 2

        result = self.wait_compare(
            {
                ids[0]: ["missing_some_int_flows"],
                ids[2]: ["missing_some_int_flows"],
            }
        )
        assert result[ids[0]]["name"] == "Vlan_331", result
        assert result[ids[2]]["name"] == "Vlan_333", result

        # the consistent INT EVC and the EVC without INT aren't reported, and
        # compare doesn't fix the flows
        assert ids[1] not in result and ids[3] not in result
        self.assert_int_flows(ids[1], self.expected_int_flows(evcs[1]))
        assert self.switches_int_flows([ids[0]]) == {}
        self.wait_int_flows_count(ids[2], 1)

    def test_043_compare_few_missing_int_flows_not_reported(self):
        """Test GET v1/evc/compare only checks the minimum expected number of
        flows (INT flows >= mef_eline flows): missing just one INT flow of an
        inter EVC isn't reported, but the flow is really missing."""
        evc_id = self.create_inter_evc(351)
        time.sleep(10)
        self.enable_int([evc_id])
        self.assert_int_flows(evc_id, self.expected_inter_int_flows(evc_id, 351))
        original = self.switches_int_flows([evc_id])

        self.delete_stored_int_flows(evc_id, self.all_stored_int_flows(evc_id)[:1])
        self.wait_int_flows_count(evc_id, 13)
        assert self.switches_int_flows([evc_id]) != original

        assert self.compare_int_evcs() == {}

    def test_044_compare_multiple_inconsistent_evcs_and_fix(self):
        """Test GET v1/evc/compare with multiple kinds of inconsistencies at
        once, that compare is read-only and the EVCs are consistent after
        fixing them with redeploy and (forced) disable."""
        evcs = self.create_evcs()
        ids = [evc[0] for evc in evcs]
        self.enable_int(ids[:3])
        self.assert_int_enabled_evcs(evcs[:3])

        # ids[0]: missing flows, ids[1]: wrong metadata, ids[2]: consistent
        self.delete_stored_int_flows(ids[0], self.all_stored_int_flows(ids[0]))
        self.wait_int_flows_count(ids[0], 0)
        self.set_telemetry_enabled_metadata(ids[1], False)

        expected = {
            ids[0]: ["missing_some_int_flows"],
            ids[1]: ["wrong_metadata_has_int_flows"],
        }
        self.wait_compare(expected)
        flows_before = self.switches_int_flows(ids)
        # calling compare again changes nothing
        self.wait_compare(expected)
        assert self.switches_int_flows(ids) == flows_before

        # redeploy fixes the missing flows
        self.redeploy_int([ids[0]])
        self.assert_int_flows(ids[0], self.expected_int_flows(evcs[0]))
        self.wait_compare({ids[1]: ["wrong_metadata_has_int_flows"]})

        # the EVC has no INT metadata, so disable requires force to remove flows
        self.disable_int([ids[1]], expected_status=409)
        self.disable_int([ids[1]], force=True)
        self.assert_int_flows(ids[1], {})
        self.wait_compare({})
