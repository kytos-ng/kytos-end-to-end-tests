

import mininet.clean
from mininet.link import Intf
from mininet.net import Mininet
from mininet.node import OVSBridge
from mininet.topo import Topo

from .p4ofswitch import P4OfSwitch
from .oftestcontroller import OFTestController


class OFTestTopo(Topo):
    def build(self):
        self.oftest = self.addHost("oftest")
        self.test_switch = self.addSwitch("test_switch")


class TestOFTest:
    net = None

    def setup_method(self, method):
        """
        It is called at the beginning of every class method execution
        """
        pass

    @classmethod
    def setup_class(cls):
        cls.net = Mininet()
        cls.net.start()

        cls.oftest_controller = cls.net.addHost(name="c0", cls=OFTestController)

        # Make eth0 the default interface of the controller, as its a docker host
        cls.oftest_controller.inNamespace = False
        interface = Intf("eth0", cls.oftest_controller, port=0)
        interface.updateIP()
        cls.oftest_controller.inNamespace = True

        cls.oftest_controller.port = 6653
        
        cls.oftest_switch = cls.net.addSwitch(name="s1", cls=P4OfSwitch)

        # Make eth0 the default interface of the switch, as its a docker host
        cls.oftest_switch.inNamespace = False
        interface = Intf("eth0", cls.oftest_switch, port=0)
        interface.updateIP()
        cls.oftest_switch.inNamespace = True

        # TC doesnt work.
        # cls.oftest_switch.cmd("tc qdisc add dev veth13 ingress")
        # cls.oftest_switch.cmd("tc filter add dev veth13 parent ffff: protocol all u32 match u8 0 0 action mirred egress mirror dev veth14")
        # cls.oftest_switch.cmd("tc qdisc add dev veth14 ingress")
        # cls.oftest_switch.cmd("tc filter add dev veth14 parent ffff: protocol all u32 match u8 0 0 action mirred egress mirror dev veth13")

        cls.oftest_switch.cmd("p4ofagent set config lag portno 1000 13 14 --aggregated_pkt_in --confirm")

        cls.net.addLink("c0", "s1", 1, 1)
        cls.net.addLink("c0", "s1", 2, 2)

        cls.oftest_bridge = cls.net.addSwitch(name="s2", cls=OVSBridge)

        cls.net.addLink("s1", "s2", 13)
        cls.net.addLink("s1", "s2", 14)

        # Creating a loop doesn't work
        # cls.net.addLink("s1", "s1", 13, 14)



        cls.oftest_switch.start([cls.oftest_controller])

    @classmethod
    def teardown_class(cls):
        cls.net.stop()
        mininet.clean.cleanup()

    def test_001_amlight_tests(self):
        """"""
        response = self.oftest_controller.cmd("./oft -V 1.3 --controller-timeout 100 -i 1@c0-eth1 -i 2@c0-eth2 amlight")
        assert False, response
