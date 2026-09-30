"""End-to-end tests for telemetry_int Napp validating INT flows stored on flow_manager.

These tests don't rely on data plane traffic, they validate that the flows
that telemetry_int installs (as seen by flow_manager stored_flows) are the ones
expected for multiple EVCs enabled at once: inter-switch EVCs, intra-switch EVCs
and a mix of both. Proxy ports are configured on all UNIs.
"""

import json
import os
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

        expected = [
            f"{dpid}:{pp_src}"
            for dpid, unis in PROXY_PORTS.items()
            for pp_src, _ in unis.values()
        ]
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
        pytest.fail(f"Proxy ports weren't detected as looped and active: {expected}")

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
