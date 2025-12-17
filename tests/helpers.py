from mininet.net import Mininet
from mininet.topo import Topo, LinearTopo
from mininet.node import RemoteController, OVSSwitch
import mininet.clean
from mock import patch
import time
import os
import requests
import hashlib

from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

from tests.noviswitch import NoviSwitch

BASE_ENV = os.environ.get('VIRTUAL_ENV', None) or '/'

def dpctl_wrapper(obj, *args):
    if args[0] == "dump-flows":
        return obj.orig_dpctl(*args, "--no-names", "--protocols=OpenFlow13", "|grep -v OFPST_FLOW")
    return obj.orig_dpctl(*args)

NoviSwitch.orig_dpctl = NoviSwitch.dpctl
NoviSwitch.dpctl = dpctl_wrapper
OVSSwitch.orig_dpctl = OVSSwitch.dpctl
OVSSwitch.dpctl = dpctl_wrapper

class SwitchFactory:
    def __new__(cls, *args, **kwargs):
        cls_name = os.environ.get('SWITCH_CLASS')
        if cls_name == "NoviSwitch" and NoviSwitch.is_available():
            return NoviSwitch(*args, **kwargs)
        return OVSSwitch(*args, **kwargs)


class AmlightTopo(Topo):
    """Amlight Topology."""
    def build(self):
        # Add switches
        self.Ampath1 = self.addSwitch('Ampath1', listenPort=6601, dpid='0000000000000011')
        self.Ampath2 = self.addSwitch('Ampath2', listenPort=6602, dpid='0000000000000012')
        SouthernLight2 = self.addSwitch('SoL2', listenPort=6603, dpid='0000000000000013')
        SanJuan = self.addSwitch('SanJuan', listenPort=6604, dpid='0000000000000014')
        AndesLight2 = self.addSwitch('AL2', listenPort=6605, dpid='0000000000000015')
        AndesLight3 = self.addSwitch('AL3', listenPort=6606, dpid='0000000000000016')
        self.Ampath3 = self.addSwitch('Ampath3', listenPort=6608, dpid='0000000000000017')
        self.Ampath4 = self.addSwitch('Ampath4', listenPort=6609, dpid='0000000000000018')
        self.Ampath5 = self.addSwitch('Ampath5', listenPort=6610, dpid='0000000000000019')
        self.Ampath7 = self.addSwitch('Ampath7', listenPort=6611, dpid='0000000000000020')
        JAX1 = self.addSwitch('JAX1', listenPort=6612, dpid='0000000000000021')
        JAX2 = self.addSwitch('JAX2', listenPort=6613, dpid='0000000000000022')
        # add hosts
        h1 = self.addHost('h1', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', mac='00:00:00:00:00:02')
        h3 = self.addHost('h3', mac='00:00:00:00:00:03')
        h4 = self.addHost('h4', mac='00:00:00:00:00:04')
        h5 = self.addHost('h5', mac='00:00:00:00:00:05')
        h6 = self.addHost('h6', mac='00:00:00:00:00:06')
        h7 = self.addHost('h7', mac='00:00:00:00:00:07')
        h8 = self.addHost('h8', mac='00:00:00:00:00:08')
        h9 = self.addHost('h9', mac='00:00:00:00:00:09')
        h10 = self.addHost('h10', mac='00:00:00:00:00:0A')
        h11 = self.addHost('h11', mac='00:00:00:00:00:0B')
        h12 = self.addHost('h12', mac='00:00:00:00:00:0C')
        h13 = self.addHost('h13', mac='00:00:00:00:00:0D')
        h14 = self.addHost('h14', mac='00:00:00:00:00:0E')
        h15 = self.addHost('h15', mac='00:00:00:00:00:0F')
        # Add links
        self.addLink(self.Ampath1, self.Ampath2, port1=1, port2=1)
        self.addLink(self.Ampath1, self.Ampath3, port1=2, port2=2)
        self.addLink(self.Ampath1, self.Ampath4, port1=3, port2=3)
        self.addLink(self.Ampath1, SouthernLight2, port1=4, port2=4)
        self.addLink(self.Ampath1, SouthernLight2, port1=5, port2=5)
        self.addLink(self.Ampath2, self.Ampath3, port1=3, port2=3)
        self.addLink(self.Ampath2, self.Ampath5, port1=4, port2=4)
        self.addLink(self.Ampath2, AndesLight2, port1=5, port2=5)
        self.addLink(self.Ampath2, SanJuan, port1=6, port2=6)
        self.addLink(self.Ampath4, self.Ampath5, port1=1, port2=1)
        self.addLink(self.Ampath4, self.Ampath7, port1=2, port2=2)
        self.addLink(self.Ampath4, JAX1, port1=4, port2=4)
        self.addLink(self.Ampath5, JAX2, port1=5, port2=5)
        self.addLink(self.Ampath7, SouthernLight2, port1=1, port2=1)
        self.addLink(SouthernLight2, AndesLight3, port1=2, port2=2)
        self.addLink(AndesLight3, AndesLight2, port1=1, port2=1)
        self.addLink(AndesLight2, SanJuan, port1=3, port2=3)
        self.addLink(JAX1, JAX2, port1=1, port2=1)
        self.addLink(h1, self.Ampath1, port1=1, port2=16)
        self.addLink(h2, self.Ampath2, port1=1, port2=16)
        self.addLink(h3, SouthernLight2, port1=1, port2=16)
        self.addLink(h4, SanJuan, port1=1, port2=16)
        self.addLink(h5, AndesLight2, port1=1, port2=16)
        self.addLink(h6, AndesLight3, port1=1, port2=16)
        self.addLink(h7, self.Ampath3, port1=1, port2=16)
        self.addLink(h8, self.Ampath4, port1=1, port2=16)
        self.addLink(h9, self.Ampath5, port1=1, port2=16)
        self.addLink(h10, self.Ampath7, port1=1, port2=16)
        self.addLink(h11, JAX1, port1=1, port2=16)
        self.addLink(h12, JAX2, port1=1, port2=16)
        self.addLink(h13, self.Ampath1, port1=1, port2=15)
        self.addLink(h14, self.Ampath2, port1=1, port2=15)
        self.addLink(h15, AndesLight2, port1=1, port2=15)


class AmlightLoopedTopo(AmlightTopo):
    """Amlight Topology with loops."""
    def build(self):
        super().build()
        #Add loops
        self.addLink(self.Ampath1, self.Ampath1, port1=10, port2=11)
        self.addLink(self.Ampath1, self.Ampath1, port1=12, port2=13)
        self.addLink(self.Ampath4, self.Ampath4, port1=10, port2=11)
        self.addLink(self.Ampath4, self.Ampath4, port1=12, port2=13)


class RingTopo(Topo):
    """Ring topology with three switches
    and one host connected to each switch"""

    def build(self):
        # Create two hosts
        h11 = self.addHost('h11', ip='0.0.0.0')
        h12 = self.addHost('h12', ip='0.0.0.0')
        h2 = self.addHost('h2', ip='0.0.0.0')
        h3 = self.addHost('h3', ip='0.0.0.0')

        # Create the switches
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')

        # Add links between the switch and each host
        self.addLink(s1, h11)
        self.addLink(s1, h12)
        self.addLink(s2, h2)
        self.addLink(s3, h3)

        # Add links between the switches
        self.addLink(s1, s2)
        self.addLink(s2, s3)
        self.addLink(s3, s1)


class Ring4Topo(Topo):
    """Create a network from semi-scratch with multiple controllers."""

    def build(self):
        # ("*** Creating switches\n")
        s1 = self.addSwitch('s1', listenPort=6601, dpid="1")
        s2 = self.addSwitch('s2', listenPort=6602, dpid="2")
        s3 = self.addSwitch('s3', listenPort=6603, dpid="3")
        s4 = self.addSwitch('s4', listenPort=6604, dpid="4")

        # ("*** Creating hosts\n")
        hosts1 = [self.addHost('h%d' % n) for n in (1, 2)]
        hosts2 = [self.addHost('h%d' % n) for n in (3, 4)]
        hosts3 = [self.addHost('h%d' % n) for n in (5, 6)]
        hosts4 = [self.addHost('h%d' % n) for n in (7, 8)]

        # ("*** Creating links\n")
        for h in hosts1:
            self.addLink(s1, h)
        for h in hosts2:
            self.addLink(s2, h)

        self.addLink(s1, s2)
        self.addLink(s2, s3)

        for h in hosts3:
            self.addLink(s3, h)
        for h in hosts4:
            self.addLink(s4, h)

        self.addLink(s3, s4)
        self.addLink(s4, s1)

class Looped(Topo):
    """ Network with two switches
    and a loop in one switch."""

    def build(self):
        "Create custom topo."

        s1 = self.addSwitch("s1")
        s2 = self.addSwitch("s2")

        self.addLink(s1, s1, port1=1, port2=2)
        self.addLink(s1, s1, port1=4, port2=5)
        self.addLink(s1, s2, port1=3, port2=1)

class MultiConnectedTopo(Topo):
    """Multiply connected network topology six
    and one host connected to each switch """
    def build(self):
        # Create hosts
        h1 = self.addHost('h1', ip='0.0.0.0')
        h2 = self.addHost('h2', ip='0.0.0.0')
        h3 = self.addHost('h3', ip='0.0.0.0')
        h4 = self.addHost('h4', ip='0.0.0.0')
        h5 = self.addHost('h5', ip='0.0.0.0')
        h6 = self.addHost('h6', ip='0.0.0.0')
        # Create the switches
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')
        s4 = self.addSwitch('s4')
        s5 = self.addSwitch('s5')
        s6 = self.addSwitch('s6')
        # Add links between the switch and each host
        self.addLink(s1, h1)
        self.addLink(s2, h2)
        self.addLink(s3, h3)
        self.addLink(s4, h4)
        self.addLink(s5, h5)
        self.addLink(s6, h6)
        # Add links between the switches
        self.addLink(s1, s2)
        self.addLink(s2, s3)
        self.addLink(s3, s4)
        self.addLink(s4, s5)
        self.addLink(s5, s6)
        self.addLink(s1, s6)
        self.addLink(s2, s6)
        self.addLink(s3, s6)
        self.addLink(s4, s6)


# You can run any of the topologies above by doing:
# mn --custom tests/helpers.py --topo ring --controller=remote,ip=127.0.0.1
topos = {
    'ring': (lambda: RingTopo()),
    'ring4': (lambda: Ring4Topo()),
    'amlight': (lambda: AmlightTopo()),
    'amlight_looped': (lambda: AmlightLoopedTopo()),
    'linear10': (lambda: LinearTopo(10)),
    'multi': (lambda: MultiConnectedTopo()),
    'looped': (lambda: Looped()),
}


def mongo_client(
    host_seeds=os.environ.get("MONGO_HOST_SEEDS"),
    username=os.environ.get("MONGO_USERNAME"),
    password=os.environ.get("MONGO_PASSWORD"),
    database=os.environ.get("MONGO_DBNAME"),
    connect=False,
    retrywrites=True,
    retryreads=True,
    readpreference='primaryPreferred',
    maxpoolsize=int(os.environ.get("MONGO_MAX_POOLSIZE", 20)),
    minpoolsize=int(os.environ.get("MONGO_MIN_POOLSIZE", 10)),
    **kwargs,
) -> MongoClient:
    """mongo_client."""
    return MongoClient(
        host_seeds.split(","),
        username=username,
        password=password,
        connect=False,
        authsource=database,
        retrywrites=retrywrites,
        retryreads=retryreads,
        readpreference=readpreference,
        maxpoolsize=maxpoolsize,
        minpoolsize=minpoolsize,
        **kwargs,
    )


class NetworkTest:
    def __init__(
        self,
        controller_ip,
        topo_name="ring",
        db_client=mongo_client,
        db_client_options=None,
    ):
        # Create an instance of our topology
        mininet.clean.cleanup()

        # Create a network based on the topology using
        # OVS and controlled by a remote controller
        patch('mininet.util.fixLimits', side_effect=None)
        self.net = Mininet(
            topo=topos.get(topo_name, (lambda: RingTopo()))(),
            controller=lambda name: RemoteController(
                name, ip=controller_ip, port=6653),
            switch=SwitchFactory,
            autoSetMacs=True)
        db_client_kwargs = db_client_options or {}
        db_name = db_client_kwargs.get("database") or os.environ.get("MONGO_DBNAME")
        self.db_client = db_client(**db_client_kwargs)
        self.db_name = db_name
        self.db = self.db_client[self.db_name]
        # setup a wrapper for configLinkStatus
        self.net.orig_configLinkStatus = self.net.configLinkStatus
        self.net.configLinkStatus = self.configLinkStatus

    def start(self):
        self.net.start()
        self.start_controller(clean_config=True)

    def drop_database(self):
        """Drop database."""
        self.db_client.drop_database(self.db_name)

    def stop_kytosd(self):
        """Stop kytosd process."""
        try:
            os.system('pkill kytosd')
            time.sleep(5)
            pid_path = os.path.join(BASE_ENV, 'var/run/kytos/kytosd.pid')
            if os.path.exists(pid_path):
                raise Exception("kytos pid still exists.")
        except Exception as e:
            print(f"FAIL to stop kytos after 5 seconds -- {e}. Force stop!")
            os.system('pkill -9 kytosd')
            os.system(f"rm -f {pid_path}")

    def start_controller(self, clean_config=False, enable_all=False,
                         del_flows=False, port=None, database='mongodb',
                         extra_args=os.environ.get("KYTOSD_EXTRA_ARGS", "")):
        # Restart kytos and check if the napp is still disabled
        try:
            os.system('pkill kytosd')
            # with open('/var/run/kytos/kytosd.pid', "r") as f:
            #    pid = int(f.read())
            #    os.kill(pid, signal.SIGTERM)
            time.sleep(5)
            pid_path = os.path.join(BASE_ENV, 'var/run/kytos/kytosd.pid')
            if os.path.exists(pid_path):
                raise Exception("Kytos pid still exists.")
        except Exception as e:
            print("FAIL to stop kytos after 5 seconds -- %s. Force stop!" % e)
            os.system('pkill -9 kytosd')
            os.system(f'rm -f {pid_path}')

        if clean_config and database:
            try:
                self.drop_database()
            except ServerSelectionTimeoutError as exc:
                print(f"FAIL to drop database. {str(exc)}")

        if clean_config or del_flows:
            # Remove any installed flow
            for sw in self.net.switches:
                sw.dpctl('del-flows')

        daemon = 'kytosd'
        if database:
            daemon += f' --database {database}'
        if port:
            daemon += ' --port %s' % port
        if enable_all:
            daemon += ' -E'
        if extra_args:
            daemon += ' ' + extra_args
        os.system(daemon)

        self.wait_controller_start()

        # make sure switches will reconnect
        self.reconnect_switches(wait=False)

    def wait_controller_start(self):
        """Wait until controller starts according to core/status API."""
        wait_count = 0
        last_error = ""
        while wait_count < 60:
            try:
                response = requests.get('http://127.0.0.1:8181/api/kytos/core/status/', timeout=3)
                assert response.json()['response'] == 'running', response.text
                break
            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.5)
                wait_count += 0.5
        else:
            msg = f"Timeout while starting Kytos controller. Last error: {last_error}"
            raise Exception(msg)

    def wait_switches_connect(self):
        max_wait = 0
        while any(not sw.connected() for sw in self.net.switches):
            time.sleep(1)
            max_wait += 1
            if max_wait > 30:
                status = [(sw.name, sw.connected()) for sw in self.net.switches]
                raise Exception('Timeout: timed out waiting switches reconnect. Status %s' % status)

    def wait_kytos_links(self):
        wait_count = 0
        last_error = ""
        topo_links = []
        for link in self.net.links:
            if link.intf1.node in self.net.switches and link.intf2.node in self.net.switches:
                link_id = self.create_link_id(link)
                if link_id:
                    topo_links.append(link_id)
        while wait_count < 30:
            try:
                response = requests.get("http://127.0.0.1:8181/api/kytos/topology/v3/links/", timeout=3)
                links = response.json()["links"]
                assert len(topo_links) == len(links), f"{topo_links=} {links=}"
                assert all(link_id in links for link_id in topo_links), f"{topo_links=} {links=}"
                break
            except Exception as exc:
                last_error = str(exc)
            time.sleep(1)
            wait_count += 1
        else:
            msg = f"Timeout waiting for links. Last error: {last_error}"
            raise Exception(msg)

    def create_link_id(self, link):
        dpid1 = link.intf1.node.dpid
        dpid2 = link.intf2.node.dpid
        dpid1 = ":".join(dpid1[i:i+2] for i in range(0, len(dpid1), 2))
        dpid2 = ":".join(dpid2[i:i+2] for i in range(0, len(dpid2), 2))
        port1 = link.intf1.node.ports.get(link.intf1)
        port2 = link.intf2.node.ports.get(link.intf2)
        if not port1 or not port2:
            return
        intf1 = f"{dpid1}:{port1}"
        intf2 = f"{dpid2}:{port2}"
        if dpid1 == dpid2:
            port1, port2 = sorted((port1, port2))
            raw_str = f"{dpid1}:{port1}:{dpid2}:{port2}"
        else:
            raw_str = ":".join(sorted((intf1, intf2)))
        return hashlib.sha256(raw_str.encode('utf-8')).hexdigest()

    def restart_kytos_clean(self):
        self.start_controller(clean_config=True, enable_all=True)
        self.wait_switches_connect()
        self.wait_kytos_links()

    def reconnect_switches(self, target="tcp:127.0.0.1:6653",
                           temp_target="tcp:127.0.0.1:6654", wait=True):
        """Restart switches connections.
        This method can also be used to trigger a consistency check initial run.

        A temporary target is used in order to avoid OvS deleting the flows
        if the controller config were to be deleted.
        """
        for sw in self.net.switches:
            if hasattr(sw, "reset_controller") and callable(sw.reset_controller):
                sw.reset_controller()
                continue
            sw.vsctl(f"set-controller {sw.name} {temp_target}")
            sw.controllerUUIDs(update=True)
            sw.vsctl(f"set-controller {sw.name} {target}")
            sw.controllerUUIDs(update=True)
        if wait:
            self.wait_switches_connect()

    def configLinkStatus(self, a, b, status):
        node_a = self.net.get(a)
        node_b = self.net.get(b)
        connections = node_a.connectionsTo(node_b)
        if isinstance(node_a, NoviSwitch):
            node_a.configLinkStatus([c[0] for c in connections], status)
        if isinstance(node_b, NoviSwitch):
            node_b.configLinkStatus([c[1] for c in connections], status)
        self.net.orig_configLinkStatus(a, b, status)

    def config_all_links_up(self):
        for link in self.net.links:
            self.configLinkStatus(
                link.intf1.node.name,
                link.intf2.node.name,
                "up"
            )

    def stop(self):
        self.net.stop()
        mininet.clean.cleanup()
