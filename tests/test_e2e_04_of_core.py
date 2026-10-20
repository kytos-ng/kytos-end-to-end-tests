import os
import socket
import time

import pytest
import requests

from tests.helpers import NetworkTest

CONTROLLER = '127.0.0.1'
KYTOS_API = f'http://{CONTROLLER}:8181/api/kytos'
OF_PORT = 6653
LOG_FILE = '/var/log/syslog'

# A valid OF1.3 HELLO (version 0x04, type 0, length 8, xid 1).
OF_HELLO = bytes.fromhex("0400000800000001")

# A valid OF1.3 FLOW_MOD whose single instruction carries the type
# OFPIT_EXPERIMENTER (0xFFFF): a valid InstructionType enum member that
# python-openflow's InstructionType.find_class() does not map, so unpacking
# it raises an unhandled TypeError instead of a clean UnpackException
# (kytos-ng/python-openflow#114). of_core only catches
# (UnpackException, AttributeError), so the TypeError escapes unpack and is
# logged by the kytos core handler wrapper as an ERROR with a full traceback.
OF_FLOWMOD_EXPERIMENTER = bytes.fromhex(
    "040e005000000001000000000000000000000000000000000000000000000000"
    "ffffffffffffffffffffffff000100000001000400000000ffff001800000000"
    "0000001000000001ffff000000000000"
)

XFAIL_REASON = (
    "A FlowMod whose instruction type is OFPIT_EXPERIMENTER (0xFFFF) makes "
    "python-openflow raise an unhandled TypeError on unpack "
    "(kytos-ng/python-openflow#114). of_core only catches UnpackException and "
    "AttributeError, so the TypeError escapes unpack and the message is not "
    "handled gracefully (it surfaces as an unhandled-exception traceback)."
)


class TestE2EOfCoreMalformed:
    """Abuse-case coverage for malformed OpenFlow 1.3 messages (OF-02).

    A fake switch opens a plain-TCP connection to the controller's OpenFlow
    port, completes the HELLO, and then sends a crafted FlowMod. A malformed
    message from a peer must not destabilise the controller and must be handled
    gracefully (rejected/logged, connection dropped), not surface as an
    unhandled exception escaping the message parser.
    """

    net = None

    def setup_method(self, method):
        """Start the controller clean before each test."""
        self.net.start_controller(clean_config=True, enable_all=True)
        self.net.wait_switches_connect()
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start(start_controller=False)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def core_status(self):
        """Return the controller status payload."""
        response = requests.get(f'{KYTOS_API}/core/status/', timeout=10)
        assert response.status_code == 200, response.text
        return response.json()

    @staticmethod
    def log_size():
        """Current size of the controller log, or 0 if unavailable."""
        try:
            return os.path.getsize(LOG_FILE)
        except OSError:
            return 0

    @staticmethod
    def log_since(offset):
        """Return log content written after ``offset``."""
        try:
            with open(LOG_FILE, errors='replace') as log:
                log.seek(offset)
                return log.read()
        except OSError:
            return ''

    def send_as_fake_switch(self, payload):
        """Connect to the OF port, say HELLO, then send ``payload``."""
        sock = socket.create_connection((CONTROLLER, OF_PORT), timeout=10)
        try:
            sock.sendall(OF_HELLO)
            time.sleep(2)              # let the controller process the HELLO
            sock.sendall(payload)
            time.sleep(3)              # let the controller process the message
            try:
                sock.recv(4096)
            except OSError:
                pass
        finally:
            sock.close()

    @pytest.mark.xfail(reason=XFAIL_REASON, strict=False)
    def test_010_malformed_experimenter_instruction_is_handled(self):
        """A crafted FlowMod with an OFPIT_EXPERIMENTER instruction must be
        handled gracefully: the controller stays running and the parser does
        not leak an unhandled exception for the message."""
        assert self.core_status()['response'] == 'running'

        offset = self.log_size()
        self.send_as_fake_switch(OF_FLOWMOD_EXPERIMENTER)
        time.sleep(2)
        new_logs = self.log_since(offset)

        # The controller process must still answer and report itself healthy.
        after = self.core_status()
        assert after['response'] == 'running', after

        # The malformed message must not surface as an unhandled exception
        # escaping the OpenFlow parser.
        assert 'TypeError' not in new_logs, new_logs[-2000:]
        assert 'flow_instructions.py' not in new_logs, new_logs[-2000:]
