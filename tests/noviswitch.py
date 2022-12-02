import os
import re
import sys
import argparse
import yaml
import paramiko
import time
from mininet.node import Switch
import mininet.log as log
import logging
import logging.handlers

from .novisettings import *

novi_cleanup_commands = [
    'del config controller controllergroup all controllerid all',
    'del config ofserver',
    'del config ofclient ipaddr all',
    'del config flow tableid all',
]

formatter = logging.Formatter(fmt='%(asctime)s %(name)s - %(levelname)s - %(message)s')
handler = logging.handlers.SysLogHandler('/dev/log')
handler.setFormatter(formatter)
logger = logging.getLogger("noviswitch")
logger.setLevel(logging.INFO)
logger.addHandler(handler)

def run_cmd(cmd):
    logger.info("running cmd in %s: %s" % (linux_ip, cmd))
    stream = os.popen(cmd)
    return stream.read()


class NoviSwitch( Switch ):
    "Noviflow Virtual Switch"
    metadata = {}
    name_map = {}

    def __init__( self, name, verbose=False, **kwargs ):
        Switch.__init__( self, name, **kwargs )
        self.verbose = verbose
        if name in known_switches:
            self.novi_name = name
        elif name in self.name_map:
            self.novi_name = self.name_map[name]
        else:
            self.novi_name = None
            for s in sorted(known_switches.keys()):
                if s not in self.metadata:
                    self.metadata[s] = {'used_by': name}
                    self.name_map[name] = s
                    self.novi_name = s
                    break

            if not self.novi_name:
                log.error('No more Noviflow switches available: known_switches=%s current_used=%s\n' % (known_switches, self.metadata))
                exit(1)

    @classmethod
    def setup( cls ):
        "Make sure all NoviSwitches are is accessible"
        errors = []
        for s, switch in known_switches.items():
            logger.info("checking switch %s\n" % s)
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(switch['ip'], username=user, password=passwd, port=switch.get('ssh_port', 22))
            # generic command just for testing if we have System error: write failure.
            cmd = 'del config port portno all description'
            stdin, stdout, stderr = client.exec_command(cmd)
            result = stdout.read().decode("utf-8")
            if 'error' in result.lower():
                errors.append('ERROR: Failed to check switch %s: %s' % (s, ' '.join(result)))
                log.error(errors[-1])

        if errors:
            log.error('Found errors when checking for Noviflow switches:' + '\n'.join(errors))
            exit(1)

    @classmethod
    def cleanup( cls ):
        """"Clean up"""
        logger.info( '*** Cleaning up L2TP tunnels\n' )
        tunnels = run_cmd('ip l2tp show tunnel | egrep -o "Tunnel [0-9]+"').split('\n')[:-1]
        for tunnel in tunnels:
            s,tid = tunnel.split(' ')
            run_cmd('ip l2tp del tunnel tunnel_id %s 2>/dev/null' % (tid))

    def novi_start(self):
        result = run_cmd('lsmod')
        if result.find("l2tp_eth") == -1:
            run_cmd('modprobe l2tp_eth')
        switch = known_switches[self.novi_name]
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(switch['ip'], username=user, password=passwd, port=switch.get('ssh_port', 22))

        cmd = 'show status port portno all'
        stdin, stdout, stderr = client.exec_command(cmd)
        result = stdout.readlines()
        if not result[0].find('port admin link description'):
            errors.append('ERROR: Failed to check ports status at %s: %s' % (s, ' '.join(result)))
            return
        max_ifaces = len(result) - 2
        for p in range(1, max_ifaces+1):
            cmd = 'del config port portno %s l2tpaddr' % p
            stdin, stdout, stderr = client.exec_command(cmd)
            result = stdout.read().decode("utf-8")
            if 'error' in result.lower():
                log.error('error removing config from port %s in switch %s: %s\n' % (p, s, result))

        for cmd in novi_cleanup_commands:
            stdin, stdout, stderr = client.exec_command(cmd)
            result = stdout.readlines()

    def get_switch_ports(self):
        ports = []
        for intf in self.intfs.values():
            if not intf.IP():
                ports.append(self.intf_name_to_number(intf.name))
        return ports

    def novi_stop(self):
        switch = known_switches[self.novi_name]
        for p in self.get_switch_ports():
            cmd = 'del config port portno %s l2tpaddr' % p
            stdin, stdout, stderr = client.exec_command(cmd)
            result = stdout.read().decode("utf-8")
            if 'error' in result.lower():
                log.error('error removing config from port %s in switch %s: %s\n' % (p, s, result))

        for cmd in novi_cleanup_commands:
            stdin, stdout, stderr = client.exec_command(cmd)
            result = stdout.readlines()

    @classmethod
    def batchShutdown( cls, switches ):
        "Shutdown switchs, to be waited on later in stop()"
        for sw in switches:
            logger.info( 'batch shutdown switches - %s (%s)\n' % (sw.name, sw.novi_name))
            switch = known_switches[sw.novi_name]
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(switch['ip'], username=user, password=passwd, port=switch.get('ssh_port', 22))

            for cmd in novi_cleanup_commands:
                stdin, stdout, stderr = client.exec_command(cmd)
                result = stdout.readlines()
        return switches

    def intf_name_to_number(self, intf_name):
        nums = re.findall(r'\d+$', intf_name)
        return int(nums[0])

    def novi_setup_intf(self, intf_name):
        logger.info("novi_setup_intf %s\n" % intf_name)
        switch = known_switches[self.novi_name]
        self.setup_link_noviflow(switch, linux_ip, intf_name)
        self.setup_link_linux(switch, linux_ip, intf_name)

    def setup_link_noviflow(self, switch, remote_ip, intf_name):
        port_num = self.intf_name_to_number(intf_name)
        l2tp_port = port_num + switch['tun_port']
        logger.info("setup_link_noviflow port_num=%d l2tp_port=%d\n" % (port_num, l2tp_port))
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(switch['ip'], username=user, password=passwd, port=switch.get('ssh_port', 22))
        cmd = 'del config port portno %d l2tpaddr' % (port_num)
        client.exec_command(cmd)
        cmd = 'set config port portno %d portdown off' % (port_num)
        client.exec_command(cmd)
        cmd = 'set config port portno %d l2tpaddr %s localtunnelid %s remotetunnelid %s localsessionid %s remotesessionid %s udpsrc %s udpdst %s' % (port_num, remote_ip, l2tp_port, l2tp_port, l2tp_port, l2tp_port, l2tp_port, l2tp_port)
        l2tp_config_ok = False
        l2tp_config_warn_sent = False
        for i in range(3):
            stdin, stdout, stderr = client.exec_command(cmd)
            result = stdout.readlines()
            if len(result) > 0:
                logger.warning('--> WARN: cmd=|%s| -- result=|%s|' % (cmd, result))
                continue
            check_cmd = 'show config port portno %d' % port_num
            stdin, stdout, stderr = client.exec_command(check_cmd)
            result = stdout.readlines()
            if self.is_l2tp_config_ok(result, remote_ip, l2tp_port):
                l2tp_config_ok = True
                if l2tp_config_warn_sent:
                    logger.warning('-->> Now ok!')
                break
            l2tp_config_warn_sent = True
            logger.warning('-->> WARN: L2tp tunnel configuration not applied. Trying again...')
        if not l2tp_config_ok:
            logger.error('-->> ERROR: L2tp tunnel config Failed. You will have to apply mannually:')
            logger.error('     %s' % cmd)

    def is_l2tp_config_ok(self, result, remote_ip, l2tp_port):
        if isinstance(result, list):
            result = "\n".join(result)
        try:
            l2tp_config = re.findall(r'L2tp tunnel configuration.*', result, flags=re.DOTALL)[0]
        except:
            l2tp_config = None
        if not l2tp_config:
            return False
        required_matches = [
            r"Remote ip:\s+%s" % remote_ip,
            r"Local tunnel id:\s+%s" % l2tp_port,
            r"Remote tunnel id:\s+%s" % l2tp_port,
            r"Local session id:\s+%s" % l2tp_port,
            r"Udp source port:\s+%s" % l2tp_port,
            r"Udp destination port:\s+%s" % l2tp_port,
        ]
        for required_match in required_matches:
            if not re.findall(required_match, l2tp_config):
                return False
        return True

    def setup_link_linux(self, switch, linux_ip, intf_name):
        port_num = self.intf_name_to_number(intf_name)
        l2tp_port = port_num + switch['tun_port']
        remote_ip = switch['ip']

        tun_name = 'tun%d' % (l2tp_port)
        cmd = 'ip l2tp add tunnel tunnel_id %s peer_tunnel_id %s remote %s local 0.0.0.0 encap udp udp_dport %s udp_sport %s 2>&1' % (l2tp_port, l2tp_port, remote_ip, l2tp_port, l2tp_port)
        result = run_cmd(cmd)
        if len(result) > 0:
            logger.error('--> ERROR: cmd=|%s| -- result=|%s|' % (cmd, result))
        cmd = 'ip l2tp add session name %s tunnel_id %s session_id %s peer_session_id %s 2>&1' % (tun_name, l2tp_port, l2tp_port, l2tp_port)
        result = run_cmd(cmd)
        if len(result) > 0:
            logger.error('--> ERROR: cmd=|%s| -- result=|%s|' % (cmd, result))
        cmd = 'ip link set up %s 2>&1' % (tun_name)
        result = run_cmd(cmd)
        if len(result) > 0:
            logger.error('--> ERROR: cmd=|%s| -- result=|%s|' % (cmd, result))

        ovs_cmds = [
            'ovs-vsctl add-port noviswitch %s' % tun_name,
            'ovs-vsctl add-port noviswitch %s' % intf_name,
            'ovs-ofctl add-flow noviswitch in_port="%s",actions=output:"%s"' % (tun_name, intf_name),
            'ovs-ofctl add-flow noviswitch in_port="%s",actions=output:"%s"' % (intf_name, tun_name),
        ]
        cmd = 'ovs-vsctl list-br'
        result = run_cmd(cmd)
        if result.find("noviswitch") == -1:
            ovs_cmds.insert(0, 'ovs-vsctl add-br noviswitch')
        for cmd in ovs_cmds:
            result = run_cmd(cmd)
            if len(result) > 0:
                logger.error('--> ERROR: cmd=|%s| -- result=|%s|' % (cmd, result))

    def start( self, controllers ):
        "Setup a new NoviSwitch"
        logger.info("start switch %s (%s)\n" % (self.name, self.novi_name))

        self.novi_start()

        for intf in self.intfs.values():
            if not intf.IP():
                self.novi_setup_intf(intf.name)

        switch = known_switches[self.novi_name]
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(switch['ip'], username=user, password=passwd, port=switch.get('ssh_port', 22))

        cmd = 'set config switch dpid %s' % hex(int(self.dpid))
        stdin, stdout, stderr = client.exec_command(cmd)
        result = stdout.read().decode("utf-8")
        if 'error' in result.lower():
            log.error('error configuring dpid %s in switch %s: %s\n' % (self.dpid, self.novi_name, result))

        i = 0
        for c in controllers:
            i += 1
            c_ip = c.IP() if c.IP() not in ['127.0.0.1'] else linux_ip
            c_port = c.port
            cmd = 'set config controller controllergroup c%d controllerid 1 priority 1 ipaddr %s port %d security none' % (i, c_ip, c_port)
            stdin, stdout, stderr = client.exec_command(cmd)
            result = stdout.read().decode("utf-8")
            if 'error' in result.lower():
                log.error('error configuring controller %s in switch %s: %s\n' % (c.IP(), self.novi_name, result))

        cmd = 'set config ofserver port 6634'
        stdin, stdout, stderr = client.exec_command(cmd)
        result = stdout.read().decode("utf-8")
        if 'error' in result.lower():
            log.error('error configuring ofserver %s in switch %s: %s\n' % (self.novi_name, result))

        cmd = 'set config ofclient ipaddr %s' % (linux_ip)
        stdin, stdout, stderr = client.exec_command(cmd)
        result = stdout.read().decode("utf-8")
        if 'error' in result.lower():
            log.error('error configuring ofclient %s in switch %s: %s\n' % (self.novi_name, result))

    def stop( self, deleteIntfs=True ):
        """Terminate IVS switch.
           deleteIntfs: delete interfaces? (True)"""
        logger.info("stop %s\n" % self.novi_name)
        self.novi_stop()

    def attach( self, intf ):
        "Connect a data port"
        logger.info("attach %s (not-implemented)\n" % self.novi_name)

    def detach( self, intf ):
        "Disconnect a data port"
        logger.info("dettach %s (not-implemented)\n" % self.novi_name)

    def dpctl( self, *args ):
        "Run dpctl command"
        switch = 'tcp:%s:6634' % known_switches[self.novi_name]['ip']
        return run_cmd('ovs-ofctl %s -O OpenFlow13 %s %s | grep -v OFPST_FLOW' % (args[0], switch, ' '.join(args[1:])))
