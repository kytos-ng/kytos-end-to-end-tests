import os
import re
import sys
import argparse
import yaml
import paramiko
import time
from mininet.node import Switch
from mininet.util import ( quietRun, errRun, errFail, moveIntf, isShellBuiltin,
                           numCores, retry, mountCgroups, BaseString, decode,
                           encode, getincrementaldecoder, Python3, which )
from mininet.moduledeps import pathCheck
import mininet.log as log
from mininet.clean import addCleanupCallback

from novisettings import *

novi_cleanup_commands = [
    'del config controller controllergroup all controllerid all',
    'del config ofserver',
    'del config ofclient ipaddr all',
    'del config flow tableid all',
]

def run_cmd(cmd):
    #print('--> LINUX: %s' % (cmd))
    #mylog(linux_ip, cmd)
    stream = os.popen(cmd)
    return stream.read()


class NoviSwitch( Switch ):
    "Noviflow Virtual Switch"
    metadata = {}

    def __init__( self, name, verbose=False, **kwargs ):
        Switch.__init__( self, name, **kwargs )
        self.verbose = verbose
        if name in known_switches:
            self.novi_name = name
        else:
            self.novi_name = None
            for s in sorted(known_switches.keys()):
                if s not in self.metadata:
                    self.metadata[s] = {}
                    self.novi_name = s
                    break

            if not self.novi_name:
                error('No more Noviflow switches available\n')
                exit(1)

    @classmethod
    def setup( cls ):
        "Make sure all NoviSwitches are is accessible"
        addCleanupCallback( cls.cleanup )
        errors = []
        for s, switch in known_switches.items():
            log.info("checking switch %s\n" % s)
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(switch['ip'], username=user, password=passwd)
            cmd = 'show status switch'
            stdin, stdout, stderr = client.exec_command(cmd)
            result = stdout.read().decode("utf-8")
            if 'error' in result.lower():
                errors.append('ERROR: Failed to check switch %s: %s' % (s, ' '.join(result)))
                log.error(errors[-1])

        if errors:
            error('Found errors when checking for Noviflow switches:' + '\n'.join(errors))
            exit(1)

    @classmethod
    def cleanup( cls ):
        """"Clean up"""
        log.info( '*** Cleaning up L2TP tunnels\n' )
        tunnels = run_cmd('ip l2tp show tunnel | egrep -o "Tunnel [0-9]+"').split('\n')[:-1]
        for tunnel in tunnels:
            s,tid = tunnel.split(' ')
            run_cmd('ip l2tp del tunnel tunnel_id %s' % (tid))

    def novi_start(self):
        result = run_cmd('lsmod')
        if result.find("l2tp_eth") == -1:
            run_cmd('modprobe l2tp_eth')
        switch = known_switches[self.novi_name]
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(switch['ip'], username=user, password=passwd)

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
        for switch in switches:
            log.info( 'batch kill %s (not-implemented)\n' % switch)
        return switches

    def intf_name_to_number(self, intf_name):
        nums = re.findall('\d+$', intf_name)
        return int(nums[0])

    def novi_setup_intf(self, intf_name):
        log.info("novi_setup_intf %s\n" % intf_name)
        switch = known_switches[self.novi_name]
        self.setup_link_noviflow(switch, linux_ip, intf_name)
        self.setup_link_linux(switch, linux_ip, intf_name)

    def setup_link_noviflow(self, node, remote_ip, intf_name):
        port_num = self.intf_name_to_number(intf_name)
        l2tp_port = port_num + node['tun_port']
        log.info("setup_link_noviflow port_num=%d l2tp_port=%d\n" % (port_num, l2tp_port))
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(node['ip'], username=user, password=passwd)
        cmd = 'del config port portno %d l2tpaddr' % (port_num)
        #mylog(node['ip'], cmd)
        client.exec_command(cmd)
        cmd = 'set config port portno %d l2tpaddr %s localtunnelid %s remotetunnelid %s localsessionid %s remotesessionid %s udpsrc %s udpdst %s' % (port_num, remote_ip, l2tp_port, l2tp_port, l2tp_port, l2tp_port, l2tp_port, l2tp_port)
        #mylog(node['ip'], cmd)
        #print('--> %s --> %s' % (node['ip'], cmd))
        l2tp_config_ok = False
        l2tp_config_warn_sent = False
        for i in range(3):
            stdin, stdout, stderr = client.exec_command(cmd)
            result = stdout.readlines()
            if len(result) > 0:
                print('--> WARN: cmd=|%s| -- result=|%s|' % (cmd, result))
                continue
            check_cmd = 'show config port portno %d' % port_num
            stdin, stdout, stderr = client.exec_command(check_cmd)
            result = stdout.readlines()
            if self.is_l2tp_config_ok(result, remote_ip, l2tp_port):
                l2tp_config_ok = True
                if l2tp_config_warn_sent:
                    print('-->> Now ok!')
                break
            l2tp_config_warn_sent = True
            print('-->> WARN: L2tp tunnel configuration not applied. Trying again...')
        if not l2tp_config_ok:
            print('-->> ERROR: L2tp tunnel config Failed. You will have to apply mannually:')
            print('     %s' % cmd)

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
            "Remote ip:\s+%s" % remote_ip,
            "Local tunnel id:\s+%s" % l2tp_port,
            "Remote tunnel id:\s+%s" % l2tp_port,
            "Local session id:\s+%s" % l2tp_port,
            "Udp source port:\s+%s" % l2tp_port,
            "Udp destination port:\s+%s" % l2tp_port,
        ]
        for required_match in required_matches:
            if not re.findall(required_match, l2tp_config):
                return False
        return True

    def setup_link_linux(self, node, linux_ip, intf_name):
        port_num = self.intf_name_to_number(intf_name)
        l2tp_port = port_num + node['tun_port']
        remote_ip = node['ip']

        tun_name = 'tun%d' % (l2tp_port)
        cmd = 'ip l2tp add tunnel tunnel_id %s peer_tunnel_id %s remote %s local %s encap udp udp_dport %s udp_sport %s 2>&1' % (l2tp_port, l2tp_port, remote_ip, linux_ip, l2tp_port, l2tp_port)
        result = run_cmd(cmd)
        if len(result) > 0:
            print('--> ERROR: cmd=|%s| -- result=|%s|' % (cmd, result))
        cmd = 'ip l2tp add session name %s tunnel_id %s session_id %s peer_session_id %s 2>&1' % (tun_name, l2tp_port, l2tp_port, l2tp_port)
        result = run_cmd(cmd)
        if len(result) > 0:
            print('--> ERROR: cmd=|%s| -- result=|%s|' % (cmd, result))
        cmd = 'ip link set up %s 2>&1' % (tun_name)
        result = run_cmd(cmd)
        if len(result) > 0:
            print('--> ERROR: cmd=|%s| -- result=|%s|' % (cmd, result))

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
                print('--> ERROR: cmd=|%s| -- result=|%s|' % (cmd, result))

    def start( self, controllers ):
        "Setup a new NoviSwitch"
        log.info("start switch %s (%s)\n" % (self.name, self.novi_name))

        self.novi_start()

        for intf in self.intfs.values():
            if not intf.IP():
                self.novi_setup_intf(intf.name)

        switch = known_switches[self.novi_name]
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(switch['ip'], username=user, password=passwd)

        cmd = 'set config switch dpid %s' % hex(int(self.dpid))
        stdin, stdout, stderr = client.exec_command(cmd)
        result = stdout.read().decode("utf-8")
        if 'error' in result.lower():
            log.error('error configuring dpid %s in switch %s: %s\n' % (self.dpid, self.novi_name, result))

        i = 0
        for c in controllers:
            i += 1
            cmd = 'set config controller controllergroup c%d controllerid 1 priority 1 ipaddr %s port %d security none' % (i, c.IP(), c.port)
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
        log.info("stop %s\n" % self.novi_name)
        self.novi_stop()

    def attach( self, intf ):
        "Connect a data port"
        log.info("attach %s (not-implemented)\n" % self.novi_name)

    def detach( self, intf ):
        "Disconnect a data port"
        log.info("dettach %s (not-implemented)\n" % self.novi_name)

    def dpctl( self, *args ):
        "Run dpctl command"
        switch = 'tcp:%s:6634' % known_switches[self.novi_name]['ip']
        allowed_of_proto = 'OpenFlow13'
        return self.cmd('ovs-ofctl', args[0], '-O', allowed_of_proto, switch, args[1:])
