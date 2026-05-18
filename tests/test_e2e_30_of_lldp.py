import requests
from tests.helpers import NetworkTest
import time
import pytest
import os

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER


class TestE2EOfLLDP:
    net = None

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start()
        cls.net.restart_kytos_clean()
        cls.net.wait_switches_connect()
        # disable ipv6 router solicitation to avoid interfere with stats
        for host in cls.net.net.hosts:
            host.cmd("sysctl net.ipv6.conf.all.accept_ra=0")
            host.cmd("sysctl net.ipv6.conf.default.accept_ra=0")
        time.sleep(10)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def get_iface_stats_rx_pkt(self, host):
        rx_pkts = host.cmd("ip -s link show dev %s | grep RX: -A 1 | tail -n1 | awk '{print $2}'" % (host.intfNames()[0]))
        return int(rx_pkts.strip())

    def enable_all_interfaces(self):
        api_url = KYTOS_API + '/topology/v3/switches/'
        response = requests.get(api_url)
        data = response.json()
        switches = data.get("switches", {})
        for sw in switches.keys():
            response = requests.post(KYTOS_API + '/topology/v3/switches/%s/enable' % sw)
            response = requests.post(KYTOS_API + '/topology/v3/interfaces/switch/%s/enable' % sw)

    @staticmethod
    def disable_all_of_lldp():
        api_url = KYTOS_API + '/of_lldp/v1/interfaces/'
        response = requests.get(api_url)
        data = response.json()
        all_interfaces = data.get("interfaces", [])
        response = requests.post(api_url+'disable/', json={"interfaces": all_interfaces})
        assert response.status_code == 200, response.text

    def test_001_list_interfaces_with_lldp(self):
        """ List interfaces with OF LLDP. """
        api_url = KYTOS_API + '/of_lldp/v1/interfaces/'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "interfaces" in data
        # the number of interfaces should match the topology + the OFP_LOCAL port, for the RingTopology it means:
        # mininet> net
        # ...
        # s1 lo:  s1-eth1:h11-eth0 s1-eth2:h12-eth0 s1-eth3:s2-eth2 s1-eth4:s3-eth3
        # s2 lo:  s2-eth1:h2-eth0 s2-eth2:s1-eth3 s2-eth3:s3-eth2
        # s3 lo:  s3-eth1:h3-eth0 s3-eth2:s2-eth3 s3-eth3:s1-eth4
        expected_interfaces = set([
                "00:00:00:00:00:00:00:01:1", "00:00:00:00:00:00:00:01:2", "00:00:00:00:00:00:00:01:3",
                "00:00:00:00:00:00:00:01:4", "00:00:00:00:00:00:00:01:4294967294",
                "00:00:00:00:00:00:00:02:1", "00:00:00:00:00:00:00:02:2", "00:00:00:00:00:00:00:02:3",
                "00:00:00:00:00:00:00:02:4294967294",
                "00:00:00:00:00:00:00:03:1", "00:00:00:00:00:00:00:03:2", "00:00:00:00:00:00:00:03:3",
                "00:00:00:00:00:00:00:03:4294967294"
        ])
        # some switches have more interfaces than the ones configured by
        # mininet (ex: Noviflow switches, P4OfSwitch)
        for sw_name in ["s1", "s2", "s3"]:
            sw = self.net.net.get(sw_name)
            if hasattr(sw, "get_all_of_ports"):
                ports = sw.get_all_of_ports()
                dpid = ":".join([sw.dpid[i:i+2] for i in range(0, 16, 2)])
                intf_ids = [f"{dpid}:{port}" for port in ports]
                expected_interfaces.update(intf_ids)

        assert set(data["interfaces"]) == set(expected_interfaces)

        # make sure the interfaces are actually receiving LLDP
        h11, h12, h2, h3 = self.net.net.get('h11', 'h12', 'h2', 'h3')
        rx_stats_h11 = self.get_iface_stats_rx_pkt(h11)
        rx_stats_h12 = self.get_iface_stats_rx_pkt(h12)
        rx_stats_h2 = self.get_iface_stats_rx_pkt(h2)
        rx_stats_h3 = self.get_iface_stats_rx_pkt(h3)
        time.sleep(10)
        rx_stats_h11_2 = self.get_iface_stats_rx_pkt(h11)
        rx_stats_h12_2 = self.get_iface_stats_rx_pkt(h12)
        rx_stats_h2_2 = self.get_iface_stats_rx_pkt(h2)
        rx_stats_h3_2 = self.get_iface_stats_rx_pkt(h3)

        assert rx_stats_h11_2 > rx_stats_h11 \
            and rx_stats_h12_2 > rx_stats_h12 \
            and rx_stats_h2_2 > rx_stats_h2 \
            and rx_stats_h3_2 > rx_stats_h3

    def test_010_disable_of_lldp(self):
        """ Test if the disabling OF LLDP in an interface worked properly. """
        self.net.start_controller(clean_config=True, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(5)
        self.enable_all_interfaces()

        # disabling all the UNI interfaces
        payload = {
            "interfaces": [
                "00:00:00:00:00:00:00:01:1", "00:00:00:00:00:00:00:01:2", "00:00:00:00:00:00:00:01:4294967294",
                "00:00:00:00:00:00:00:02:1", "00:00:00:00:00:00:00:02:4294967294",
                "00:00:00:00:00:00:00:03:1", "00:00:00:00:00:00:00:03:4294967294"
            ]
        }
        expected_interfaces = [
                "00:00:00:00:00:00:00:01:3", "00:00:00:00:00:00:00:01:4",
                "00:00:00:00:00:00:00:02:2", "00:00:00:00:00:00:00:02:3",
                "00:00:00:00:00:00:00:03:2", "00:00:00:00:00:00:00:03:3"
        ]
        # some switches have more interfaces than the ones configured by
        # mininet (ex: Noviflow switches, P4OfSwitch)
        extra_intfs = []
        for sw_name in ["s1", "s2", "s3"]:
            sw = self.net.net.get(sw_name)
            if hasattr(sw, "get_all_of_ports"):
                ports = sw.get_all_of_ports()
                dpid = ":".join([sw.dpid[i:i+2] for i in range(0, 16, 2)])
                for port in ports:
                    intf_id = f"{dpid}:{port}"
                    if intf_id in expected_interfaces:
                        continue
                    if intf_id in payload["interfaces"]:
                        continue
                    extra_intfs.append(intf_id)
        payload["interfaces"].extend(extra_intfs)

        api_url = KYTOS_API + '/of_lldp/v1/interfaces/disable/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 200, response.text

        api_url = KYTOS_API + '/of_lldp/v1/interfaces/'
        response = requests.get(api_url)
        data = response.json()
        assert set(data["interfaces"]) == set(expected_interfaces)

        # wait LLDP message that were being sent before disabling
        time.sleep(5)

        h11, h12, h2, h3 = self.net.net.get('h11', 'h12', 'h2', 'h3')
        rx_stats_h11 = self.get_iface_stats_rx_pkt(h11)
        rx_stats_h12 = self.get_iface_stats_rx_pkt(h12)
        rx_stats_h2 = self.get_iface_stats_rx_pkt(h2)
        rx_stats_h3 = self.get_iface_stats_rx_pkt(h3)
        time.sleep(15)
        rx_stats_h11_2 = self.get_iface_stats_rx_pkt(h11)
        rx_stats_h12_2 = self.get_iface_stats_rx_pkt(h12)
        rx_stats_h2_2 = self.get_iface_stats_rx_pkt(h2)
        rx_stats_h3_2 = self.get_iface_stats_rx_pkt(h3)

        assert rx_stats_h11_2 == rx_stats_h11
        assert rx_stats_h12_2 == rx_stats_h12
        assert rx_stats_h2_2 == rx_stats_h2
        assert rx_stats_h3_2 == rx_stats_h3

        # restart kytos and check if lldp remains disabled
        self.net.start_controller(clean_config=False, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(10)

        api_url = KYTOS_API + '/of_lldp/v1/interfaces/'
        response = requests.get(api_url)
        data = response.json()
        assert set(data["interfaces"]) == set(expected_interfaces)

    def test_020_enable_of_lldp(self):
        """ Test if enabling OF LLDP in an interface works properly. """
        self.net.start_controller(clean_config=True, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(5)
        self.enable_all_interfaces()
        TestE2EOfLLDP.disable_all_of_lldp()

        payload = {
            "interfaces": [
                "00:00:00:00:00:00:00:01:1"
            ]
        }
        expected_interfaces = [
                "00:00:00:00:00:00:00:01:1"
        ]

        api_url = KYTOS_API + '/of_lldp/v1/interfaces/enable/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 200, response.text

        api_url = KYTOS_API + '/of_lldp/v1/interfaces/'
        response = requests.get(api_url)
        data = response.json()
        assert set(data["interfaces"]) == set(expected_interfaces)

        h11 = self.net.net.get('h11')
        rx_stats_h11 = self.get_iface_stats_rx_pkt(h11)
        time.sleep(10)
        rx_stats_h11_2 = self.get_iface_stats_rx_pkt(h11)

        assert rx_stats_h11_2 > rx_stats_h11

        # restart kytos and check if lldp remains disabled
        self.net.start_controller(clean_config=False, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(10)

        api_url = KYTOS_API + '/of_lldp/v1/interfaces/'
        response = requests.get(api_url)
        data = response.json()
        assert set(data["interfaces"]) == set(expected_interfaces)

    def test_030_change_polling_interval(self):
        """ Test if changing the polling interval works works properly. """
        self.net.restart_kytos_clean()
        time.sleep(5)

        default_polling_time = 3
        api_url = KYTOS_API + '/of_lldp/v1/polling_time'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "polling_time" in data
        assert data["polling_time"] == default_polling_time

        h11 = self.net.net.get('h11')
        rx_stats_h11 = self.get_iface_stats_rx_pkt(h11)
        lldp_wait = 31
        time.sleep(lldp_wait)
        rx_stats_h11_2 = self.get_iface_stats_rx_pkt(h11)

        # the delta pps should be around 10, because the interface is every 3s
        delta_pps = rx_stats_h11_2 - rx_stats_h11

        api_url = KYTOS_API + '/of_lldp/v1/polling_time'
        response = requests.post(api_url, json={"polling_time": 1})
        assert response.status_code == 200, response.text

        response = requests.get(api_url)
        data = response.json()
        assert data["polling_time"] == 1

        # wait a few seconds to let the last polling time schedule finish
        time.sleep(default_polling_time)

        rx_stats_h11 = self.get_iface_stats_rx_pkt(h11)
        time.sleep(lldp_wait)
        rx_stats_h11_2 = self.get_iface_stats_rx_pkt(h11)

        delta_pps_2 = rx_stats_h11_2 - rx_stats_h11

        # the delta pps now should be around 30, because the interval is every 1s
        assert delta_pps_2 > 2*delta_pps

    @pytest.mark.skipif(
        os.environ.get("SWITCH_CLASS") in ("NoviSwitch", "P4OfSwitch"),
        reason="NoviSwitch/P4OfSwitch already include all interface when connecting",
    )
    def test_040_new_interface_allocated_lldp_vlan(self):
        """New interface hot-added to ring topology must be picked up by of_lldp
        within 5s, with VLAN 3799 absent from available_tags (reserved by of_lldp)."""
        self.net.restart_kytos_clean()
        time.sleep(5)

        new_intf_s1 = "00:00:00:00:00:00:00:01:5"
        new_intf_s2 = "00:00:00:00:00:00:00:02:4"

        api_url = KYTOS_API + "/of_lldp/v1/interfaces/"
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        intfs_before = set(response.json()["interfaces"])
        assert new_intf_s1 not in intfs_before
        assert new_intf_s2 not in intfs_before

        # Add a new link between s1 (port 5) and s2 (port 4)
        S1, S2 = self.net.net.get("s1", "s2")
        self.net.net.addLink(S1, S2, port1=5, port2=4)
        S1.attach("s1-eth5")
        S2.attach("s2-eth4")
        try:
            time.sleep(5)

            response = requests.get(api_url)
            assert response.status_code == 200, response.text
            intfs_after = set(response.json()["interfaces"])
            assert new_intf_s1 in intfs_after, f"{intfs_after}"
            assert new_intf_s2 in intfs_after, f"{intfs_after}"

            # Assert VLAN 3799 is NOT in available_tags
            expected_available = [[1, 3798], [3800, 4094]]
            topo_url = KYTOS_API + "/topology/v3/interfaces/tag_ranges"
            response = requests.get(topo_url)
            assert response.status_code == 200, response.text
            data = response.json()
            for intf_id in (new_intf_s1, new_intf_s2):
                actual = data[intf_id]["available_tags"]["vlan"]
                assert actual == expected_available, f"{intf_id} available_tags: {actual}"
        finally:
            S1.detach("s1-eth5")
            S2.detach("s2-eth4")
            for link in self.net.net.linksBetween(S1, S2):
                if link.intf1.name == "s1-eth5" or link.intf2.name == "s1-eth5":
                    self.net.net.delLink(link)
                    break

    def test_041_lldp_flow_installed_after_switch_enable(self):
        """of_lldp must not install the VLAN 3799 flow while switches are
        disabled on every interface that exposes tag ranges."""
        self.net.start_controller(clean_config=True, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(10)

        api_url = KYTOS_API + '/topology/v3/switches'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        switches = response.json().get("switches", {})
        assert switches, "no switches found in topology"
        dpids = list(switches.keys())
        for dpid in dpids:
            assert switches[dpid]["enabled"] is False, dpid

        def has_lldp_flow(dpid):
            stored_url = f"{KYTOS_API}/flow_manager/v2/stored_flows/?dpid={dpid}"
            stored = requests.get(stored_url)
            assert stored.status_code == 200, stored.text
            flows = stored.json().get(dpid, [])
            return any(
                f["flow"].get("match", {}).get("dl_vlan") == 3799
                for f in flows
            )

        for dpid in dpids:
            assert not has_lldp_flow(dpid), f"unexpected LLDP flow on {dpid}"

        self.enable_all_interfaces()
        time.sleep(10)

        for dpid in dpids:
            assert has_lldp_flow(dpid), f"missing LLDP flow on {dpid}"

        tags_url = KYTOS_API + "/topology/v3/interfaces/tag_ranges"
        response = requests.get(tags_url)
        assert response.status_code == 200, response.text
        data = response.json()
        for intf_id, intf_data in data.items():
            available = intf_data.get("available_tags", {}).get("vlan")
            if not available:
                continue
            for start, end in available:
                assert not (start <= 3799 <= end), (
                    f"{intf_id} still has VLAN 3799 in available_tags: {available}"
                )
