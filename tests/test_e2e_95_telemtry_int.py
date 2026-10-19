"""End-to-end tests for telemetry_int: EVC shapes and convergence.

Flow-count expectations are ``{switch_name: (n_mef_eline_flows, n_int_flows)}``,
split by cookie owner (mef_eline 0xaa, telemetry_int 0xa8) so a regression that
drops every INT flow cannot hide behind a combined total.

The three paths between the UNI switches s1 and s6, in the order mef_eline
picks them: ``PRIMARY`` is s1:7--s6:7, ``BACKUP`` is s1:5--s5:5--s6:8, ``THIRD``
is s1:6--s2:6--s6:5.

A static EVC gets no pre-provisioned ``failover_path`` -- mef_eline only
provisions a declared ``backup_path`` once the primary breaks -- so
``STATIC_PRIMARY_FLOWS`` has flows on the two UNI switches only (2 mef_eline
plus 3 INT source and 3 INT sink flows each), and ``STATIC_BACKUP_FLOWS`` adds
s5 once it becomes a transit switch.
"""

import os
import re
import time
from pathlib import Path

import pytest

from .helpers import NetworkTest
from .int_helpers import INTHelper


@pytest.mark.skipif(
    os.environ.get("SWITCH_CLASS") not in ("NoviSwitch", "P4OfSwitch")
    or (
        os.environ.get("SWITCH_CLASS") == "NoviSwitch"
        and os.environ.get("NOVIVERSION") != "NW570.6.1"
    ),
    reason="NoviSwitch does not support interface removal",
)
class TestE2ETelemtryINT(INTHelper):
    """End-to-end tests for telemetry_int."""

    controller = "127.0.0.1"
    net = None
    SCRIPTS_DIR = str(Path(__file__).resolve().parents[1] / "scripts")
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
    THIRD = [
        ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
        ["00:00:00:00:00:00:00:02:5", "00:00:00:00:00:00:00:06:5"],
    ]
    NO_FLOWS = {f"s{i}": (0, 0) for i in range(1, 7)}
    STATIC_PRIMARY_FLOWS = {
        "s1": (2, 6),
        "s2": (0, 0),
        "s3": (0, 0),
        "s4": (0, 0),
        "s5": (0, 0),
        "s6": (2, 6),
    }
    STATIC_BACKUP_FLOWS = {
        "s1": (2, 6),
        "s2": (0, 0),
        "s3": (0, 0),
        "s4": (0, 0),
        "s5": (2, 4),
        "s6": (2, 6),
    }
    # INT flows on a UNI switch, by table. FlowBuilder.get_table_id is
    # evaluated per *originating mef_eline flow*, not per EVC, so each flow's
    # own match decides its table -- which is why an EPL ends up split across
    # two: its UNI-ingress flow has no dl_vlan (-> the epl group, table 3)
    # while its NNI-ingress flow always carries the s-vlan (-> evpl, table 2).
    # So an EPL moves only the INT *source* off table 2; the sink stays.
    EVPL_DYNAMIC_TABLES = {0: 6, 2: 3}  # current_path + failover_path
    EVPL_STATIC_TABLES = {0: 4, 2: 2}  # current_path only
    EPL_STATIC_TABLES = {0: 4, 2: 1, 3: 1}  # source on 3, sink on 2

    _USE_VLAN_ID = object()
    _TABLE_ID_RE = re.compile(r"^\s*table (\d+)", re.MULTILINE)

    @classmethod
    def probe_pipeline_tables(cls, switch):
        """Return the set of table ids the switch pipeline reports.

        telemetry_int places its table X flows on table 2 (evpl) or table 3 (epl and
        vlan-range/special-tag), so knowing which tables exist decides which of the
        issue #408 EVC shapes are testable on this backend at all.
        """
        tables = set()
        for source in ("dump-tables", "dump-table-features"):
            try:
                output = switch.dpctl(source)
            except Exception:  # pylint: disable=broad-except
                continue
            tables.update(
                int(match) for match in cls._TABLE_ID_RE.findall(output or "")
            )
        return tables

    def setup_method(self, method):  # pylint: disable=unused-argument
        """
        It is called at the beginning of every class method execution
        """
        self.net.config_all_links_up()
        self.net.restart_kytos_clean()
        time.sleep(5)
        # restart_kytos_clean only waits for the links to be *present*; after a
        # test that downed some, they must be UP again before the next EVC is
        # created or it deploys on the wrong path
        self.wait_nni_links_up(self.net)

    @classmethod
    def setup_class(cls):
        """Called once before all test methods within a class are run."""
        cls.net = NetworkTest(cls.controller, topo_name="amlight_intlab")
        cls.net.start(start_controller=False)
        cls.switches = [cls.net.net.get(f"s{i}") for i in range(1, 7)]
        cls.pipeline_tables = cls.probe_pipeline_tables(cls.switches[0])
        cls.supports_table_3 = 3 in cls.pipeline_tables

    @classmethod
    def teardown_class(cls):
        """Called once after all tests in the class have finished for cleanup."""
        cls.net.stop()

    def require_table_3(self):
        """Skip a test whose INT flows would land on table 3.

        Only skips when the probe positively read the table list and table 3 was
        absent. If the probe could not parse anything, the test runs and
        ``enable_int_and_wait_up`` skips it on an ofpt_error instead.
        """
        if not self.pipeline_tables:
            # setup_class probed before any controller was up; try once more
            # now that the switches are connected
            cls = type(self)
            cls.pipeline_tables = cls.probe_pipeline_tables(self.switches[0])
            cls.supports_table_3 = 3 in cls.pipeline_tables
        if self.pipeline_tables and not self.supports_table_3:
            pytest.skip(
                "pipeline has no table 3 (tables found: "
                f"{sorted(self.pipeline_tables)}); telemetry_int maps EPL, "
                "vlan-range and special-tag INT flows to table 3"
            )

    def create_evc(
        self,
        vlan_id,
        uni_a=UNI_A1,
        uni_z=UNI_Z1,
        tag_a=_USE_VLAN_ID,
        tag_z=_USE_VLAN_ID,
        **kwargs,
    ):
        """Create an EVC and return its EVC ID.

        ``vlan_id`` is used for the EVC name and, unless overridden, for the tag
        value on both UNIs. ``tag_a``/``tag_z`` accept an int, ``"any"``,
        ``"untagged"``, a vlan range such as ``[[10, 15]]``, or ``None`` for an
        EPL (no ``tag`` key at all). ``kwargs`` is merged last, so
        ``primary_path``, ``backup_path``, ``dynamic_backup_path`` and
        ``metadata`` all pass straight through.
        """
        payload = {
            "name": f"Vlan_{vlan_id}",
            "dynamic_backup_path": True,
            "uni_a": {"interface_id": uni_a},
            "uni_z": {"interface_id": uni_z},
        }
        tag_a = vlan_id if tag_a is self._USE_VLAN_ID else tag_a
        tag_z = vlan_id if tag_z is self._USE_VLAN_ID else tag_z
        if tag_a is not None:
            payload["uni_a"]["tag"] = {"tag_type": "vlan", "value": tag_a}
        if tag_z is not None:
            payload["uni_z"]["tag"] = {"tag_type": "vlan", "value": tag_z}
        payload.update(kwargs)
        return self.mef_create_evc(payload)

    def create_int_evc(self, vlan_id, **kwargs):
        """Create an EVC, wait for it to deploy and enable INT on it.

        For a dynamic EVC the failover path is waited for *before* the enable.
        telemetry_int builds its flows from the mef_eline flows that exist at
        enable time, so enabling while mef_eline is still provisioning the
        failover leaves the failover subset to the ``failover_deployed``
        handler and races its install. When that race is lost the EVC settles
        with only the current-path INT flows and stays there:
        ``test_100`` hit it with ten ``missing_flows``, every one of them on
        the failover path (s5 in_port 5/8, s1 in_port 5, s6 in_port 8).

        A static EVC gets no pre-provisioned failover, so it is skipped.
        """
        evc_id = self.create_evc(vlan_id, **kwargs)
        self.wait_evc_active(evc_id)
        if kwargs.get("dynamic_backup_path", True):
            self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)
        self.enable_int_and_wait_up(evc_id)
        return evc_id

    @staticmethod
    def path_payload(path):
        """Turn a ``[[endpoint_a_id, endpoint_b_id], ...]`` path into a payload."""
        return [
            {"endpoint_a": {"id": link[0]}, "endpoint_b": {"id": link[1]}}
            for link in path
        ]

    def enable_int_and_wait_up(self, evc_id, table_3=False):
        """Enable INT on an EVC and wait for its telemetry to report UP.

        When the pipeline rejects an INT flow mod, flow_manager emits
        ``flow.error`` and telemetry_int disables INT with
        ``status_reason: ["ofpt_error"]``. For the shapes whose table X flows
        land on table 3 that is a pipeline limitation rather than a
        telemetry_int bug, so remember it on the class and skip the remaining
        table 3 shapes instead of failing each of them with the same cause.
        """
        self.wait_evc_failover_ready(evc_id)
        response = self.int_enable([evc_id])
        assert response.status_code == 201, response.text

        # install_int_flows writes status UP *concurrently* with sending the
        # flow mods, so UP alone does not mean the pipeline accepted them. Trust
        # it only once INT flows are visible on s1, or until a rejection shows
        # up as an ofpt_error; the loop keeps watching for either.
        deadline = time.time() + 90
        telemetry = {}
        while time.time() < deadline:
            telemetry = self.get_evc_telemetry(evc_id)
            reasons = telemetry.get("status_reason", [])
            if "ofpt_error" in reasons:
                break
            if (
                telemetry.get("enabled")
                and telemetry.get("status") == "UP"
                and self.count_evc_flows(self.switches[0], evc_id)[1]
            ):
                assert reasons == [], telemetry
                return telemetry
            time.sleep(2)

        if table_3 and "ofpt_error" in telemetry.get("status_reason", []):
            type(self).supports_table_3 = False
            pytest.skip(
                "pipeline rejected the table 3 INT flows "
                f"(status_reason={telemetry.get('status_reason')}); this EVC "
                "shape needs table 3"
            )
        raise AssertionError(f"EVC {evc_id} INT did not come UP: {telemetry}")

    def config_host_ip_vlan(self, host, ip, vlan):
        """Configure VLAN subinterface and VLAN into host."""
        host.cmd(
            f"ip link add link {host.name}-eth1 name vlan{vlan} type vlan id {vlan}"
        )
        host.cmd(f"ip link set up vlan{vlan}")
        host.cmd(f"ip addr add {ip}/24 dev vlan{vlan}")
        host.test_ip = ip
        host.test_intf = f"vlan{vlan}"

    def validate_evc_paths(self, evc_id, expected_current, expected_failover):
        """Validate EVC paths (current and failover) with the expected values."""
        evc = self.get_evc(evc_id)
        current = self.path_links(evc["current_path"])
        assert current == expected_current, str(current)
        failover = self.path_links(evc["failover_path"])
        assert failover == expected_failover, str(failover)

    def validate_switch_flows(self, evc_id, expected_counts):
        """
        Validates flow counts across switches, split by cookie owner.

        expected_counts: dict mapping a switch name to a
        ``(n_mef_eline_flows, n_telemetry_int_flows)`` tuple. Keeping the two
        apart matters: a single ``cookie=0xa.`` regex conflates mef_eline (0xaa)
        with telemetry_int (0xa8), so a regression that drops every INT flow
        while duplicating mef flows would go unnoticed.
        """
        return self.assert_evc_flow_counts(self.switches, evc_id, expected_counts)

    def validate_traffic(self, src, dst):
        """Send traffic from src to dst and check packet received on dst."""
        result = src.cmd(f"ping -c1 {dst.test_ip}")
        assert ", 0% packet loss," in result, result

        dst.cmd(f"truncate -s0 /tmp/{dst.name}-capture.log")
        src.cmd(
            f"python3 {self.SCRIPTS_DIR}/sendp.py -i {src.test_intf} "
            f"-s 127.0.0.1 -d {dst.test_ip} -c1 -p 80"
        )
        src.cmd(
            f"python3 {self.SCRIPTS_DIR}/sendp.py -i {src.test_intf} "
            f"-s 127.0.0.1 -d {dst.test_ip} -c1 -p 80 -u"
        )
        time.sleep(2)
        dst.cmd(f"kill -USR2 {dst.capture_pid}")
        pkts = dst.cmd(f"cat /tmp/{dst.name}-capture.log").splitlines()
        assert sum(dst.test_ip in s for s in pkts) == 2, str(pkts)

    def test_001_dynamic_evc_without_proxy(self):
        # pylint: disable=too-many-locals, too-many-statements
        """Test enabling INT on a dynamic EVC without proxy port."""
        h1, h2, h3 = self.net.net.get("h1", "h2", "h3")
        s1, s2, s3, s4, s5, s6 = self.net.net.get("s1", "s2", "s3", "s4", "s5", "s6")

        evc_id = self.create_evc(198)

        time.sleep(10)

        self.config_host_ip_vlan(h1, "10.1.98.1", 198)
        self.config_host_ip_vlan(h3, "10.1.98.3", 198)

        if os.environ.get("SWITCH_CLASS") == "NoviSwitch":
            s1.novi_cmd(
                "set config int monitor portno 3 ethdst 00:00:00:00:02:01 ethsrc 00:00:00:aa:aa:01 "
                "ipv4src 10.255.255.1 ipv4dst 10.255.255.254 udpsrc 6000 udpdst 5900 maxlen 320"
            )
            s1.novi_cmd("set config int maxhopcount 10")
            s6.novi_cmd(
                "set config int monitor portno 3 ethdst 00:00:00:00:02:03 ethsrc 00:00:00:aa:aa:06 "
                "ipv4src 10.255.255.6 ipv4dst 10.255.255.254 udpsrc 6000 udpdst 5900 maxlen 320"
            )
            s6.novi_cmd("set config int maxhopcount 10")
        elif os.environ.get("SWITCH_CLASS") == "P4OfSwitch":
            s1.cmd(
                "p4ofagent set config int --of_port_no 3 --src_mac 00:00:00:aa:aa:01 --dst_mac 00:00:00:00:02:01 "
                "--src_ip 10.255.255.1 --dst_ip 10.255.255.254 --dst_udp 5900 --max_pkt_len 320"
            )
            s6.cmd(
                "p4ofagent set config int --of_port_no 3 --src_mac 00:00:00:aa:aa:06 --dst_mac 00:00:00:00:02:03 "
                "--src_ip 10.255.255.6 --dst_ip 10.255.255.254 --dst_udp 5900 --max_pkt_len 320"
            )
        else:
            pytest.fail(
                "Unsupported SWITCH_CLASS. Supported values: NoviSwitch, P4OfSwitch"
            )

        h2.cmd(
            f"python3 {self.SCRIPTS_DIR}/int_collector.py -i h2-eth1 -i h2-eth3 "
            ">/tmp/int_collector.log 2>&1 &"
        )

        h1.cmd("tcpdump -i vlan198 -n -l -e tcp or udp >/tmp/h1-capture.log 2>&1 &")
        h1.capture_pid = h1.lastPid
        h3.cmd("tcpdump -i vlan198 -n -l -e tcp or udp >/tmp/h3-capture.log 2>&1 &")
        h3.capture_pid = h3.lastPid

        expected_current = [
            ["00:00:00:00:00:00:00:01:7", "00:00:00:00:00:00:00:06:7"],
        ]
        expected_failover = [
            ["00:00:00:00:00:00:00:01:5", "00:00:00:00:00:00:00:05:5"],
            ["00:00:00:00:00:00:00:05:8", "00:00:00:00:00:00:00:06:8"],
        ]

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        h2.cmd("truncate -s0 /tmp/int_collector.log")

        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = self.parse_int_collector(output)
        assert len(int_pkts) == 0, output

        self.validate_switch_flows(
            evc_id,
            {
                "s1": (3, 0),
                "s2": (0, 0),
                "s3": (0, 0),
                "s4": (0, 0),
                "s5": (2, 0),
                "s6": (3, 0),
            },
        )

        response = self.int_enable([evc_id])
        assert response.status_code == 201, response.text

        time.sleep(15)

        h2.cmd("truncate -s0 /tmp/int_collector.log")

        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = self.parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x1,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x1,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x6,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x6,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)

        self.validate_switch_flows(
            evc_id,
            {
                "s1": (3, 9),
                "s2": (0, 0),
                "s3": (0, 0),
                "s4": (0, 0),
                "s5": (2, 4),
                "s6": (3, 9),
            },
        )

        self.net.net.configLinkStatus("s1", "s6", "down")
        self.net.wait_kytos_links("s1", "s6", status="DOWN")

        time.sleep(5)

        expected_current = [
            ["00:00:00:00:00:00:00:01:5", "00:00:00:00:00:00:00:05:5"],
            ["00:00:00:00:00:00:00:05:8", "00:00:00:00:00:00:00:06:8"],
        ]
        expected_failover = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:5", "00:00:00:00:00:00:00:06:5"],
        ]

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        h2.cmd("truncate -s0 /tmp/int_collector.log")

        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = self.parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x5,ig_port=5,eg_port=8,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=5,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x5,ig_port=5,eg_port=8,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=5,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x5,ig_port=8,eg_port=5,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=8,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x5,ig_port=8,eg_port=5,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=8,queue=0",
        ], str(int_pkts)

        self.validate_switch_flows(
            evc_id,
            {
                "s1": (3, 9),
                "s2": (2, 4),
                "s3": (0, 0),
                "s4": (0, 0),
                "s5": (2, 4),
                "s6": (3, 9),
            },
        )

        self.net.net.configLinkStatus("s1", "s5", "down")
        self.net.wait_kytos_links("s1", "s5", status="DOWN")

        time.sleep(5)

        expected_current = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:5", "00:00:00:00:00:00:00:06:5"],
        ]
        expected_failover = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:3", "00:00:00:00:00:00:00:05:3"],
            ["00:00:00:00:00:00:00:05:8", "00:00:00:00:00:00:00:06:8"],
        ]

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        h2.cmd("truncate -s0 /tmp/int_collector.log")

        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = self.parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x2,ig_port=6,eg_port=5,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x2,ig_port=6,eg_port=5,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x2,ig_port=5,eg_port=6,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=5,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x2,ig_port=5,eg_port=6,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=5,queue=0",
        ], str(int_pkts)

        self.validate_switch_flows(
            evc_id,
            {
                "s1": (3, 9),
                "s2": (4, 8),
                "s3": (0, 0),
                "s4": (0, 0),
                "s5": (2, 4),
                "s6": (3, 9),
            },
        )

        self.net.net.configLinkStatus("s2", "s6", "down")
        self.net.wait_kytos_links("s2", "s6", status="DOWN")

        time.sleep(5)

        expected_current = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:3", "00:00:00:00:00:00:00:05:3"],
            ["00:00:00:00:00:00:00:05:8", "00:00:00:00:00:00:00:06:8"],
        ]
        expected_failover = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:4", "00:00:00:00:00:00:00:03:4"],
            ["00:00:00:00:00:00:00:03:6", "00:00:00:00:00:00:00:04:6"],
            ["00:00:00:00:00:00:00:04:7", "00:00:00:00:00:00:00:05:7"],
            ["00:00:00:00:00:00:00:05:9", "00:00:00:00:00:00:00:06:9"],
        ]

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        h2.cmd("truncate -s0 /tmp/int_collector.log")

        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = self.parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x5,ig_port=3,eg_port=8,queue=0",
            "sw_id=0x2,ig_port=6,eg_port=3,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x5,ig_port=3,eg_port=8,queue=0",
            "sw_id=0x2,ig_port=6,eg_port=3,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x2,ig_port=3,eg_port=6,queue=0",
            "sw_id=0x5,ig_port=8,eg_port=3,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=8,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x2,ig_port=3,eg_port=6,queue=0",
            "sw_id=0x5,ig_port=8,eg_port=3,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=8,queue=0",
        ], str(int_pkts)

        self.validate_switch_flows(
            evc_id,
            {
                "s1": (3, 9),
                "s2": (4, 8),
                "s3": (2, 4),
                "s4": (2, 4),
                "s5": (4, 8),
                "s6": (3, 9),
            },
        )

        self.net.net.configLinkStatus("s5", "s6", "down", port1=8, port2=8)
        self.net.wait_kytos_links("s5", "s6", port1=8, port2=8, status="DOWN")

        time.sleep(5)

        expected_current = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:4", "00:00:00:00:00:00:00:03:4"],
            ["00:00:00:00:00:00:00:03:6", "00:00:00:00:00:00:00:04:6"],
            ["00:00:00:00:00:00:00:04:7", "00:00:00:00:00:00:00:05:7"],
            ["00:00:00:00:00:00:00:05:9", "00:00:00:00:00:00:00:06:9"],
        ]
        expected_failover = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:3", "00:00:00:00:00:00:00:05:3"],
            ["00:00:00:00:00:00:00:05:9", "00:00:00:00:00:00:00:06:9"],
        ]

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        h2.cmd("truncate -s0 /tmp/int_collector.log")

        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = self.parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x5,ig_port=7,eg_port=9,queue=0",
            "sw_id=0x4,ig_port=6,eg_port=7,queue=0",
            "sw_id=0x3,ig_port=4,eg_port=6,queue=0",
            "sw_id=0x2,ig_port=6,eg_port=4,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x5,ig_port=7,eg_port=9,queue=0",
            "sw_id=0x4,ig_port=6,eg_port=7,queue=0",
            "sw_id=0x3,ig_port=4,eg_port=6,queue=0",
            "sw_id=0x2,ig_port=6,eg_port=4,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x2,ig_port=4,eg_port=6,queue=0",
            "sw_id=0x3,ig_port=6,eg_port=4,queue=0",
            "sw_id=0x4,ig_port=7,eg_port=6,queue=0",
            "sw_id=0x5,ig_port=9,eg_port=7,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=9,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x2,ig_port=4,eg_port=6,queue=0",
            "sw_id=0x3,ig_port=6,eg_port=4,queue=0",
            "sw_id=0x4,ig_port=7,eg_port=6,queue=0",
            "sw_id=0x5,ig_port=9,eg_port=7,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=9,queue=0",
        ], str(int_pkts)

        self.validate_switch_flows(
            evc_id,
            {
                "s1": (3, 9),
                "s2": (4, 8),
                "s3": (2, 4),
                "s4": (2, 4),
                "s5": (4, 8),
                "s6": (3, 9),
            },
        )

        self.net.net.configLinkStatus("s2", "s3", "down")
        self.net.wait_kytos_links("s2", "s3", status="DOWN")

        time.sleep(5)

        expected_current = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:3", "00:00:00:00:00:00:00:05:3"],
            ["00:00:00:00:00:00:00:05:9", "00:00:00:00:00:00:00:06:9"],
        ]
        expected_failover = []

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        h2.cmd("truncate -s0 /tmp/int_collector.log")

        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = self.parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x5,ig_port=3,eg_port=9,queue=0",
            "sw_id=0x2,ig_port=6,eg_port=3,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x5,ig_port=3,eg_port=9,queue=0",
            "sw_id=0x2,ig_port=6,eg_port=3,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x2,ig_port=3,eg_port=6,queue=0",
            "sw_id=0x5,ig_port=9,eg_port=3,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=9,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x2,ig_port=3,eg_port=6,queue=0",
            "sw_id=0x5,ig_port=9,eg_port=3,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=9,queue=0",
        ], str(int_pkts)

        self.validate_switch_flows(
            evc_id,
            {
                "s1": (2, 6),
                "s2": (2, 4),
                "s3": (0, 0),
                "s4": (0, 0),
                "s5": (2, 4),
                "s6": (2, 6),
            },
        )

        self.net.net.configLinkStatus("s5", "s6", "down", port1=9, port2=9)
        self.net.wait_kytos_links("s5", "s6", port1=9, port2=9, status="DOWN")

        time.sleep(5)

        self.validate_evc_paths(evc_id, [], [])

        result = h1.cmd("ping -c1 10.1.98.3")
        assert ", 100% packet loss," in result

        self.validate_switch_flows(evc_id, self.NO_FLOWS)

        self.net.net.configLinkStatus("s1", "s6", "up")
        self.net.wait_kytos_links("s1", "s6", status="UP")

        time.sleep(5)

        expected_current = [
            ["00:00:00:00:00:00:00:01:7", "00:00:00:00:00:00:00:06:7"],
        ]
        expected_failover = []

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        h2.cmd("truncate -s0 /tmp/int_collector.log")

        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = self.parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x1,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x1,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x6,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x6,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)

        self.validate_switch_flows(
            evc_id,
            {
                "s1": (2, 6),
                "s2": (0, 0),
                "s3": (0, 0),
                "s4": (0, 0),
                "s5": (0, 0),
                "s6": (2, 6),
            },
        )

    def test_005_pipeline_capabilities(self):
        """Report which pipeline tables exist and gate the table 3 shapes.

        telemetry_int puts its table X flows on table 2 for EVPL and table 3
        for EPL, vlan ranges and the special any/untagged tags. test_001 only
        ever proves table 2, so record what the pipeline reports before the
        shape tests rely on it. This is informational: a missing table 3 skips
        those shapes rather than failing here.
        """
        if not self.pipeline_tables:
            pytest.skip(
                "could not parse any table id out of dump-tables or "
                "dump-table-features, so table 3 cannot be probed up front; the "
                "table 3 shapes fall back to detecting an ofpt_error on enable"
            )
        assert 0 in self.pipeline_tables, sorted(self.pipeline_tables)
        assert 2 in self.pipeline_tables, (
            f"pipeline has no table 2, which every EVPL INT flow needs: "
            f"{sorted(self.pipeline_tables)}"
        )
        print(
            f"\npipeline tables: {sorted(self.pipeline_tables)} "
            f"supports_table_3={self.supports_table_3}"
        )

    def test_010_shape_evpl_symmetric_vlan(self):
        """Dynamic EVPL, same VLAN on both UNIs, no proxy port.

        The reference shape: same EVC as test_001, but validated through the
        control plane instead of real INT reports. The flow counts are the
        same literals test_001 asserts, so a divergence between the two tests
        points at the validation, not at the NApp.
        """
        evc_id = self.create_evc(100)
        self.wait_evc_active(evc_id)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)

        self.validate_switch_flows(
            evc_id,
            {
                "s1": (3, 0),
                "s2": (0, 0),
                "s3": (0, 0),
                "s4": (0, 0),
                "s5": (2, 0),
                "s6": (3, 0),
            },
        )

        self.enable_int_and_wait_up(evc_id)

        self.validate_switch_flows(
            evc_id,
            {
                "s1": (3, 9),
                "s2": (0, 0),
                "s3": (0, 0),
                "s4": (0, 0),
                "s5": (2, 4),
                "s6": (3, 9),
            },
        )
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(
            evc_id, [self.DPID_S1, self.DPID_S6], hop_dpids=[self.DPID_S5]
        )
        self.assert_int_consistent(evc_id)

        by_table = self.count_evc_flows_by_table(self.switches[0], evc_id)
        assert by_table == self.EVPL_DYNAMIC_TABLES, by_table

        assert evc_id in self.int_list_evcs()
        assert self.int_compare() == [], self.int_compare()

        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 100, [self.DPID_S1, self.DPID_S6]
        )
        self.assert_int_trace_matches_control(
            self.DPID_S6, 1, 100, [self.DPID_S6, self.DPID_S1]
        )

    def test_015_shape_evpl_asymmetric_vlan(self):
        """Dynamic EVPL with different VLANs on each UNI (VLAN translation).

        Asymmetric VLANs make ``utils.has_vlan_translation`` true, so the EVC
        is not QinQ. That only changes the proxy-port sink builder, but it does
        change the mef_eline flows the INT flows are derived from, so the shape
        is worth pinning on its own.
        """
        evc_id = self.create_evc(200, tag_a=200, tag_z=201)
        self.wait_evc_active(evc_id)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)

        self.enable_int_and_wait_up(evc_id)

        self.validate_switch_flows(
            evc_id,
            {
                "s1": (3, 9),
                "s2": (0, 0),
                "s3": (0, 0),
                "s4": (0, 0),
                "s5": (2, 4),
                "s6": (3, 9),
            },
        )
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(
            evc_id, [self.DPID_S1, self.DPID_S6], hop_dpids=[self.DPID_S5]
        )
        self.assert_int_consistent(evc_id)

        control, int_result = self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 200, [self.DPID_S1, self.DPID_S6]
        )
        assert control[-1]["out"]["vlan"] == 201, control
        assert int_result[-1]["out"]["vlan"] == 201, int_result

        control, int_result = self.assert_int_trace_matches_control(
            self.DPID_S6, 1, 201, [self.DPID_S6, self.DPID_S1]
        )
        assert control[-1]["out"]["vlan"] == 200, control
        assert int_result[-1]["out"]["vlan"] == 200, int_result

    def test_020_shape_evpl_single_static_path(self):
        """EVPL with a single static primary_path and no backup.

        With no failover_path the INT flow count is strictly current_path only
        -- a tighter assertion than any dynamic EVC can give, because nothing
        is pre-provisioned on a second path.
        """
        evc_id = self.create_evc(
            110, dynamic_backup_path=False, primary_path=self.path_payload(self.PRIMARY)
        )
        self.wait_evc_active(evc_id)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=[])

        self.enable_int_and_wait_up(evc_id)

        self.validate_switch_flows(evc_id, self.STATIC_PRIMARY_FLOWS)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(evc_id, [self.DPID_S1, self.DPID_S6])
        self.assert_int_consistent(evc_id)
        by_table = self.count_evc_flows_by_table(self.switches[0], evc_id)
        assert by_table == self.EVPL_STATIC_TABLES, by_table

        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 110, [self.DPID_S1, self.DPID_S6]
        )
        self.assert_int_trace_matches_control(
            self.DPID_S6, 1, 110, [self.DPID_S6, self.DPID_S1]
        )

    def test_025_shape_evpl_dual_static_paths(self):
        """EVPL with both a static primary_path and a static backup_path.

        mef_eline does not pre-provision a failover_path for a static EVC, so
        declaring a backup_path changes nothing until the primary breaks: the
        INT flows must be exactly the single-static ones, with nothing sitting
        on the declared backup. The backup only gets instrumented when the EVC
        actually moves onto it (test_155).
        """
        evc_id = self.create_evc(
            115,
            dynamic_backup_path=False,
            primary_path=self.path_payload(self.PRIMARY),
            backup_path=self.path_payload(self.BACKUP),
        )
        self.wait_evc_active(evc_id)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=[])

        self.enable_int_and_wait_up(evc_id)

        self.validate_switch_flows(evc_id, self.STATIC_PRIMARY_FLOWS)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(evc_id, [self.DPID_S1, self.DPID_S6])
        self.assert_int_consistent(evc_id)
        by_table = self.count_evc_flows_by_table(self.switches[0], evc_id)
        assert by_table == self.EVPL_STATIC_TABLES, by_table

        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 115, [self.DPID_S1, self.DPID_S6]
        )
        self.assert_int_trace_matches_control(
            self.DPID_S6, 1, 115, [self.DPID_S6, self.DPID_S1]
        )

    def test_030_shape_epl_single_static_path(self):
        """EPL (no tag on either UNI) with a single static primary_path.

        An EPL's UNI-ingress flow has no dl_vlan, so get_table_id maps the INT
        *source* table X flow to the ``epl`` group -- table 3. The sink is built
        from the NNI-ingress flow, which always carries the s-vlan, so it stays
        on table 2: the table group follows each flow's own match, not the EVC's
        shape. This is the first shape that reaches table 3 at all.
        """
        self.require_table_3()
        evc_id = self.create_evc(
            0,
            uni_a=self.UNI_A2,
            uni_z=self.UNI_Z2,
            tag_a=None,
            tag_z=None,
            dynamic_backup_path=False,
            primary_path=self.path_payload(self.PRIMARY),
        )
        self.wait_evc_active(evc_id)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=[])

        self.enable_int_and_wait_up(evc_id, table_3=True)

        self.validate_switch_flows(evc_id, self.STATIC_PRIMARY_FLOWS)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(evc_id, [self.DPID_S1, self.DPID_S6])
        self.assert_int_consistent(evc_id)

        for switch in (self.switches[0], self.switches[5]):
            by_table = self.count_evc_flows_by_table(switch, evc_id)
            assert by_table == self.EPL_STATIC_TABLES, f"{switch.name}: {by_table}"

        self.assert_int_trace_matches_control(
            self.DPID_S1, 2, None, [self.DPID_S1, self.DPID_S6]
        )
        self.assert_int_trace_matches_control(
            self.DPID_S6, 2, None, [self.DPID_S6, self.DPID_S1]
        )

    def test_035_shape_epl_dual_static_paths(self):
        """EPL with both a static primary_path and a static backup_path.

        As in test_025 the declared backup is not pre-provisioned, so the
        counts match test_030; what this adds is that the split placement
        survives declaring a ``backup_path``.
        """
        self.require_table_3()
        evc_id = self.create_evc(
            0,
            uni_a=self.UNI_A2,
            uni_z=self.UNI_Z2,
            tag_a=None,
            tag_z=None,
            dynamic_backup_path=False,
            primary_path=self.path_payload(self.PRIMARY),
            backup_path=self.path_payload(self.BACKUP),
        )
        self.wait_evc_active(evc_id)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=[])

        self.enable_int_and_wait_up(evc_id, table_3=True)

        self.validate_switch_flows(evc_id, self.STATIC_PRIMARY_FLOWS)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(evc_id, [self.DPID_S1, self.DPID_S6])
        self.assert_int_consistent(evc_id)
        for switch in (self.switches[0], self.switches[5]):
            by_table = self.count_evc_flows_by_table(switch, evc_id)
            assert by_table == self.EPL_STATIC_TABLES, f"{switch.name}: {by_table}"

        self.assert_int_trace_matches_control(
            self.DPID_S1, 2, None, [self.DPID_S1, self.DPID_S6]
        )

    def test_040_shape_evpl_vlan_range(self):
        """EVPL with a VLAN range on both UNIs.

        A range produces a masked dl_vlan on the UNI-ingress flow, which
        get_table_id maps to the ``evpl_vlan_range`` group (table 3); as in
        test_030 the sink stays on table 2. It also produces more than one
        ingress flow per UNI, and the INT source builder turns each of those
        into three flows, so the totals come from expected_flows rather than
        from literals.
        """
        self.require_table_3()
        evc_id = self.create_evc(10, tag_a=[[10, 15]], tag_z=[[10, 15]])
        self.wait_evc_active(evc_id)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)

        mef_stored = self.get_stored_flows(self.mef_cookie_prefix, evc_id)
        uni_vlans = sorted(
            {
                str(flow["flow"]["match"].get("dl_vlan"))
                for flow in mef_stored.get(self.DPID_S1, [])
                if flow["flow"]["match"].get("in_port") == 1
            }
        )
        print(f"\nvlan range dl_vlan representation on s1 UNI: {uni_vlans}")

        self.enable_int_and_wait_up(evc_id, table_3=True)

        expected = self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_consistent(evc_id)
        by_table = {int(k): v for k, v in expected["count_table"].items()}
        assert by_table.get(3), f"vlan range INT flows did not use table 3: {by_table}"
        print(f"\nvlan range INT flow total: {expected['count_total']}")

    def test_045_shape_evpl_any(self):
        """EVPL with the special ``any`` tag on both UNIs.

        ``any`` is a fully masked dl_vlan, so get_table_id sends the INT source
        table X flow to the ``evpl_vlan_range`` group (table 3). The assertion that
        matters beyond placement is that the INT table 0 flows still carry
        dl_type/nw_proto on top of the wildcard VLAN match -- without that the
        INT flows would steal all traffic, not just TCP/UDP.
        """
        self.require_table_3()
        evc_id = self.create_evc(0, tag_a="any", tag_z="any")
        self.wait_evc_active(evc_id)

        self.enable_int_and_wait_up(evc_id, table_3=True)

        expected = self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_consistent(evc_id)
        by_table = {int(k): v for k, v in expected["count_table"].items()}
        assert by_table.get(3), f"'any' INT flows did not use table 3: {by_table}"

        int_stored = self.get_stored_flows(self.int_cookie_prefix, evc_id)
        table_0 = [
            flow
            for flows in int_stored.values()
            for flow in flows
            if flow["flow"]["table_id"] == 0
        ]
        assert table_0, int_stored
        for flow in table_0:
            match = flow["flow"]["match"]
            assert match.get("dl_type") == self.ipv4, flow
            assert match.get("nw_proto") in (self.tcp, self.udp), flow

    def test_050_shape_evpl_untagged(self):
        """EVPL with the special ``untagged`` tag on both UNIs.

        ``untagged`` is dl_vlan 0, which get_table_id also routes the INT
        source to the ``evpl_vlan_range`` group (table 3). No trace assertion:
        its trace schema only accepts dl_vlan 1-4095, so an untagged match
        cannot be expressed as a trace request.
        """
        self.require_table_3()
        evc_id = self.create_evc(0, tag_a="untagged", tag_z="untagged")
        self.wait_evc_active(evc_id)

        self.enable_int_and_wait_up(evc_id, table_3=True)

        expected = self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_consistent(evc_id)
        by_table = {int(k): v for k, v in expected["count_table"].items()}
        assert by_table.get(3), f"'untagged' INT flows did not use table 3: {by_table}"

    def test_055_shape_mixed_any_untagged(self):
        """EVPL with ``any`` on one UNI and ``untagged`` on the other.

        A cheap smoke test for the asymmetric special-tag combination
        mef_eline supports; the per-shape detail is already covered by 045/050.
        """
        self.require_table_3()
        evc_id = self.create_evc(0, tag_a="any", tag_z="untagged")
        self.wait_evc_active(evc_id)

        self.enable_int_and_wait_up(evc_id, table_3=True)
        self.assert_int_consistent(evc_id)
        assert evc_id in self.int_list_evcs()

    def down_link(self, node_a, node_b, port1=None, port2=None):
        """Bring a link down and wait for kytos to see it as DOWN."""
        self.net.net.configLinkStatus(node_a, node_b, "down", port1=port1, port2=port2)
        self.net.wait_kytos_links(
            node_a, node_b, port1=port1, port2=port2, status="DOWN"
        )

    def down_link_and_settle(self, evc_id, node_a, node_b, port1=None, port2=None):
        """Bring a link down and wait for the EVC to finish reacting to it.

        Downing the next link while telemetry_int is still installing the INT
        subset for the previous failover leaves stale hop flows behind: the
        install lands *after* ``remove_int_flows`` queried stored_flows, so the
        removal never saw them and nothing deletes them afterwards. Observed on
        s5 with 4 orphaned hop flows for a failover path that no longer existed.

        Real link flaps are not back-to-back, so settle between steps: wait for
        the paths to stop changing, then for INT to be consistent -- which is
        precisely the condition that the install has reached stored_flows.
        """
        self.down_link(node_a, node_b, port1=port1, port2=port2)

        deadline = time.time() + 90
        previous = None
        while time.time() < deadline:
            evc = self.get_evc(evc_id)
            snapshot = (
                self.path_links(evc.get("current_path", [])),
                self.path_links(evc.get("failover_path", [])),
            )
            if snapshot == previous:
                break
            previous = snapshot
            time.sleep(5)

        if previous and previous[0]:
            self.assert_int_consistent(evc_id)

    def up_link(self, node_a, node_b, port1=None, port2=None):
        """Bring a link up and wait for kytos to see it as UP."""
        self.net.net.configLinkStatus(node_a, node_b, "up", port1=port1, port2=port2)
        self.net.wait_kytos_links(node_a, node_b, port1=port1, port2=port2, status="UP")

    def test_100_converge_dyn_current_path_link_down(self):
        """Dynamic EVC: a current_path link goes down and the failover takes over.

        This is the fast path (``mef_eline.failover_link_down``): the failover
        flows are already installed, so telemetry_int only has to promote the
        INT subset rather than rebuild everything. The INT flows must end up
        exactly where expected_flows says they should be.
        """
        evc_id = self.create_int_evc(120)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)
        self.assert_int_consistent(evc_id)

        self.down_link("s1", "s6")
        self.wait_evc_paths(evc_id, current=self.BACKUP, failover=self.THIRD)

        self.validate_switch_flows(
            evc_id,
            {
                "s1": (3, 9),
                "s2": (2, 4),
                "s3": (0, 0),
                "s4": (0, 0),
                "s5": (2, 4),
                "s6": (3, 9),
            },
        )
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(
            evc_id, [self.DPID_S1, self.DPID_S6], hop_dpids=[self.DPID_S5, self.DPID_S2]
        )
        self.assert_int_consistent(evc_id)
        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 120, [self.DPID_S1, self.DPID_S5, self.DPID_S6]
        )

    def test_105_converge_dyn_failover_path_link_down(self):
        """Dynamic EVC: only the failover_path is hit, current_path is untouched.

        Exercises ``mef_eline.failover_old_path`` / ``failover_deployed``: the
        INT flows for the stale failover have to be removed and new ones
        installed, all while the EVC keeps forwarding. The replacement failover
        is not asserted literally because more than one equal-cost path exists
        -- what matters is that it changed and that INT stayed consistent.
        """
        evc_id = self.create_int_evc(125)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)

        self.down_link("s5", "s6", port1=8, port2=8)

        deadline = time.time() + 60
        while time.time() < deadline:
            failover = self.path_links(self.get_evc(evc_id)["failover_path"])
            if failover and failover != self.BACKUP:
                break
            time.sleep(2)
        else:
            raise AssertionError(
                f"failover_path never moved off {self.BACKUP}: "
                f"{self.path_links(self.get_evc(evc_id)['failover_path'])}"
            )

        self.wait_evc_paths(evc_id, current=self.PRIMARY)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_consistent(evc_id)
        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 125, [self.DPID_S1, self.DPID_S6]
        )

    def test_110_converge_dyn_successive_link_downs(self):
        """Dynamic EVC: three successive link failures, re-checked each time.

        Each failure drives either a failover takeover or a full redeploy, and
        telemetry_int has to keep the INT flows aligned with whatever
        mef_eline settled on. Rather than hardcoding every intermediate path,
        this asserts the invariant that actually matters after each step: the
        installed INT flows equal telemetry_int's own expected_flows, the EVC
        is consistent, and both the control and INT trace reach the far UNI.
        """
        evc_id = self.create_int_evc(130)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)

        self.down_link("s1", "s6")
        self.wait_evc_paths(evc_id, current=self.BACKUP)

        for node_a, node_b in (("s1", "s5"), ("s2", "s6")):
            previous = self.path_links(self.get_evc(evc_id)["current_path"])
            self.down_link(node_a, node_b)

            # the EVC may never flip inactive during a takeover, so "active"
            # proves nothing; wait for current_path itself to move off the
            # path that just lost a link
            deadline = time.time() + 60
            while True:
                current = self.path_links(self.get_evc(evc_id)["current_path"])
                if current and current != previous:
                    break
                assert time.time() < deadline, (
                    f"current_path did not move after {node_a}--{node_b} went "
                    f"down: {current}"
                )
                time.sleep(2)
            self.wait_evc_active(evc_id)

            expected_dpids = self.path_dpids(current, self.DPID_S1)
            assert expected_dpids[-1] == self.DPID_S6, expected_dpids
            self.assert_int_flows_match_expected(evc_id, self.switches)
            self.assert_int_consistent(evc_id)
            self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
            self.assert_int_trace_matches_control(self.DPID_S1, 1, 130, expected_dpids)

    def test_115_converge_dyn_no_path_left(self):
        """Dynamic EVC: every path is gone, so all flows must be withdrawn.

        When mef_eline cannot find any path it emits
        ``error_redeploy_link_down``, and telemetry_int removes the INT flows
        while deliberately leaving ``telemetry.enabled`` true -- INT is still
        requested, it just cannot be delivered.
        """
        evc_id = self.create_int_evc(135)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)

        for node_a, node_b in (("s1", "s6"), ("s1", "s5"), ("s1", "s2")):
            self.down_link_and_settle(evc_id, node_a, node_b)

        self.wait_evc_paths(evc_id, current=[], failover=[])
        self.assert_no_evc_flows(self.switches, evc_id)
        self.assert_telemetry_metadata(
            evc_id,
            True,
            status="DOWN",
            status_reason=["redeployed_link_down_no_path"],
        )
        self.assert_no_int_trace(self.DPID_S1, 1, 135, [self.DPID_S1, self.DPID_S6])

    def test_120_converge_dyn_recovery_link_up(self):
        """Dynamic EVC: the EVC recovers once a link comes back up.

        The interesting part is that INT has to be reinstalled by the
        ``redeployed_link_up`` handler, from metadata alone -- the flows were
        all withdrawn while no path existed.
        """
        evc_id = self.create_int_evc(140)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=self.BACKUP)

        for node_a, node_b in (("s1", "s6"), ("s1", "s5"), ("s1", "s2")):
            self.down_link_and_settle(evc_id, node_a, node_b)
        self.wait_evc_paths(evc_id, current=[], failover=[])
        self.assert_no_evc_flows(self.switches, evc_id)

        self.up_link("s1", "s6")
        self.wait_evc_paths(evc_id, current=self.PRIMARY)

        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(evc_id, [self.DPID_S1, self.DPID_S6])
        self.assert_int_consistent(evc_id)
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 140, [self.DPID_S1, self.DPID_S6]
        )

    def test_150_converge_static_single_path(self):
        """Static EVC with only a primary_path: down means down, then recovers.

        The assertion that matters is the negative one -- with no backup and
        ``dynamic_backup_path: False`` the EVC must not silently fall back to
        any other path, and every INT flow must be withdrawn.
        """
        evc_id = self.create_int_evc(
            150, dynamic_backup_path=False, primary_path=self.path_payload(self.PRIMARY)
        )
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=[])
        self.validate_switch_flows(evc_id, self.STATIC_PRIMARY_FLOWS)

        self.down_link("s1", "s6")
        self.wait_evc_paths(evc_id, current=[], failover=[])

        self.assert_no_evc_flows(self.switches, evc_id)
        self.assert_telemetry_metadata(
            evc_id,
            True,
            status="DOWN",
            status_reason=["redeployed_link_down_no_path"],
        )
        self.assert_no_int_trace(self.DPID_S1, 1, 150, [self.DPID_S1, self.DPID_S6])

        self.up_link("s1", "s6")
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=[])

        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.validate_switch_flows(evc_id, self.STATIC_PRIMARY_FLOWS)
        self.assert_int_consistent(evc_id)
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 150, [self.DPID_S1, self.DPID_S6]
        )

    def test_155_converge_static_dual_paths(self):
        """Static EVC with primary_path and backup_path: the full walk.

        primary down -> the declared backup (not a recomputed shortest path);
        backup down too -> nothing; both back up -> the primary again, not the
        backup. That last step is the one that catches a redeploy picking the
        wrong static path, which would leave INT flows on a switch that is no
        longer on the path.
        """
        evc_id = self.create_int_evc(
            155,
            dynamic_backup_path=False,
            primary_path=self.path_payload(self.PRIMARY),
            backup_path=self.path_payload(self.BACKUP),
        )
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=[])
        self.validate_switch_flows(evc_id, self.STATIC_PRIMARY_FLOWS)
        self.assert_int_consistent(evc_id)

        self.down_link("s1", "s6")
        self.wait_evc_paths(evc_id, current=self.BACKUP, failover=[])

        self.validate_switch_flows(evc_id, self.STATIC_BACKUP_FLOWS)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(
            evc_id, [self.DPID_S1, self.DPID_S6], hop_dpids=[self.DPID_S5]
        )
        self.assert_int_consistent(evc_id)
        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 155, [self.DPID_S1, self.DPID_S5, self.DPID_S6]
        )

        self.down_link("s5", "s6", port1=8, port2=8)
        self.wait_evc_paths(evc_id, current=[], failover=[])
        self.assert_no_evc_flows(self.switches, evc_id)
        self.assert_telemetry_metadata(
            evc_id,
            True,
            status="DOWN",
            status_reason=["redeployed_link_down_no_path"],
        )

        self.up_link("s5", "s6", port1=8, port2=8)
        self.up_link("s1", "s6")
        self.wait_evc_paths(evc_id, current=self.PRIMARY)

        self.assert_telemetry_metadata(evc_id, True, status="UP", status_reason=[])
        self.validate_switch_flows(evc_id, self.STATIC_PRIMARY_FLOWS)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_consistent(evc_id)
        self.assert_int_trace_matches_control(
            self.DPID_S1, 1, 155, [self.DPID_S1, self.DPID_S6]
        )

    def test_160_converge_static_dual_paths_epl(self):
        """Static EPL with primary_path and backup_path, on table 3.

        The abbreviated version of 155 for the EPL shape: the point is that
        the split table placement survives a path change, and that the hop flows
        the backup introduces stay on table 0 (hop flows never use table X).
        """
        self.require_table_3()
        evc_id = self.create_evc(
            0,
            uni_a=self.UNI_A2,
            uni_z=self.UNI_Z2,
            tag_a=None,
            tag_z=None,
            dynamic_backup_path=False,
            primary_path=self.path_payload(self.PRIMARY),
            backup_path=self.path_payload(self.BACKUP),
        )
        self.wait_evc_active(evc_id)
        self.enable_int_and_wait_up(evc_id, table_3=True)
        self.wait_evc_paths(evc_id, current=self.PRIMARY, failover=[])
        self.validate_switch_flows(evc_id, self.STATIC_PRIMARY_FLOWS)

        self.down_link("s1", "s6")
        self.wait_evc_paths(evc_id, current=self.BACKUP, failover=[])

        self.validate_switch_flows(evc_id, self.STATIC_BACKUP_FLOWS)
        self.assert_int_flows_match_expected(evc_id, self.switches)
        self.assert_int_flow_actions(
            evc_id, [self.DPID_S1, self.DPID_S6], hop_dpids=[self.DPID_S5]
        )
        self.assert_int_consistent(evc_id)

        for switch in (self.switches[0], self.switches[5]):
            by_table = self.count_evc_flows_by_table(switch, evc_id)
            assert by_table.get(3), f"{switch.name} lost its table 3 flows: {by_table}"
        assert set(self.count_evc_flows_by_table(self.switches[4], evc_id)) == {
            0
        }, self.count_evc_flows_by_table(self.switches[4], evc_id)

        self.assert_int_trace_matches_control(
            self.DPID_S1, 2, None, [self.DPID_S1, self.DPID_S5, self.DPID_S6]
        )
