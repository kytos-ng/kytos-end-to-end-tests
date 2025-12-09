import os
import re
import json
import paramiko
import time
from mininet.node import Switch
from mininet.clean import addCleanupCallback
import logging
import logging.handlers


novi_cleanup_commands = [
    "del config controller controllergroup all controllerid all",
    "del config flow tableid all",
]

formatter = logging.Formatter(fmt="%(asctime)s %(name)s - %(levelname)s - %(message)s")
handler = logging.handlers.SysLogHandler("/dev/log")
handler.setFormatter(formatter)
logger = logging.getLogger("noviswitch")
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)


def run_cmd(cmd):
    logger.debug("running cmd in %s: %s" % (NOVILOCALIP, cmd))
    stream = os.popen(cmd)
    return stream.read()


NOVISETTINGS = os.environ.get("NOVISETTINGS", {})
NOVISWITCHES = os.environ.get("NOVISWITCHES")
if NOVISETTINGS:
    try:
        NOVISETTINGS = json.load(open(NOVISETTINGS))
    except:
        try:
            NOVISETTINGS = json.loads(NOVISETTINGS)
        except:
            raise ValueError(
                "Invalid NOVISETTINGS environment variable: must be a JSON encoded string or file"
            )
elif NOVISWITCHES:
    switches = NOVISWITCHES.split()
    if len(switches) < 2:
        raise ValueError(
            "Invalid NOVISWITCHES environment variable: must be a space-separated list of IPs"
        )
    NOVISETTINGS = {}
    for i, sw in enumerate(switches, 1):
        NOVISETTINGS["vnovi%d" % i] = {"ip": sw}
elif os.environ.get("SWITCH_CLASS") == "NoviSwitch":
    raise ValueError(
        "Neither NOVISWITCHES nor NOVISETTINGS environment variables found. You must define one of them"
    )

NOVIUSER = os.environ.get("NOVIUSER")
NOVIPASS = os.environ.get("NOVIPASS")
if os.environ.get("SWITCH_CLASS") == "NoviSwitch" and (not NOVIUSER or not NOVIPASS):
    raise ValueError(
        "Missing env vars for username/password for Noviflow switches: NOVIUSER and NOVIPASS"
    )

NOVILOCALIP = os.environ.get("NOVILOCALIP")
if os.environ.get("SWITCH_CLASS") == "NoviSwitch" and not NOVILOCALIP:
    sw = next(iter(NOVISETTINGS.values()))
    NOVILOCALIP = run_cmd(f"ip -j route get {sw['ip']} | jq -r '.[0].prefsrc'")
    if not NOVILOCALIP:
        raise ValueError(
            "Could not identify the Local IP for L2TP tunnels. Please define NOVILOCALIP env var or check pref ip source for switches"
        )
    NOVILOCALIP = NOVILOCALIP.strip()

NOVIMAXWAIT = int(os.environ.get("NOVIMAXWAIT", 30))

# Noviflow virtual switches supports up to 16 interfaces.
# We leave an option to change this via envvars just in case
# on the future this gets increased
NOVIMAXIFACES = int(os.environ.get("NOVIMAXIFACES", 16))


