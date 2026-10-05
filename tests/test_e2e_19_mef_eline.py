"""EP041: fast convergence for static EVCs (single/dual static, with and
without a dynamic backup): link down, link up and admin path updates."""

import json
import time

import requests

from .helpers import NetworkTest

CONTROLLER = "127.0.0.1"
KYTOS_API = "http://%s:8181/api/kytos" % CONTROLLER


def create_evc(payload: dict, wait: int = 10) -> str:
    """POST an EVC, assert 201, wait for deploy, return its id."""
    response = requests.post(KYTOS_API + "/mef_eline/v2/evc/", json=payload)
    assert response.status_code == 201, response.text
    evc_id = response.json()["circuit_id"]
    time.sleep(wait)
    return evc_id


def get_evc(evc_id: str) -> dict:
    response = requests.get(KYTOS_API + "/mef_eline/v2/evc/" + evc_id)
    assert response.status_code == 200, response.text
    return response.json()


def path_endpoint_ids(path: list[dict]) -> set:
    """Interface ids traversed by a path (response or spec)."""
    ids = set()
    for link in path:
        ids.add(link["endpoint_a"]["id"])
        ids.add(link["endpoint_b"]["id"])
    return ids


def installed_flows(evc_id: str) -> dict:
    """{dpid: [flow,...]} of this EVC's installed flows (by cookie)."""
    cookie = int(f"0xaa{evc_id}", 16)
    url = (
        f"{KYTOS_API}/flow_manager/v2/stored_flows/"
        f"?cookie_range={cookie}&cookie_range={cookie}&state=installed"
    )
    response = requests.get(url)
    assert response.status_code == 200, response.text
    return response.json()


def flow_counts(evc_id: str) -> dict:
    """{dpid: n_installed_flows} for this EVC."""
    return {dpid: len(flows)
            for dpid, flows in installed_flows(evc_id).items()}


def flow_keys(evc_id: str) -> set:
    """{(dpid, flow as json)} of this EVC's installed flows, to diff them
    across a convergence event."""
    kept = ("match", "actions", "instructions", "priority", "cookie",
            "table_id")
    return {
        (dpid, json.dumps({k: doc["flow"].get(k) for k in kept},
                          sort_keys=True))
        for dpid, docs in installed_flows(evc_id).items()
        for doc in docs
    }


def available_tags(interfaces) -> dict:
    """Available tags per interface from topology tag_ranges."""
    response = requests.get(
        f"{KYTOS_API}/topology/v3/interfaces/tag_ranges"
    )
    assert response.ok, response.text
    data = response.json()
    return {i: data[i]["available_tags"] for i in interfaces}


def all_available_tags() -> dict:
    """Available tags for every interface (leak-check baseline)."""
    response = requests.get(
        f"{KYTOS_API}/topology/v3/interfaces/tag_ranges"
    )
    assert response.ok, response.text
    return {i: d["available_tags"] for i, d in response.json().items()}


def delete_evc(evc_id: str, wait: int = 10) -> None:
    response = requests.delete(KYTOS_API + "/mef_eline/v2/evc/" + evc_id)
    assert response.status_code == 200, response.text
    time.sleep(wait)


def path_s_vlans(path: list[dict]) -> list:
    """s_vlan per link of a path as returned by the API (None when unset)."""
    return [link.get("metadata", {}).get("s_vlan") for link in path]


def assert_deployed(path: list[dict]) -> None:
    """A kept-installed path carries an s_vlan on every link."""
    vlans = path_s_vlans(path)
    assert path and all(v is not None for v in vlans), (path, vlans)


def assert_not_deployed(path: list[dict]) -> None:
    """A never-deployed path has no s_vlan on any link."""
    assert all(v is None for v in path_s_vlans(path)), path_s_vlans(path)


def redeploy_evc(evc_id: str, wait: int = 10) -> None:
    response = requests.patch(
        KYTOS_API + "/mef_eline/v2/evc/" + evc_id + "/redeploy")
    assert response.status_code == 202, response.text
    time.sleep(wait)


def update_evc(evc_id: str, payload: dict, wait: int = 10) -> None:
    response = requests.patch(
        KYTOS_API + "/mef_eline/v2/evc/" + evc_id, json=payload)
    assert response.status_code == 200, response.text
    time.sleep(wait)


def path_switch_ids(path: list[dict]) -> set:
    """Set of switch dpids traversed by a path."""
    return {i.rsplit(":", 1)[0] for i in path_endpoint_ids(path)}


def assert_only_paths_hold_vlans(baseline: dict, *paths, ignore=()) -> None:
    """Interfaces off the given paths (and ignore) match the baseline."""
    held = set(ignore).union(*(path_endpoint_ids(p) for p in paths))
    now = all_available_tags()
    for intf, avail in baseline.items():
        if intf not in held:
            assert now[intf] == avail, (intf, avail, now[intf])


