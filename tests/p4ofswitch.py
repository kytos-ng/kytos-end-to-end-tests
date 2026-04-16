"""P4OfSwitch"""

import os
import re
import time
from mininet.nodelib import DockerSwitch
from mininet.log import error

P4OFSWITCH_ARCH = os.environ.get("P4OFSWITCH_ARCH", "tf1")

MYLOCALIP = os.environ.get("MYLOCALIP")
if os.environ.get("SWITCH_CLASS") == "P4OfSwitch" and not MYLOCALIP:
    try:
        stream = os.popen("ip -j route get 8.8.8.8 | jq -r '.[0].prefsrc'")
        output = stream.read().strip()
    except:
        output = ""
    if not output:
        raise ValueError(
            "Could not identify the Local IP. Please define MYLOCALIP env var"
            " or check pref ip default router"
        )
    MYLOCALIP = output


class P4OfSwitch(DockerSwitch):
    """P4OfSwitch (P4OfAgent + AmLight P4 pipeline)."""

    def __init__(self, name, **kwargs):
        """create p4ofswitch instance."""
        self.arch = P4OFSWITCH_ARCH
        self.controllers = []
        super().__init__(
            name,
            image="amlight/p4ofswitch:latest",
            pull="always",
            env=[
                f"ARCH={self.arch}",
                "MIN_RETRY_DELAY=2",
                "MAX_RETRY_DELAY=4",
                "DEFAULT_PORTDOWN=on",
            ],
            volume=[f"/tmp/{name}-logs:/var/log"],
        )

    def wait_start(self):
        """Wait until switch has started."""
        for i in range(300):
            output = self.cmd("p4ofagent show status system-health")
            if "System internal status : OK" in output:
                return True
            time.sleep(1)
        raise TimeoutError(f"timeout waiting for switch {self.name} to start")

    def intf_name_to_number(self, intf_name):
        """Convert interface name into number: s1-eth1 -> 1."""
        nums = re.findall(r"\d+$", intf_name)
        return int(nums[0])

    def setup_intf(self, intf):
        """Setup the interface by enabling it and other confis."""
        portno = self.intf_name_to_number(intf.name)
        cmd = f"p4ofagent set config port portno {portno} --portdown off"
        output = self.cmd(cmd)
        if output:
            error(f"Failed to run node={self.name} cmd={cmd}: {outpt}")

    def start(self, controllers):
        """Start p4ofswitch instance."""
        self.wait_start()

        # configure DPID
        dpid = "{:016x}".format(int(self.dpid, 16))
        dpid = ":".join([dpid[i:i+2] for i in range(0, 16, 2)])
        output = self.cmd(f"p4ofagent set config switch dpid {dpid}")
        if output:
            raise ValueError(
                f"Failed to configure DPID {dpid} for {self.name}: {output}"
            )

        # enable ports in use
        for intf in self.intfs.values():
            if intf.link:
                self.setup_intf(intf)

        # configure controllers
        i = 0
        for c in controllers:
            i += 1
            c_ip = c.IP() if c.IP() not in ["127.0.0.1"] else MYLOCALIP
            c_port = c.port
            self.controllers.append((c_ip, c_port))
            cmd_str = (
                f"p4ofagent set config controller --ip {c_ip} --port {c_port}"
                f" --name c{i} --priority {1000+i}"
            )
            output = self.cmd(cmd_str)
            if output:
                error(
                    f"\nFailed to run node={self.name} cmd={cmd_str}: {output}"
                )
        
    def connected(self):
        output = self.cmd("p4ofagent show config controller")
        return "Is connected: True" in output

    def dpctl(self, *args):
        "Run dpctl command"
        cmd = f"ovs-ofctl {args[0]} -O OpenFlow13 tcp:127.0.0.1:6653 "
        cmd += " ".join(args[1:])
        return self.cmd(cmd)

    def attach(self, intfName):
        """Connect a data port"""
        intf = self.nameToIntf.get(intfName)
        if not intf:
            error(f"Attached unknown intf {intfName} to {self.name}. Ignoring")
            return
        self.setup_intf(intf)

    def reset_controller(self):
        """Reset the controller connection."""
        cmd = "p4ofagent set status controller all --reset"
        return self.cmd(cmd)

    def vsctl(self, *args):
        """Run vsctl command (try to map from ovs-vsctl)"""
        if len(args) == 1:
            args = args[0].split(" ")
        if args[0] == "del-controller":
            cmd = "p4ofagent del config controller all"
            output = self.cmd(cmd)
            if output:
                error(f"\nFailed to run name={self.name} cmd={cmd}: {output}")
            self.controllers = []
        elif args[0] == "set-controller":
            cmd = "p4ofagent del config controller all"
            output = self.cmd(cmd)
            if output:
                error(f"\nFailed to run name={self.name} cmd={cmd}: {output}")
            self.controllers = []
            i = 0
            for c in args[2:]:
                i += 1
                proto, ip, port = c.split(":")
                ip = ip if ip not in ["127.0.0.1"] else MYLOCALIP
                self.controllers.append((ip, port))
                cmd_str = (
                    f"p4ofagent set config controller --ip {ip} "
                    f"--port {port} --name c{i} --priority {1000+i}"
                )
                output = self.cmd(cmd_str)
                if output:
                    error(
                        f"\nFailed to run node={self.name} "
                        f"cmd={cmd_str}: {output}"
                    )
        else:
            logger.error("vsctl command not implemented: %s" % args)

    def controllerUUIDs(self, *args, **kwargs):
        pass