class NoviSwitch(Switch):
    "Noviflow Virtual Switch"
    metadata = {}
    name_map = {}
    in_use = set()
    l2tp_tunnels = {}
    l2tp_next_id = 1

    def __init__(self, name, verbose=False, **kwargs):
        Switch.__init__(self, name, **kwargs)
        self.verbose = verbose
        self.novi_ip = None
        self.novi_name = None
        if name in NOVISETTINGS:
            self.novi_name = name
        elif name in NoviSwitch.name_map:
            self.novi_name = NoviSwitch.name_map[name]
        else:
            self.novi_name = None
            for s in sorted(NOVISETTINGS.keys()):
                if s not in NoviSwitch.metadata:
                    NoviSwitch.metadata[s] = {"used_by": name}
                    NoviSwitch.name_map[name] = s
                    self.novi_name = s
                    break

            if not self.novi_name:
                logger.error(
                    "No more Noviflow switches available: known_switches=%s current_used=%s\n"
                    % (NOVISETTINGS, NoviSwitch.metadata)
                )
                exit(1)
        NoviSwitch.in_use.add(self.novi_name)
        self.controllers = []
        switch = NOVISETTINGS[self.novi_name]
        self.novi_ip = switch["ip"]
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh_client.connect(
            self.novi_ip,
            username=NOVIUSER,
            password=NOVIPASS,
            port=switch.get("ssh_port", 22),
        )
        logger.debug(
            "ssh_client connected sw=%s novisw=%s ip=%s"
            % (self.name, self.novi_name, self.novi_ip)
        )

    def __del__(self):
        NoviSwitch.in_use.discard(self.novi_name)

    @classmethod
    def is_available(cls):
        return len(cls.in_use) < len(NOVISETTINGS)

    @classmethod
    def setup(cls):
        "Make sure all NoviSwitches are is accessible"
        errors = []
        for s, switch in NOVISETTINGS.items():
            logger.info("checking switch %s\n" % s)
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                switch["ip"],
                username=NOVIUSER,
                password=NOVIPASS,
                port=switch.get("ssh_port", 22),
            )
            # generic command just for testing if we have System error: write failure.
            cmd = "del config controller controllergroup all controllerid all"
            stdin, stdout, stderr = client.exec_command(cmd)
            result = stdout.read().decode("utf-8")
            if "error" in result.lower():
                errors.append(
                    "ERROR: Failed to check switch %s: %s" % (s, " ".join(result))
                )
                logger.error(errors[-1])

        if errors:
            logger.error(
                "Found errors when checking for Noviflow switches:" + "\n".join(errors)
            )
            exit(1)

    @classmethod
    def cleanup(cls):
        """ "Clean up"""
        logger.info("*** Cleaning up L2TP tunnels\n")
        tunnels = run_cmd('ip l2tp show tunnel | egrep -o "Tunnel [0-9]+"').split("\n")[
            :-1
        ]
        for tunnel in tunnels:
            s, tid = tunnel.split(" ")
            run_cmd("ip l2tp del tunnel tunnel_id %s 2>/dev/null" % (tid))
        for s, switch in NOVISETTINGS.items():
            logger.info(f"cleanup switch - {s} ({switch['ip']})\n")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                switch["ip"],
                username=NOVIUSER,
                password=NOVIPASS,
                port=switch.get("ssh_port", 22),
            )
            for cmd in novi_cleanup_commands:
                stdin, stdout, stderr = client.exec_command(cmd)
                result = stdout.read().decode("utf-8")
                if "error" in result.lower():
                    logger.error(f"ERROR: Failed to cleanup switch {s}: {result}")

    def addIntf(self, intf, port=None, **kwargs):
        """Wrapper for Mininet Node.addIntf to validate port number against NOVIMAXIFACES."""
        if port is None:
            port = self.newPort()
        if port > NOVIMAXIFACES:
            raise ValueError(f"Invalid port number {port} intf={intf} node={self}. Max port number: {NOVIMAXIFACES}")
        super().addIntf(intf, port=port, **kwargs)

    def is_ssh_alive(self):
        if not self.ssh_client:
            return False
        try:
            transport = self.ssh_client.get_transport()
            assert transport.is_active()
            transport.send_ignore()
        except:
            return False
        return True

    def novi_start(self):
        result = run_cmd("lsmod")
        if result.find("l2tp_eth") == -1:
            run_cmd("modprobe l2tp_eth")
        switch = NOVISETTINGS[self.novi_name]

        cmd = "show status port portno all"
        result = self.novi_cmd(cmd).splitlines()
        if not result[0].find("port admin link description"):
            errors.append(
                "ERROR: Failed to check ports status at %s: %s" % (s, " ".join(result))
            )
            return
        for p in range(1, NOVIMAXIFACES + 1):
            cmd = "del config port portno %s l2tpaddr" % p
            result = self.novi_cmd(cmd)
            if "error" in result.lower():
                logger.error(
                    "error removing config from port %s in switch %s: %s\n"
                    % (p, switch, result)
                )
                logger.error(
                    "error removing config from port %s in switch %s: %s\n"
                    % (p, switch, result)
                )

        for cmd in novi_cleanup_commands:
            result = self.novi_cmd(cmd)

    def get_switch_ports(self):
        ports = []
        for intf in self.intfs.values():
            if not intf.IP():
                ports.append(self.intf_name_to_number(intf.name))
        return ports

    def novi_stop(self):
        for p in self.get_switch_ports():
            cmd = "del config port portno %s l2tpaddr" % p
            result = self.novi_cmd(cmd)
            if "error" in result.lower():
                logger.error(
                    "error removing config from port %s in switch %s: %s\n"
                    % (p, self.novi_name, result)
                )

        for cmd in novi_cleanup_commands:
            result = self.novi_cmd(cmd)

        self.controllers = []

    @classmethod
    def batchShutdown(cls, switches):
        "Shutdown switchs, to be waited on later in stop()"
        for sw in switches:
            logger.info("batch shutdown switches - %s (%s)\n" % (sw.name, sw.novi_name))
            switch = NOVISETTINGS[sw.novi_name]
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                switch["ip"],
                username=NOVIUSER,
                password=NOVIPASS,
                port=switch.get("ssh_port", 22),
            )

            for cmd in novi_cleanup_commands:
                stdin, stdout, stderr = client.exec_command(cmd)
                result = stdout.readlines()
        return switches

    def intf_name_to_number(self, intf_name):
        nums = re.findall(r"\d+$", intf_name)
        return int(nums[0])

    def novi_setup_intf(self, intf1):
        logger.info("novi_setup_intf %s\n" % intf1.name)
        intf2 = (
            intf1.link.intf2
            if (
                intf1.link.intf1.node.name == intf1.node.name
                and intf1.link.intf1.name == intf1.name
            )
            else intf1.link.intf1
        )
        l2tp_tun_id = self.get_l2tp_tun_id(intf1, intf2)

        remote_ip = NOVILOCALIP
        if isinstance(intf2.node, NoviSwitch):
            remote_ip = intf2.node.novi_ip
        else:
            self.setup_link_linux(self.novi_ip, intf1.name, l2tp_tun_id)

        self.setup_link_noviflow(remote_ip, intf1.name, l2tp_tun_id, intf2.name)

    def get_l2tp_tun_id(self, intf1, intf2):
        link_name = "<->".join(
            sorted(
                [f"{intf1.node.name}:{intf1.name}", f"{intf2.node.name}:{intf2.name}"]
            )
        )
        if not (l2tp_tun_id := NoviSwitch.l2tp_tunnels.get(link_name)):
            l2tp_tun_id = NoviSwitch.l2tp_next_id
            NoviSwitch.l2tp_tunnels[link_name] = l2tp_tun_id
            NoviSwitch.l2tp_next_id += 1
        return l2tp_tun_id

    def setup_link_noviflow(self, remote_ip, intf_name, l2tp_tun_id, other_intf):
        port_num = self.intf_name_to_number(intf_name)
        logger.info(
            "setup_link_noviflow port_num=%d l2tp_tun_id=%d\n" % (port_num, l2tp_tun_id)
        )
        cmd = "del config port portno %d l2tpaddr" % (port_num)
        self.novi_cmd(cmd)
        cmd = "set config port portno %d portdown off" % (port_num)
        self.novi_cmd(cmd)
        remote_tun_id = l2tp_tun_id
        if remote_ip == self.novi_ip:
            if intf_name < other_intf:
                remote_tun_id = l2tp_tun_id + 1000
            else:
                l2tp_tun_id += 1000
        local_port = 17000 + l2tp_tun_id
        remote_port = 17000 + remote_tun_id
        cmd = f"set config port portno {port_num} l2tpaddr {remote_ip} localtunnelid {l2tp_tun_id} remotetunnelid {remote_tun_id} localsessionid {l2tp_tun_id} remotesessionid {remote_tun_id} udpsrc {local_port} udpdst {remote_port}"
        l2tp_config_ok = False
        l2tp_config_warn_sent = False
        for i in range(3):
            result = self.novi_cmd(cmd)
            if len(result) > 0:
                logger.warning("--> WARN: cmd=|%s| -- result=|%s|" % (cmd, result))
                continue
            check_cmd = f"show config port portno {port_num}"
            result = self.novi_cmd(check_cmd)
            if self.is_l2tp_config_ok(result, remote_ip, l2tp_tun_id, remote_tun_id, local_port, remote_port):
                l2tp_config_ok = True
                if l2tp_config_warn_sent:
                    logger.warning("-->> Now ok!")
                break
            l2tp_config_warn_sent = True
            logger.warning(
                "-->> WARN: L2tp tunnel configuration not applied. Trying again..."
            )
        if not l2tp_config_ok:
            logger.error(
                "-->> ERROR: L2tp tunnel config Failed. You will have to apply mannually:"
            )
            logger.error("     %s" % cmd)

    def is_l2tp_config_ok(self, result, remote_ip, local_tun_id, remote_tun_id, local_port, remote_port):
        if isinstance(result, list):
            result = "\n".join(result)
        try:
            l2tp_config = re.findall(
                r"L2tp tunnel configuration.*", result, flags=re.DOTALL
            )[0]
        except:
            l2tp_config = None
        if not l2tp_config:
            return False
        required_matches = [
            r"Remote ip:\s+%s" % remote_ip,
            r"Local tunnel id:\s+%s" % local_tun_id,
            r"Remote tunnel id:\s+%s" % remote_tun_id,
            r"Local session id:\s+%s" % local_tun_id,
            r"Udp source port:\s+%s" % local_port,
            r"Udp destination port:\s+%s" % remote_port,
        ]
        for required_match in required_matches:
            if not re.findall(required_match, l2tp_config):
                return False
        return True

    def setup_link_linux(self, remote_ip, intf_name, l2tp_port):
        tun_name = "tun%d" % (l2tp_port)
        udp_port = 17000 + l2tp_port
        cmd = f"ip l2tp add tunnel tunnel_id {l2tp_port} peer_tunnel_id {l2tp_port} remote {remote_ip} local 0.0.0.0 encap udp udp_dport {udp_port} udp_sport {udp_port} 2>&1"
        result = run_cmd(cmd)
        if len(result) > 0:
            logger.error("--> ERROR: cmd=|%s| -- result=|%s|" % (cmd, result))
            if "RTNETLINK answers: File exists" in result:
                logger.error("--> trying to recover... delete and create again")
                run_cmd(f"ip l2tp del tunnel tunnel_id {l2tp_port}")
                result = run_cmd(cmd)
                if len(result) > 0:
                    logger.error(
                        "--> ERROR again: cmd=|%s| -- result=|%s|" % (cmd, result)
                    )
        cmd = f"ip l2tp add session name {tun_name} tunnel_id {l2tp_port} session_id {l2tp_port} peer_session_id {l2tp_port} 2>&1"
        result = run_cmd(cmd)
        if len(result) > 0:
            logger.error("--> ERROR: cmd=|%s| -- result=|%s|" % (cmd, result))
        cmd = "ip link set up %s 2>&1" % (tun_name)
        result = run_cmd(cmd)
        if len(result) > 0:
            logger.error("--> ERROR: cmd=|%s| -- result=|%s|" % (cmd, result))

        ovs_cmds = [
            "ovs-vsctl add-port noviswitch %s" % tun_name,
            "ovs-vsctl add-port noviswitch %s" % intf_name,
            'ovs-ofctl add-flow noviswitch in_port="%s",actions=output:"%s"'
            % (tun_name, intf_name),
            'ovs-ofctl add-flow noviswitch in_port="%s",actions=output:"%s"'
            % (intf_name, tun_name),
        ]
        cmd = "ovs-vsctl list-br"
        result = run_cmd(cmd)
        if result.find("noviswitch") == -1:
            ovs_cmds.insert(0, "ovs-vsctl add-br noviswitch")
        for cmd in ovs_cmds:
            result = run_cmd(cmd)
            if len(result) > 0:
                logger.error("--> ERROR: cmd=|%s| -- result=|%s|" % (cmd, result))

    def start(self, controllers):
        "Setup a new NoviSwitch"
        switch = NOVISETTINGS[self.novi_name]
        logger.info(
            "start switch %s (%s - %s) controllers=%s\n"
            % (self.name, self.novi_name, switch["ip"], controllers)
        )

        self.novi_start()

        for intf in self.intfs.values():
            if intf.link:
                self.novi_setup_intf(intf)

        cmd = "set config switch dpid %s" % hex(int(self.dpid, 16))
        result = self.novi_cmd(cmd)
        if "error" in result.lower():
            logger.error(
                "error configuring dpid %s in switch %s: %s\n"
                % (self.dpid, self.novi_name, result)
            )

        cmd = "del config controller controllergroup all controllerid all"
        result = self.novi_cmd(cmd)
        if "error" in result.lower():
            logger.error(
                "error running del-controller in switch %s: %s\n"
                % (self.novi_name, result)
            )
        i = 0
        for c in controllers:
            i += 1
            c_ip = c.IP() if c.IP() not in ["127.0.0.1"] else NOVILOCALIP
            c_port = c.port
            self.controllers.append((c_ip, c_port))
            cmd = f"set config controller controllergroup c{i} controllerid 1 priority 1 ipaddr {c_ip} port {c_port} security none version of13"
            result = self.novi_cmd(cmd)
            if "error" in result.lower():
                logger.error(
                    "error configuring controller %s in switch %s: %s\n"
                    % (c.IP(), self.novi_name, result)
                )

        cmd = "del config ofserver"
        result = self.novi_cmd(cmd)
        if "error" in result.lower():
            logger.error(
                "error deleting ofserver config in switch %s: %s\n"
                % (self.novi_name, result)
            )

        cmd = "set config ofserver port 6634"
        result = self.novi_cmd(cmd)
        if "error" in result.lower():
            logger.error(
                "error configuring ofserver %s in switch %s: %s\n"
                % (self.novi_name, result)
            )

        cmd = "set config ofclient ipaddr %s" % (NOVILOCALIP)
        result = self.novi_cmd(cmd)
        if "error" in result.lower():
            logger.error(
                "error configuring ofclient %s in switch %s: %s\n"
                % (NOVILOCALIP, self.novi_name, result)
            )

    def novi_cmd(self, cmd):
        switch = NOVISETTINGS[self.novi_name]
        if not self.is_ssh_alive():
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(
                switch["ip"],
                username=NOVIUSER,
                password=NOVIPASS,
                port=switch.get("ssh_port", 22),
            )

        logger.debug("running cmd in %s: %s" % (switch["ip"], cmd))
        stdin, stdout, stderr = self.ssh_client.exec_command(cmd)
        result = stdout.read().decode("utf-8")
        return result

    def stop(self, deleteIntfs=True):
        """Terminate IVS switch.
        deleteIntfs: delete interfaces? (True)"""
        logger.info("stop %s\n" % self.novi_name)
        self.novi_stop()

    def connected(self):
        """Check if switch is connected to any controller"""
        result = self.novi_cmd("show status controller controllergroup all")
        for ip, port in self.controllers:
            if re.findall(r"\*%s\s+%s\s+" % (ip, port), result):
                return True
        return False

    def attach(self, intfName):
        "Connect a data port"
        intf = self.nameToIntf.get(intfName)
        if not intf:
            logger.warning(
                f"attached unknown intf {intfName} into {self.name}/{self.novi_ip}! Ignoring...\n"
            )
            return
        self.novi_setup_intf(intf)

    def detach(self, intf):
        "Disconnect a data port"
        logger.info("dettach %s (not-implemented)\n" % self.novi_name)

    def dpctl(self, *args):
        "Run dpctl command"
        switch = "tcp:%s:6634" % NOVISETTINGS[self.novi_name]["ip"]
        cmd = "ovs-ofctl %s -O OpenFlow13 %s %s" % (args[0], switch, " ".join(args[1:]))
        result = run_cmd(cmd)
        if args[0] != "del-flows":
            return result

        # if we receive a del-flows, make sure flows are deleted before returning (NOVIMAXWAIT=30)
        begin = time.time()
        while time.time() - begin <= NOVIMAXWAIT:
            cmd = "ovs-ofctl dump-flows -O OpenFlow13 %s %s|grep -v OFPST_FLOW" % (
                switch,
                " ".join(args[1:]),
            )
            flows = run_cmd(cmd)
            if len(flows.splitlines()) == 0:
                break
            logger.info(f"DEBUG: remaining flows for del-flows: {flows.splitlines()}")
            time.sleep(1)

        return result

    def reset_controller(self):
        """Reset the controller connection."""
        cmd = "set status controller controllergroup all controllerid all reset"
        result = self.novi_cmd(cmd)
        if "error" in result.lower():
            logger.error(
                "error resetting controller in switch %s: %s\n"
                % (self.novi_name, result)
            )

    def vsctl(self, *args):
        """Run vsctl command (try to map from ovs-vsctl)"""
        if len(args) == 1:
            args = args[0].split(" ")
        if args[0] == "del-controller":
            cmd = "del config controller controllergroup all controllerid all"
            result = self.novi_cmd(cmd)
            if "error" in result.lower():
                logger.error(
                    "error running del-controller in switch %s: %s\n"
                    % (self.novi_name, result)
                )
            self.controllers = []
        elif args[0] == "set-controller":
            cmd = "del config controller controllergroup all controllerid all"
            result = self.novi_cmd(cmd)
            if "error" in result.lower():
                logger.error(
                    "error running del-controller in switch %s: %s\n"
                    % (self.novi_name, result)
                )
            self.controllers = []
            i = 0
            for c in args[2:]:
                i += 1
                proto, ip, port = c.split(":")
                ip = ip if ip not in ["127.0.0.1"] else NOVILOCALIP
                self.controllers.append((ip, port))
                cmd = f"set config controller controllergroup c{i} controllerid 1 priority 1 ipaddr {ip} port {port} security none version of13"
                result = self.novi_cmd(cmd)
                if "error" in result.lower():
                    logger.error(
                        "error running del-controller in switch %s: %s\n"
                        % (self.novi_name, result)
                    )
        else:
            logger.error("vsctl command not implemented: %s" % args)

    def controllerUUIDs(self, *args, **kwargs):
        pass

    def configLinkStatus(self, intfs, status):
        """config link status"""
        for intf in intfs:
            portno = self.intf_name_to_number(intf.name)
            portdown = "off" if status == "up" else "on"
            cmd = "set config port portno %d portdown %s" % (portno, portdown)
            self.novi_cmd(cmd)


addCleanupCallback(NoviSwitch.cleanup)
