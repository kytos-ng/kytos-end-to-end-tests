"""P4OfSwitch"""

import os
from mininet.nodelib import DockerSwitch
from mininet.log import error

P4OFSWITCH_ARCH = os.environ.get("P4OFSWITCH_ARCH", "tf1")

class P4OfSwitch(DockerSwitch):
    """P4OfSwitch (P4OfAgent + AmLight P4 pipeline)."""

    def __init__(self, name, **kwargs):
        """create p4ofswitch instance."""
        self.arch = P4OFSWITCH_ARCH
        DockerSwitch.__init__(
            self,
            name,
            image="amlight/p4ofswitch:latest",
            pull="always",
            env=[f"ARCH={self.arch}"],
        )

    def wait_start(self):
        """Wait until switch has started."""
        for i in range(300):
            output = self.cmd("p4ofagent show status system-health")
            if "System internal status : OK" in output:
                return True
            time.sleep(1)
        raise TimeoutError(f"timeout waiting for switch {self.name} to start")

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

        # configure controllers
        i = 0
        for c in controllers:
            i += 1
            c_ip = c.IP()
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
            for c in controllers:
                i += 1
                c_ip = c.IP()
                c_port = c.port
                self.controllers.append((c_ip, c_port))
                cmd_str = (
                    f"p4ofagent set config controller --ip {c_ip} "
                    f"--port {c_port} --name c{i} --priority {1000+i}"
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