class TestE2EMefEline:
    net = None

    def setup_method(self, method):
        """Reset all links up and restart kytos with a clean database."""
        self.net.config_all_links_up()
        self.net.restart_kytos_clean()
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER, topo_name="multi")
        cls.net.start(start_controller=False)

        # `multi` topology (ports: horizontal = left:right, vertical = top:s6)
        #
        #   h1         h2         h3         h4         h5
        #   |1         |1         |1         |1         |1
        #   s1 --2:2-- s2 --3:2-- s3 --3:2-- s4 --3:2-- s5
        #   |3:3       |4:4       |4:5       |4:6       |3:2
        #   +----------+----------+----------+----------+
        #                         s6 --1-- h6
        #
        # s1 only exits via s1-s2/s1-s6 and s5 only enters via s4-s5/s5-s6:
        # cutting s1-s2 + s1-s6 isolates s1; cutting s1-s2 + s5-s6 leaves
        # s1-s6-s4-s5; the only path fully disjoint from primary is s1-s6-s5.

        cls.DPID = {n: f"00:00:00:00:00:00:00:0{n}" for n in range(1, 7)}

        def intf(switch, port):
            return f"{cls.DPID[switch]}:{port}"

        def link(a, b):
            return {"endpoint_a": {"id": intf(*a)},
                    "endpoint_b": {"id": intf(*b)}}

        cls.UNI_A = intf(1, 1)
        cls.UNI_Z = intf(5, 1)

        # paths between s1 and s5

        cls.PRIMARY_DISJOINT = [
            link((1, 2), (2, 2)),
            link((2, 3), (3, 2)),
            link((3, 3), (4, 2)),
            link((4, 3), (5, 2)),
        ]
        cls.BACKUP_DISJOINT = [
            link((1, 3), (6, 3)),
            link((6, 2), (5, 3)),
        ]

        cls.PRIMARY_SHARED = [
            link((1, 2), (2, 2)),
            link((2, 4), (6, 4)),
            link((6, 2), (5, 3)),
        ]
        # the same backup: what differs is the primary, PRIMARY_SHARED has
        # the s6-s5 link (and s6) in common with it, PRIMARY_DISJOINT nothing
        cls.BACKUP_SHARED = cls.BACKUP_DISJOINT

        # switches only on one of the disjoint paths
        cls.PRIMARY_TRANSIT = (2, 3, 4)
        cls.BACKUP_TRANSIT = (6,)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()


    def _ping_h1_h5(self, vlan: int = 100) -> bool:
        """Ping h1 <-> h5 over a vlan subinterface; return True on 0% loss."""
        h1, h5 = self.net.net.get("h1", "h5")
        for host, addr in ((h1, "100.0.0.1"), (h5, "100.0.0.5")):
            intf = host.intfNames()[0]
            host.cmd(f"ip link add link {intf} name vlan{vlan} "
                     f"type vlan id {vlan}")
            host.cmd(f"ip link set up vlan{vlan}")
            host.cmd(f"ip addr add {addr}/24 dev vlan{vlan}")
        result = h1.cmd("ping -c1 100.0.0.5")
        for host in (h1, h5):
            host.cmd(f"ip link del vlan{vlan}")
        return ", 0% packet loss," in result

    def _assert_only_ingress_moved(self, before: set, after: set,
                                   removed: int = 2, added: int = 2) -> None:
        """Between two flow_keys() snapshots only UNI ingress flows (match on
        the UNI in_port) differ: every NNI and egress flow is identical."""
        gone, new = before - after, after - before
        uni_ports = {self.UNI_A.rsplit(":", 1)[0]: 1,
                     self.UNI_Z.rsplit(":", 1)[0]: 1}
        for dpid, flow in gone | new:
            assert dpid in uni_ports, (dpid, flow)
            assert json.loads(flow)["match"].get("in_port") == \
                uni_ports[dpid], (dpid, flow)
        assert len(gone) == removed and len(new) == added, (gone, new)

    def _static_payload(self, name, primary, backup=None, dynamic=False):
        payload = {
            "name": name,
            "enabled": True,
            "uni_a": {"interface_id": self.UNI_A,
                      "tag": {"tag_type": "vlan", "value": 100}},
            "uni_z": {"interface_id": self.UNI_Z,
                      "tag": {"tag_type": "vlan", "value": 100}},
            "primary_path": primary,
        }
        if backup is not None:
            payload["backup_path"] = backup
        if dynamic:
            payload["dynamic_backup_path"] = True
        return payload

    def test_006_dual_static_preinstalled(self):
        """Standby pre-installed; a primary link down is an ingress swap."""
        evc_id = create_evc(self._static_payload(
            "dual static swap", self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        assert not data["failover_path"], data["failover_path"]
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])

        before = flow_counts(evc_id)
        assert self.DPID[6] in before, before
        flows_before = flow_keys(evc_id)

        nni = (path_endpoint_ids(self.PRIMARY_DISJOINT) |
               path_endpoint_ids(self.BACKUP_DISJOINT))
        avail = available_tags(nni)
        assert self._ping_h1_h5()

        self.net.net.configLinkStatus("s1", "s2", "down")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT)
        assert not data["failover_path"], data["failover_path"]
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])

        after = flow_counts(evc_id)
        assert after.get(self.DPID[6]) == before.get(self.DPID[6]), \
            (before, after)
        self._assert_only_ingress_moved(flows_before, flow_keys(evc_id))
        assert available_tags(nni) == avail
        assert self._ping_h1_h5()

    def test_007_dual_static_full_failure_keeps_flows(self):
        """Both paths down: only the 2 UNI ingress flows are removed."""
        evc_id = create_evc(self._static_payload(
            "dual static full failure",
            self.PRIMARY_SHARED, self.BACKUP_SHARED))

        data = get_evc(evc_id)
        assert data["active"], data
        nni = (path_endpoint_ids(self.PRIMARY_SHARED) |
               path_endpoint_ids(self.BACKUP_SHARED))
        avail = available_tags(nni)
        before = flow_counts(evc_id)
        flows_before = flow_keys(evc_id)

        self.net.net.configLinkStatus("s5", "s6", "down")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["enabled"], data
        assert not data["active"], data
        assert data["current_path"], data
        self._assert_only_ingress_moved(
            flows_before, flow_keys(evc_id), removed=2, added=0)
        assert not self._ping_h1_h5()

        after = flow_counts(evc_id)
        assert sum(after.values()) == sum(before.values()) - 2, (before, after)
        for dpid in set(before) | set(after):
            expected = before.get(dpid, 0)
            if dpid in (self.DPID[1], self.DPID[5]):
                expected -= 1
            assert after.get(dpid, 0) == expected, (dpid, before, after)

        assert available_tags(nni) == avail

    def test_008_dual_static_reactivation_on_link_up(self):
        """A recovering path reactivates ingress-only; primary reverts."""
        evc_id = create_evc(self._static_payload(
            "dual static reactivation",
            self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))

        full = flow_counts(evc_id)
        nni = (path_endpoint_ids(self.PRIMARY_DISJOINT) |
               path_endpoint_ids(self.BACKUP_DISJOINT))
        avail = available_tags(nni)

        self.net.net.configLinkStatus("s1", "s2", "down")
        self.net.net.configLinkStatus("s5", "s6", "down")
        time.sleep(10)

        data = get_evc(evc_id)
        assert not data["active"], data
        assert available_tags(nni) == avail

        self.net.net.configLinkStatus("s5", "s6", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT)
        assert sum(flow_counts(evc_id).values()) == sum(full.values())
        assert available_tags(nni) == avail
        assert self._ping_h1_h5()

        self.net.net.configLinkStatus("s1", "s2", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        assert available_tags(nni) == avail
        assert self._ping_h1_h5()

    def test_009_dual_static_reactivation_prefers_primary(self):
        """Both paths recovering at once reactivates on primary."""
        evc_id = create_evc(self._static_payload(
            "dual static prefer primary",
            self.PRIMARY_SHARED, self.BACKUP_SHARED))

        assert get_evc(evc_id)["active"]

        self.net.net.configLinkStatus("s5", "s6", "down")
        time.sleep(10)
        assert not get_evc(evc_id)["active"]

        self.net.net.configLinkStatus("s5", "s6", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_SHARED)
        assert self._ping_h1_h5()

    def test_010_primary_dynamic_preinstalled_failover(self):
        """Single static + dynamic: pre-installed failover, swap, revert."""
        evc_id = create_evc(self._static_payload(
            "single static + dynamic", self.PRIMARY_DISJOINT, dynamic=True))

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        assert data["failover_path"], data
        assert not (path_endpoint_ids(data["failover_path"]) &
                    path_endpoint_ids(self.PRIMARY_DISJOINT)), data
        assert self._ping_h1_h5()

        self.net.net.configLinkStatus("s1", "s2", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) != \
            path_endpoint_ids(self.PRIMARY_DISJOINT), data
        assert self._ping_h1_h5()

        self.net.net.configLinkStatus("s1", "s2", "up")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT), data
        assert self._ping_h1_h5()

    def test_011_static_fast_revert_ingress_swap(self):
        """Revert is an ingress swap; the old backup stays installed."""
        evc_id = create_evc(self._static_payload(
            "fast revert", self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))
        nni = (path_endpoint_ids(self.PRIMARY_DISJOINT) |
               path_endpoint_ids(self.BACKUP_DISJOINT))
        avail = available_tags(nni)

        self.net.net.configLinkStatus("s1", "s2", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT)
        before = flow_counts(evc_id)
        flows_before = flow_keys(evc_id)
        assert self._ping_h1_h5()

        self.net.net.configLinkStatus("s1", "s2", "up")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)

        after = flow_counts(evc_id)
        assert sum(after.values()) == sum(before.values()), (before, after)
        assert after.get(self.DPID[6]) == before.get(self.DPID[6]), \
            (before, after)
        self._assert_only_ingress_moved(flows_before, flow_keys(evc_id))
        assert available_tags(nni) == avail
        assert self._ping_h1_h5()

    def test_012_reject_primary_equals_backup_on_create(self):
        """Creating an EVC with primary_path == backup_path is rejected."""
        payload = self._static_payload(
            "identical paths", self.PRIMARY_DISJOINT, self.PRIMARY_DISJOINT)
        response = requests.post(
            KYTOS_API + "/mef_eline/v2/evc/", json=payload)
        assert response.status_code == 400, response.text
        assert "must be different" in response.text, response.text

    def test_013_reject_primary_equals_backup_on_update(self):
        """Updating backup_path to equal primary_path is rejected."""
        evc_id = create_evc(self._static_payload(
            "update identical", self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))

        response = requests.patch(
            KYTOS_API + "/mef_eline/v2/evc/" + evc_id,
            json={"backup_path": self.PRIMARY_DISJOINT})
        assert response.status_code == 400, response.text
        assert "must be different" in response.text, response.text

        data = get_evc(evc_id)
        assert path_endpoint_ids(data["backup_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT), data

    def _intf(self, switch: int, port: int) -> str:
        return f"{self.DPID[switch]}:{port}"

    def _transit_counts(self, evc_id: str, switches) -> dict:
        """Installed flow count on the given transit switches only."""
        counts = flow_counts(evc_id)
        return {s: counts.get(self.DPID[s], 0) for s in switches}

    def _uses(self, path: list[dict], switch: int, port: int) -> bool:
        return self._intf(switch, port) in path_endpoint_ids(path)

    def test_014_single_static_link_down_keeps_flows_then_reactivates(self):
        """Link down drops only the ingress; link up reinstalls it."""
        evc_id = create_evc(self._static_payload(
            "single static", self.PRIMARY_DISJOINT))

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        assert not data["failover_path"], data["failover_path"]
        assert not data["backup_path"], data["backup_path"]
        assert_deployed(data["primary_path"])
        before = flow_counts(evc_id)
        nni = path_endpoint_ids(self.PRIMARY_DISJOINT)
        avail = available_tags(nni)
        assert self._ping_h1_h5()

        self.net.net.configLinkStatus("s1", "s2", "down")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["enabled"] and not data["active"], data
        assert data["current_path"], data
        assert_deployed(data["primary_path"])
        after = flow_counts(evc_id)
        assert sum(after.values()) == sum(before.values()) - 2, (before, after)
        for dpid in set(before) | set(after):
            expected = before.get(dpid, 0)
            if dpid in (self.DPID[1], self.DPID[5]):
                expected -= 1
            assert after.get(dpid, 0) == expected, (dpid, before, after)
        assert available_tags(nni) == avail

        self.net.net.configLinkStatus("s1", "s2", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        assert_deployed(data["primary_path"])
        assert flow_counts(evc_id) == before
        assert available_tags(nni) == avail
        assert self._ping_h1_h5()

    def test_015_dual_static_standby_link_down_no_forwarding_impact(self):
        """The standby's own link down touches no flows or vlans."""
        evc_id = create_evc(self._static_payload(
            "dual standby link down",
            self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))

        data = get_evc(evc_id)
        assert data["active"], data
        assert_deployed(data["backup_path"])
        before = flow_counts(evc_id)
        nni = (path_endpoint_ids(self.PRIMARY_DISJOINT) |
               path_endpoint_ids(self.BACKUP_DISJOINT))
        avail = available_tags(nni)

        self.net.net.configLinkStatus("s5", "s6", "down")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        assert_deployed(data["backup_path"])
        assert flow_counts(evc_id) == before
        assert available_tags(nni) == avail
        assert self._ping_h1_h5()

        self.net.net.configLinkStatus("s5", "s6", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        assert flow_counts(evc_id) == before
        assert self._ping_h1_h5()

    def test_016_dual_static_never_deployed_primary_cold_install_revert(self):
        """A never-deployed primary is cold-installed inside the revert."""
        self.net.net.configLinkStatus("s1", "s2", "down")
        time.sleep(10)

        evc_id = create_evc(self._static_payload(
            "cold install", self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT)
        assert_deployed(data["backup_path"])
        assert_not_deployed(data["primary_path"])
        assert self._transit_counts(evc_id, self.PRIMARY_TRANSIT) == \
            {s: 0 for s in self.PRIMARY_TRANSIT}
        assert self._ping_h1_h5()

        self.net.net.configLinkStatus("s1", "s2", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        assert not data["failover_path"], data["failover_path"]
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        assert all(self._transit_counts(evc_id, self.PRIMARY_TRANSIT).values())
        assert self._ping_h1_h5()

    def test_017_single_dynamic_keeps_primary_and_retains_dynamic_on_revert(
        self
    ):
        """Swap keeps primary; revert retains a still-good dynamic."""
        evc_id = create_evc(self._static_payload(
            "single dyn keep", self.PRIMARY_DISJOINT, dynamic=True))

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["failover_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT), data
        assert_deployed(data["primary_path"])
        assert_deployed(data["failover_path"])
        primary_flows = self._transit_counts(evc_id, self.PRIMARY_TRANSIT)
        nni = (path_endpoint_ids(self.PRIMARY_DISJOINT) |
               path_endpoint_ids(self.BACKUP_DISJOINT))
        avail = available_tags(nni)

        self.net.net.configLinkStatus("s1", "s2", "down")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT)
        assert not data["failover_path"], data["failover_path"]
        assert_deployed(data["primary_path"])
        assert self._transit_counts(evc_id, self.PRIMARY_TRANSIT) == \
            primary_flows
        assert available_tags(nni) == avail
        assert self._ping_h1_h5()

        self.net.net.configLinkStatus("s1", "s2", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        assert path_endpoint_ids(data["failover_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT), data
        assert_deployed(data["failover_path"])
        assert available_tags(nni) == avail
        assert self._ping_h1_h5()

    def test_018_single_dynamic_down_failover_cleared_on_primary(self):
        """A down pre-installed failover is cleared; forwarding untouched."""
        evc_id = create_evc(self._static_payload(
            "single dyn down failover",
            self.PRIMARY_DISJOINT, dynamic=True))

        data = get_evc(evc_id)
        assert data["active"], data
        assert self._uses(data["failover_path"], 5, 3), data

        self.net.net.configLinkStatus("s5", "s6", "down")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        assert_deployed(data["primary_path"])
        # cleared or recomputed away from the down link
        assert not self._uses(data["failover_path"], 5, 3), data
        assert self._ping_h1_h5()

    def test_019_single_dynamic_dynamic_fails_cold_escape_keeps_primary(self):
        """An unprotected dynamic failing cold-computes a fresh escape."""
        evc_id = create_evc(self._static_payload(
            "single dyn escape", self.PRIMARY_DISJOINT, dynamic=True))

        self.net.net.configLinkStatus("s1", "s2", "down")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT)
        assert not data["failover_path"], data
        assert_deployed(data["primary_path"])

        self.net.net.configLinkStatus("s5", "s6", "down")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        current = data["current_path"]
        assert current, data
        assert path_endpoint_ids(current) != \
            path_endpoint_ids(self.PRIMARY_DISJOINT), data
        assert path_endpoint_ids(current) != \
            path_endpoint_ids(self.BACKUP_DISJOINT), data
        assert not self._uses(current, 1, 2) and \
            not self._uses(current, 5, 3), data
        assert not data["failover_path"], data
        assert_deployed(data["primary_path"])
        assert self._ping_h1_h5()

    def test_020_single_dynamic_no_escape_deactivates_then_reactivates(self):
        """No escape keeps primary and deactivates; recovery reactivates."""
        baseline = all_available_tags()
        evc_id = create_evc(self._static_payload(
            "single dyn no escape", self.PRIMARY_DISJOINT, dynamic=True))
        primary_flows = self._transit_counts(evc_id, self.PRIMARY_TRANSIT)

        self.net.net.configLinkStatus("s1", "s2", "down")
        self.net.net.configLinkStatus("s1", "s6", "down")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["enabled"] and not data["active"], data
        assert not data["failover_path"], data
        assert_deployed(data["primary_path"])
        assert self._transit_counts(evc_id, self.PRIMARY_TRANSIT) == \
            primary_flows

        self.net.net.configLinkStatus("s1", "s2", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        # down dynamic freed (a fresh failover may hold vlans)
        assert_only_paths_hold_vlans(
            baseline, self.PRIMARY_DISJOINT, data["failover_path"],
            ignore={self.UNI_A, self.UNI_Z})
        assert self._ping_h1_h5()

    def _dual_dynamic_on_escape(self, name: str) -> str:
        """Create a dual static + dynamic EVC and drive it onto an escape."""
        evc_id = create_evc(self._static_payload(
            name, self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT, dynamic=True))
        data = get_evc(evc_id)
        assert data["active"], data
        assert not data["failover_path"], data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])

        self.net.net.configLinkStatus("s1", "s2", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT), data

        self.net.net.configLinkStatus("s5", "s6", "down")
        time.sleep(10)
        return evc_id

    def _assert_on_escape(self, data: dict) -> None:
        """Forwarding on an escape, both statics still installed."""
        assert data["active"], data
        current = data["current_path"]
        assert current, data
        assert path_endpoint_ids(current) != \
            path_endpoint_ids(self.PRIMARY_DISJOINT), data
        assert path_endpoint_ids(current) != \
            path_endpoint_ids(self.BACKUP_DISJOINT), data
        assert not self._uses(current, 1, 2) and \
            not self._uses(current, 5, 3), data
        assert not data["failover_path"], data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])

    def test_021_dual_dynamic_double_failure_escape_keeps_both_statics(self):
        """Both statics down: escape onto a fresh dynamic, keeping both."""
        evc_id = self._dual_dynamic_on_escape("dual dyn escape")
        data = get_evc(evc_id)

        self._assert_on_escape(data)
        assert all(self._transit_counts(evc_id, self.PRIMARY_TRANSIT).values())
        assert all(self._transit_counts(evc_id, self.BACKUP_TRANSIT).values())
        assert self._ping_h1_h5()

    def test_022_dual_dynamic_escape_non_revertive_then_reverts(self):
        """Backup recovering is non-revertive; primary recovering reverts."""
        baseline = all_available_tags()
        evc_id = self._dual_dynamic_on_escape("dual dyn revert")
        data = get_evc(evc_id)
        self._assert_on_escape(data)
        escape = path_endpoint_ids(data["current_path"])

        self.net.net.configLinkStatus("s5", "s6", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == escape, data
        assert self._ping_h1_h5()

        self.net.net.configLinkStatus("s1", "s2", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT), data
        assert not data["failover_path"], data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        # escape freed
        assert_only_paths_hold_vlans(
            baseline, self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT,
            ignore={self.UNI_A, self.UNI_Z})
        assert self._ping_h1_h5()

    def test_023_dual_dynamic_no_escape_deactivates_then_reactivates(self):
        """No escape keeps both statics, deactivates; recovery reactivates."""
        evc_id = create_evc(self._static_payload(
            "dual dyn no escape",
            self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT, dynamic=True))

        self.net.net.configLinkStatus("s1", "s2", "down")
        self.net.net.configLinkStatus("s1", "s6", "down")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["enabled"] and not data["active"], data
        assert not data["failover_path"], data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])

        self.net.net.configLinkStatus("s1", "s2", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        assert self._ping_h1_h5()

    def test_024_dual_dynamic_delete_on_escape_frees_everything(self):
        """Delete on an escape frees every flow and vlan."""
        baseline = all_available_tags()
        evc_id = self._dual_dynamic_on_escape("dual dyn delete")
        self._assert_on_escape(get_evc(evc_id))

        delete_evc(evc_id)

        assert sum(flow_counts(evc_id).values()) == 0, flow_counts(evc_id)
        assert all_available_tags() == baseline

    def _link(self, a, b):
        return {"endpoint_a": {"id": self._intf(*a)},
                "endpoint_b": {"id": self._intf(*b)}}

    def _new_primary(self):
        """s1-s2-s6-s4-s5: fully disjoint from BACKUP_DISJOINT."""
        return [self._link((1, 2), (2, 2)), self._link((2, 4), (6, 4)),
                self._link((6, 6), (4, 4)), self._link((4, 3), (5, 2))]

    def _new_backup(self):
        """s1-s6-s4-s5: distinct from PRIMARY_DISJOINT (shares s4-s5)."""
        return [self._link((1, 3), (6, 3)), self._link((6, 6), (4, 4)),
                self._link((4, 3), (5, 2))]

    def _assert_dual_redeployed(self, evc_id, baseline, primary, backup):
        """Redeployed on primary with backup standby, old paths freed."""
        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(primary), data
        assert path_endpoint_ids(data["primary_path"]) == \
            path_endpoint_ids(primary), data
        assert path_endpoint_ids(data["backup_path"]) == \
            path_endpoint_ids(backup), data
        assert not data["failover_path"], data["failover_path"]
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        assert_only_paths_hold_vlans(
            baseline, primary, backup, ignore={self.UNI_A, self.UNI_Z})
        live = ({self.DPID[1], self.DPID[5]} | path_switch_ids(primary)
                | path_switch_ids(backup))
        orphans = {d: n for d, n in flow_counts(evc_id).items()
                   if n and d not in live}
        assert not orphans, orphans
        assert self._ping_h1_h5()

    def test_025_dual_static_update_primary_path(self):
        """Only primary_path changes: new primary live, backup kept."""
        baseline = all_available_tags()
        evc_id = create_evc(self._static_payload(
            "update primary",
            self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))
        new_primary = self._new_primary()

        update_evc(evc_id, {"primary_path": new_primary})

        self._assert_dual_redeployed(
            evc_id, baseline, new_primary, self.BACKUP_DISJOINT)

    def test_026_dual_static_update_backup_path(self):
        """Only backup_path changes: new backup installed as standby."""
        baseline = all_available_tags()
        evc_id = create_evc(self._static_payload(
            "update backup",
            self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))
        new_backup = self._new_backup()

        update_evc(evc_id, {"backup_path": new_backup})

        self._assert_dual_redeployed(
            evc_id, baseline, self.PRIMARY_DISJOINT, new_backup)

    def test_027_dual_static_update_both_paths(self):
        """Both paths change: new primary live, new backup standby."""
        baseline = all_available_tags()
        evc_id = create_evc(self._static_payload(
            "update both", self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))
        new_primary, new_backup = self.PRIMARY_SHARED, self._new_backup()

        update_evc(evc_id, {
            "primary_path": new_primary, "backup_path": new_backup})

        self._assert_dual_redeployed(evc_id, baseline, new_primary, new_backup)

    def test_028_single_dynamic_link_up_recovers_onto_escape(self):
        """A deactivated EVC whose statics are still down recovers onto a
        cold-computed escape when a link_up makes one reachable."""
        evc_id = create_evc(self._static_payload(
            "link up escape", self.PRIMARY_DISJOINT, dynamic=True))

        # isolate s1, no path at all, the escape finds nothing
        self.net.net.configLinkStatus("s1", "s2", "down")
        self.net.net.configLinkStatus("s1", "s6", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["enabled"] and not data["active"], data
        assert_deployed(data["primary_path"])

        # only s1-s6 returns, primary stays down, but s1-s6-s5 is now usable
        self.net.net.configLinkStatus("s1", "s6", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        current = data["current_path"]
        assert current, data
        assert path_endpoint_ids(current) != \
            path_endpoint_ids(self.PRIMARY_DISJOINT), data
        assert self._uses(current, 1, 3), data
        assert not self._uses(current, 1, 2), data
        assert not data["failover_path"], data
        assert_deployed(data["primary_path"])
        assert self._ping_h1_h5()

    def test_029_dual_static_link_up_installs_missing_standby(self):
        """A standby that could not be installed at deploy time (its links
        were down) is installed when they come back up."""
        self.net.net.configLinkStatus("s5", "s6", "down")
        time.sleep(10)

        evc_id = create_evc(self._static_payload(
            "missing standby", self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        assert_deployed(data["primary_path"])
        assert_not_deployed(data["backup_path"])
        before = flow_counts(evc_id)

        self.net.net.configLinkStatus("s5", "s6", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT)
        assert_deployed(data["backup_path"])
        assert sum(flow_counts(evc_id).values()) > sum(before.values())
        assert self._ping_h1_h5()

    def _restart_keeping_db(self):
        """Restart kytosd without dropping the database."""
        self.net.start_controller(clean_config=False, enable_all=True)
        self.net.wait_switches_connect()
        self.net.wait_kytos_links()
        time.sleep(10)

    def test_030_dual_static_restart_reloads_path_vlans(self):
        """Both configured paths come back from the database still holding
        their s_vlans, so the standby is recognized as installed and a link
        down is still an ingress swap."""
        baseline = all_available_tags()
        evc_id = create_evc(self._static_payload(
            "restart dual static",
            self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))

        data = get_evc(evc_id)
        before_vlans = (path_s_vlans(data["primary_path"]),
                        path_s_vlans(data["backup_path"]))
        before_flows = flow_counts(evc_id)

        self._restart_keeping_db()

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT), data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        assert (path_s_vlans(data["primary_path"]),
                path_s_vlans(data["backup_path"])) == before_vlans, data
        assert flow_counts(evc_id) == before_flows
        assert_only_paths_hold_vlans(
            baseline, self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT,
            ignore={self.UNI_A, self.UNI_Z})
        assert self._ping_h1_h5()

        self.net.net.configLinkStatus("s1", "s2", "down")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT), data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        assert (path_s_vlans(data["primary_path"]),
                path_s_vlans(data["backup_path"])) == before_vlans, data
        assert self._ping_h1_h5()

    def test_031_dual_static_redeploy_after_restart_frees_paths(self):
        """A redeploy of a reloaded EVC tears down both configured paths and
        allocates fresh s_vlans, with nothing left behind."""
        baseline = all_available_tags()
        evc_id = create_evc(self._static_payload(
            "redeploy after restart",
            self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))
        data = get_evc(evc_id)
        before_vlans = (path_s_vlans(data["primary_path"]),
                        path_s_vlans(data["backup_path"]))

        self._restart_keeping_db()
        redeploy_evc(evc_id)

        self._assert_dual_redeployed(
            evc_id, baseline, self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT)
        data = get_evc(evc_id)
        assert (path_s_vlans(data["primary_path"]),
                path_s_vlans(data["backup_path"])) != before_vlans, data

        # the reloaded paths held one tag each, not the old one too
        delete_evc(evc_id)
        assert all_available_tags() == baseline

    def test_032_dual_static_uni_flap_while_inactive_restores_forwarding(
        self
    ):
        """A UNI coming back while the EVC is inactive on its kept paths
        reinstalls its ingress rather than only flipping it back to active."""
        evc_id = create_evc(self._static_payload(
            "uni flap while inactive",
            self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))
        assert self._ping_h1_h5()

        # isolate s1 both configured paths go down
        self.net.net.configLinkStatus("s1", "s2", "down")
        self.net.net.configLinkStatus("s1", "s6", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert not data["active"], data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])

        # the UNI goes down, so the paths coming back can't reactivate it yet
        self.net.net.configLinkStatus("h1", "s1", "down")
        time.sleep(10)
        self.net.net.configLinkStatus("s1", "s2", "up")
        self.net.net.configLinkStatus("s1", "s6", "up")
        time.sleep(10)
        data = get_evc(evc_id)
        assert not data["active"], data

        self.net.net.configLinkStatus("h1", "s1", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        assert self._ping_h1_h5()

    def test_033_dual_dynamic_escape_fails_swaps_to_recovered_backup(self):
        """On an escape with the backup recovered and primary still down, the
        escape swaps onto the kept backup, freeing the escape."""
        baseline = all_available_tags()
        evc_id = self._dual_dynamic_on_escape("dual dyn escape to backup")
        data = get_evc(evc_id)
        self._assert_on_escape(data)

        # with s1-s2 and s5-s6 down the escape is s1-s6-s4-s5, s6-s4 being
        # its only link on neither configured path
        assert self._uses(data["current_path"], 6, 6) and \
            self._uses(data["current_path"], 4, 4), data

        # backup recovers, non-revertive, stays on the escape
        self.net.net.configLinkStatus("s5", "s6", "up")
        time.sleep(10)
        escape = path_endpoint_ids(data["current_path"])
        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == escape, data

        self.net.net.configLinkStatus("s4", "s6", "down")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT), data
        assert not data["failover_path"], data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        # escape freed
        assert_only_paths_hold_vlans(
            baseline, self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT,
            ignore={self.UNI_A, self.UNI_Z})
        assert self._ping_h1_h5()

    def test_034_reject_backup_without_primary_on_create(self):
        """Creating an EVC with a backup_path but no primary_path is
        rejected, even with a dynamic backup."""
        payload = self._static_payload(
            "backup only", self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT,
            dynamic=True)
        del payload["primary_path"]
        response = requests.post(
            KYTOS_API + "/mef_eline/v2/evc/", json=payload)
        assert response.status_code == 400, response.text
        assert "requires a primary_path" in response.text, response.text

    def test_035_reject_removing_primary_keeping_backup_on_update(self):
        """Emptying primary_path of an EVC keeping its backup_path is
        rejected, the EVC is left untouched."""
        evc_id = create_evc(self._static_payload(
            "update backup only",
            self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT, dynamic=True))

        response = requests.patch(
            KYTOS_API + "/mef_eline/v2/evc/" + evc_id,
            json={"primary_path": []})
        assert response.status_code == 400, response.text
        assert "requires a primary_path" in response.text, response.text

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["primary_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT), data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT), data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        assert self._ping_h1_h5()

    def test_036_dual_dynamic_link_up_recovers_onto_escape(self):
        """A deactivated dual static + dynamic EVC whose statics are both
        still down recovers onto a cold-computed escape when a link_up makes
        one reachable, keeping both statics installed."""
        evc_id = self._dual_dynamic_on_escape("dual dyn link up escape")
        self._assert_on_escape(get_evc(evc_id))

        # with s1-s2 and s5-s6 down the escape is s1-s6-s4-s5; cutting s1-s6
        # isolates s1, so the escape dies and no other one is found
        self.net.net.configLinkStatus("s1", "s6", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["enabled"] and not data["active"], data
        assert not data["failover_path"], data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        assert all(self._transit_counts(evc_id, self.PRIMARY_TRANSIT).values())
        assert all(self._transit_counts(evc_id, self.BACKUP_TRANSIT).values())

        # s1-s6 returns, primary (s1-s2) and backup (s5-s6) are still down,
        # but s1-s6-s4-s5 is usable again
        self.net.net.configLinkStatus("s1", "s6", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        self._assert_on_escape(data)
        assert self._uses(data["current_path"], 1, 3), data
        assert all(self._transit_counts(evc_id, self.PRIMARY_TRANSIT).values())
        assert all(self._transit_counts(evc_id, self.BACKUP_TRANSIT).values())
        assert self._ping_h1_h5()

    def test_037_single_dynamic_uni_up_recovers_onto_escape(self):
        """A UNI coming back while the EVC is inactive with its primary still
        down recovers onto a cold-computed escape, keeping primary installed,
        instead of redeploying."""
        evc_id = create_evc(self._static_payload(
            "uni up escape", self.PRIMARY_DISJOINT, dynamic=True))
        assert self._ping_h1_h5()

        # isolate s1 no path at all, the escape finds nothing
        self.net.net.configLinkStatus("s1", "s2", "down")
        self.net.net.configLinkStatus("s1", "s6", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["enabled"] and not data["active"], data
        assert_deployed(data["primary_path"])

        # the UNI goes down, so s1-s6 coming back can't trigger the escape
        self.net.net.configLinkStatus("h1", "s1", "down")
        time.sleep(10)
        self.net.net.configLinkStatus("s1", "s6", "up")
        time.sleep(10)
        data = get_evc(evc_id)
        assert not data["active"], data
        assert_deployed(data["primary_path"])

        self.net.net.configLinkStatus("h1", "s1", "up")
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        current = data["current_path"]
        assert current, data
        assert path_endpoint_ids(current) != \
            path_endpoint_ids(self.PRIMARY_DISJOINT), data
        assert self._uses(current, 1, 3), data
        assert not self._uses(current, 1, 2), data
        assert not data["failover_path"], data
        assert_deployed(data["primary_path"])
        assert self._ping_h1_h5()

    def test_038_dual_static_delete_while_inactive_frees_everything(self):
        """Deleting an EVC inactive on its kept paths (ingress removed)
        frees every flow and vlan."""
        baseline = all_available_tags()
        evc_id = create_evc(self._static_payload(
            "delete while inactive",
            self.PRIMARY_SHARED, self.BACKUP_SHARED))

        self.net.net.configLinkStatus("s5", "s6", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["enabled"] and not data["active"], data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        assert sum(flow_counts(evc_id).values()) > 0

        delete_evc(evc_id)

        assert sum(flow_counts(evc_id).values()) == 0, flow_counts(evc_id)
        assert all_available_tags() == baseline

    def test_039_dual_static_chained_convergence(self):
        """Swap to backup, full failure, then reactivation on primary, back
        to back on one EVC: only the ingress moves at each step."""
        evc_id = create_evc(self._static_payload(
            "chained", self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))
        nni = (path_endpoint_ids(self.PRIMARY_DISJOINT) |
               path_endpoint_ids(self.BACKUP_DISJOINT))
        avail = available_tags(nni)
        on_primary = flow_keys(evc_id)
        assert self._ping_h1_h5()

        # primary down, swap onto backup
        self.net.net.configLinkStatus("s1", "s2", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT), data
        on_backup = flow_keys(evc_id)
        self._assert_only_ingress_moved(on_primary, on_backup)
        assert self._ping_h1_h5()

        # backup down, too full failure, only the ingress is removed
        self.net.net.configLinkStatus("s5", "s6", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["enabled"] and not data["active"], data
        assert data["current_path"], data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        self._assert_only_ingress_moved(
            on_backup, flow_keys(evc_id), removed=2, added=0)
        assert available_tags(nni) == avail

        # primary back, reactivated on it, exactly as first deployed
        self.net.net.configLinkStatus("s1", "s2", "up")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT), data
        assert flow_keys(evc_id) == on_primary
        assert available_tags(nni) == avail
        assert self._ping_h1_h5()

        # backup back, forwarding unchanged
        self.net.net.configLinkStatus("s5", "s6", "up")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT), data
        assert flow_keys(evc_id) == on_primary
        assert available_tags(nni) == avail
        assert self._ping_h1_h5()

    def test_040_dual_static_partially_disjoint_paths(self):
        """Paths sharing a link, a primary-only link down swaps onto the
        backup, the shared link down never swaps onto an affected path."""
        evc_id = create_evc(self._static_payload(
            "partially disjoint", self.PRIMARY_SHARED, self.BACKUP_SHARED))
        on_primary = flow_keys(evc_id)
        assert self._ping_h1_h5()

        # s1-s2 is only on primary, the backup is unaffected
        self.net.net.configLinkStatus("s1", "s2", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.BACKUP_SHARED), data
        self._assert_only_ingress_moved(on_primary, flow_keys(evc_id))
        assert self._ping_h1_h5()

        self.net.net.configLinkStatus("s1", "s2", "up")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_SHARED), data
        assert flow_keys(evc_id) == on_primary

        # s5-s6 is on both, no swap, the ingress is removed
        self.net.net.configLinkStatus("s5", "s6", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["enabled"] and not data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_SHARED), data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        self._assert_only_ingress_moved(
            on_primary, flow_keys(evc_id), removed=2, added=0)

    def test_041_dual_static_repeated_flaps_leak_nothing(self):
        """Quick primary link flaps leave no leaked vlan nor orphaned flow,
        the EVC ends on primary exactly as first deployed."""
        evc_id = create_evc(self._static_payload(
            "flaps", self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))
        baseline = all_available_tags()
        on_primary = flow_keys(evc_id)
        assert self._ping_h1_h5()

        for _ in range(3):
            self.net.net.configLinkStatus("s1", "s2", "down")
            time.sleep(2)
            self.net.net.configLinkStatus("s1", "s2", "up")
            time.sleep(2)
        time.sleep(10)

        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.PRIMARY_DISJOINT), data
        assert not data["failover_path"], data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        assert flow_keys(evc_id) == on_primary
        assert all_available_tags() == baseline
        assert self._ping_h1_h5()

    def test_042_dual_static_disable_frees_paths_then_enable_redeploys(self):
        """Disabling tears down both configured paths, standby included;
        enabling deploys them again."""
        baseline = all_available_tags()
        evc_id = create_evc(self._static_payload(
            "disable enable", self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))
        data = get_evc(evc_id)
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])

        update_evc(evc_id, {"enabled": False})

        data = get_evc(evc_id)
        assert not data["enabled"] and not data["active"], data
        assert not data["current_path"], data
        assert_not_deployed(data["primary_path"])
        assert_not_deployed(data["backup_path"])
        assert sum(flow_counts(evc_id).values()) == 0, flow_counts(evc_id)
        assert_only_paths_hold_vlans(
            baseline, ignore={self.UNI_A, self.UNI_Z})
        assert not self._ping_h1_h5()

        update_evc(evc_id, {"enabled": True})

        self._assert_dual_redeployed(
            evc_id, baseline, self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT)

    def test_043_dual_static_disable_while_inactive_frees_paths(self):
        """Disabling an EVC inactive on its kept paths (ingress removed)
        tears down both configured paths."""
        baseline = all_available_tags()
        evc_id = create_evc(self._static_payload(
            "disable while inactive",
            self.PRIMARY_SHARED, self.BACKUP_SHARED))

        self.net.net.configLinkStatus("s5", "s6", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["enabled"] and not data["active"], data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        assert sum(flow_counts(evc_id).values()) > 0

        update_evc(evc_id, {"enabled": False})

        data = get_evc(evc_id)
        assert not data["enabled"] and not data["active"], data
        assert not data["current_path"], data
        assert_not_deployed(data["primary_path"])
        assert_not_deployed(data["backup_path"])
        assert sum(flow_counts(evc_id).values()) == 0, flow_counts(evc_id)
        assert_only_paths_hold_vlans(
            baseline, ignore={self.UNI_A, self.UNI_Z})

    def test_044_dual_static_redeploy_reinstalls_both_paths(self):
        """An admin redeploy tears down and reinstalls both configured paths:
        primary gets fresh s_vlans, nothing is left behind."""
        baseline = all_available_tags()
        evc_id = create_evc(self._static_payload(
            "redeploy", self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))
        before_vlans = path_s_vlans(get_evc(evc_id)["primary_path"])
        before_flows = flow_keys(evc_id)
        assert self._ping_h1_h5()

        redeploy_evc(evc_id)

        # pings h1 <-> h5 on the redeployed primary too
        self._assert_dual_redeployed(
            evc_id, baseline, self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT)
        assert path_s_vlans(get_evc(evc_id)["primary_path"]) != before_vlans
        after_flows = flow_keys(evc_id)
        assert len(after_flows) == len(before_flows), \
            (before_flows, after_flows)
        assert after_flows != before_flows

        # the reinstalled standby forwards too
        self.net.net.configLinkStatus("s1", "s2", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT), data
        assert self._ping_h1_h5()

    def test_045_dual_static_delete_removes_all_flows(self):
        """Deleting an active dual static EVC removes every flow, the
        pre-installed standby's included, and frees every vlan."""
        baseline = all_available_tags()
        evc_id = create_evc(self._static_payload(
            "delete on primary", self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))
        data = get_evc(evc_id)
        assert data["active"], data
        assert_deployed(data["primary_path"])
        assert_deployed(data["backup_path"])
        assert all(self._transit_counts(evc_id, self.PRIMARY_TRANSIT).values())
        assert all(self._transit_counts(evc_id, self.BACKUP_TRANSIT).values())
        assert self._ping_h1_h5()

        delete_evc(evc_id)

        assert sum(flow_counts(evc_id).values()) == 0, flow_counts(evc_id)
        assert all_available_tags() == baseline
        assert not self._ping_h1_h5()

    def test_046_dual_static_delete_on_backup_removes_all_flows(self):
        """Deleting after a swap, with primary as the kept standby and its
        link still down, removes every flow and frees every vlan."""
        baseline = all_available_tags()
        evc_id = create_evc(self._static_payload(
            "delete on backup", self.PRIMARY_DISJOINT, self.BACKUP_DISJOINT))

        self.net.net.configLinkStatus("s1", "s2", "down")
        time.sleep(10)
        data = get_evc(evc_id)
        assert data["active"], data
        assert path_endpoint_ids(data["current_path"]) == \
            path_endpoint_ids(self.BACKUP_DISJOINT), data
        assert_deployed(data["primary_path"])
        assert all(self._transit_counts(evc_id, self.PRIMARY_TRANSIT).values())
        assert self._ping_h1_h5()

        delete_evc(evc_id)

        assert sum(flow_counts(evc_id).values()) == 0, flow_counts(evc_id)
        assert all_available_tags() == baseline
        assert not self._ping_h1_h5()
