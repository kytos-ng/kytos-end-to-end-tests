#!/usr/bin/env python3
"""
Simple OpenFlow controller to receive packet_in messages for testing purposes.
Supports multiple switch connections via TCP.
Uses pyof.v0x04 library for OpenFlow message handling.
"""

import socket
import struct
import sys
import threading
import time
from collections import defaultdict
from contextlib import contextmanager

# Import pyof modules for OpenFlow v0x04
from pyof.foundation.base import GenericMessage
from pyof.v0x04.asynchronous.packet_in import PacketIn
from pyof.v0x04.asynchronous.port_status import PortStatus
from pyof.v0x04.common.header import Header, Type
from pyof.v0x04.common.port import Port
from pyof.v0x04.common.utils import unpack_message
from pyof.v0x04.controller2switch.barrier_reply import BarrierReply
from pyof.v0x04.controller2switch.common import (
    MultipartType,
    TableFeaturePropType,
    InstructionsProperty,
    NextTablesProperty,
    ActionsProperty,
    OxmProperty,
)
from pyof.v0x04.controller2switch.features_reply import FeaturesReply
from pyof.v0x04.controller2switch.features_request import FeaturesRequest
from pyof.v0x04.controller2switch.multipart_reply import MultipartReply
from pyof.v0x04.controller2switch.multipart_request import MultipartRequest
from pyof.v0x04.symmetric.echo_reply import EchoReply
from pyof.v0x04.symmetric.echo_request import EchoRequest
from pyof.v0x04.symmetric.hello import Hello


def of_slicer(remaining_data: bytes) -> tuple[list[bytes], bytes]:
    """Slice a raw `bytes` instance into OpenFlow packets."""
    data_len = len(remaining_data)
    pkts = []
    while data_len > 3:
        length_field = struct.unpack('!H', remaining_data[2:4])[0]
        ofver = remaining_data[0]
        # sanity checks: badly formatted packet
        if length_field == 0:
            remaining_data = remaining_data[4:]
            data_len = len(remaining_data)
            continue
        if data_len >= length_field:
            pkts.append(remaining_data[:length_field])
            remaining_data = remaining_data[length_field:]
            data_len = len(remaining_data)
        else:
            break
    return pkts, remaining_data


class SwitchConnection:
    """Class to manage a single switch connection."""
    
    def __init__(self, controller, conn: socket.socket, addr):
        self.controller = controller
        self.conn = conn
        self.addr = addr
        self.switch_id = str(addr)
        self.running = False
        self.thread = None
        self.remaining_data = b""
        
        # Initialize switch info
        self.switch_info = {
            'name': None,
            'dpid': None,
            'ports': [],
            'tables': [],
            'features': {},
        }

        # Message handlers
        self.ofp_handlers = {
            Type.OFPT_HELLO: self.handle_hello,
            Type.OFPT_ERROR: self.handle_error,
            Type.OFPT_PACKET_IN: self.handle_packet_in,
            Type.OFPT_BARRIER_REPLY: self.handle_barrier_reply,
            Type.OFPT_FEATURES_REPLY: self.handle_features_reply,
            Type.OFPT_ECHO_REQUEST: self.handle_echo_request,
            Type.OFPT_ECHO_REPLY: self.handle_echo_reply,
            Type.OFPT_MULTIPART_REPLY: self.handle_multipart_reply,
            Type.OFPT_PORT_STATUS: self.handle_port_status,
        }
        
        self.ofpmp_handlers = {
            MultipartType.OFPMP_PORT_DESC: self.handle_port_desc_reply,
            MultipartType.OFPMP_PORT_STATS: self.handle_port_stats_reply,
            MultipartType.OFPMP_TABLE_FEATURES: self.handle_table_features_reply,
        }
    
        # Watchers for specific message types
        self.watchers = defaultdict(list)

        # Handle replies
        self.reply_locks = dict[int, threading.Lock]()
        self.replies = dict[int, GenericMessage]()

    def start(self):
        """Start the connection thread."""
        self.running = True
        self.thread = threading.Thread(target=self._run)
        self.thread.daemon = True
        self.thread.start()
        
        # Send initial hello message
        self.send_hello()
    
    def _run(self):
        """Main run loop for handling messages from this switch."""
        try:
            while self.running:
                # Try to receive data
                data = self.conn.recv(8192)
                if not data:
                    break
                    
                # Process OpenFlow message(s)
                packets, self.remaining_data = of_slicer(self.remaining_data + data)
                for packet in packets:
                    # Use unpack_message to convert raw bytes to message object
                    message = unpack_message(packet)
                    self.process_message(message)
                
        except socket.error:
            print(f"Connection lost with switch {self.addr}")
        finally:
            self._cleanup()
    
    def process_message(self, message: GenericMessage):
        """Process a single OpenFlow message."""
        message_type = message.header.message_type

        if reply_lock := self.reply_locks.get(message.header.xid):
            reply_lock.release()
            del self.reply_locks[message.header.xid]

        # Check if there are watchers for this message type
        if message_type in self.watchers:
            for watcher in self.watchers[message_type]:
                try:
                    watcher(message)
                except Exception as e:
                    print(f"Error in watcher for message type {message_type}: {e}")
        
        # Process normally with registered handlers
        handler_func = self.ofp_handlers.get(message_type)
        
        if handler_func is not None:
            handler_func(message)
        else:
            print(f"Unknown message type {message_type} received from switch {self.switch_id}")

    def _cleanup(self):
        """Clean up resources when connection closes."""
        self.running = False
        if self.conn:
            try:
                self.conn.close()
            except:
                pass
        # Remove from controller's clients list
        if self.switch_id in self.controller.clients:
            del self.controller.clients[self.switch_id]
    
    def stop(self):
        """Stop the connection."""
        self.running = False
        if self.conn:
            try:
                self.conn.close()
            except:
                pass
    
    def send_and_wait_for_reply(
        self,
        message: GenericMessage,
        timeout: float = -1
    ) -> GenericMessage:
        """Send a message and wait for reply."""
        message_lock = threading.Lock()
        message_lock.acquire()

        self.reply_locks[int(message.header.xid)] = message_lock

        self.send(message)

        if not message_lock.acquire(timeout=timeout):
            raise TimeoutError(f"Timeout waiting for reply to {message}")
        message_lock.release()

        return self.replies[int(message.header.xid)]

    def send(self, message: GenericMessage):
       """Send a message to the switch."""
       self.conn.send(message.pack())

    def send_hello(self):
        """Send a Hello message to the switch."""
        self.send(Hello())
        print("Sent Hello message to switch.")
    
    def send_features_request(self):
        """Send a Features Request message to the switch."""
        self.send(FeaturesRequest())
        print("Sent Features Request to switch.")
    
    def do_echo(
        self,
        data_to_echo: bytes = None,

    ):
        """Send an Echo Request message to the switch."""
        sent_echo = EchoRequest(data=data_to_echo)
        reply = self.send_and_wait_for_reply(sent_echo)
        reply.data
        print("Sent Echo Request to switch.")

    def send_multipart_request(self, req_type: MultipartType):
        """Send a multipart request to get port information."""
        multipart_request = MultipartRequest(
            multipart_type=req_type,
            flags=0
        )
        self.conn.send(multipart_request.pack())
        print(f"Sent multipart request for type {req_type} to switch.")
    
    def handle_packet_in(self, message: PacketIn):
        """Handle packet_in message."""
        self.controller.packet_count += 1
        self.controller.packet_reasons[int(message.reason)] += 1
        
        print(f"Packet-in received from switch {self.switch_id}: buffer_id={message.buffer_id}, total_len={message.total_len}, table={message.table_id}, reason={message.reason}")
        print(f"Total packet-ins so far: {self.controller.packet_count}")
        print(f"Reason distribution: {dict(self.controller.packet_reasons)}")
    
    def handle_features_reply(self, message: FeaturesReply):
        """Handle features reply message."""
        print(f"Features reply received from switch {self.switch_id}")
        
        # Store switch info
        switch_info = self.switch_info
        switch_info['dpid'] = message.datapath_id
        switch_info['n_buffers'] = message.n_buffers
        switch_info['n_tables'] = message.n_tables
        switch_info['auxiliary_id'] = message.auxiliary_id
        switch_info['capabilities'] = message.capabilities
        
        print(f"Switch DPID: {message.datapath_id}")
        print(f"Buffers: {message.n_buffers}")
        print(f"Tables: {message.n_tables}")
        print(f"Auxiliary ID: {message.auxiliary_id}")
        print(f"Capabilities: {message.capabilities}")

    def handle_hello(self, message: Hello):
        """Handle hello message."""
        print(f"Hello message received from switch {self.switch_id}")
        self.send_features_request()
        # Send multipart request for port information after receiving hello
        self.send_multipart_request(MultipartType.OFPMP_PORT_DESC)
        # Send multipart request for table features
        self.send_multipart_request(MultipartType.OFPMP_TABLE_FEATURES)
    
    def handle_echo_request(self, message: EchoRequest):
        """Handle echo request message."""
        print(f"Echo request received from switch {self.switch_id}")
        # Create and send echo reply with same data
        echo_reply = EchoReply(xid=message.header.xid)
        if hasattr(message, 'data') and message.data:
            echo_reply.data = message.data
        self.conn.send(echo_reply.pack())
        print(f"Sent Echo Reply to switch {self.switch_id}")
    
    def handle_echo_reply(self, message: EchoReply):
        """Handle echo reply message."""
        print(f"Echo reply received from switch {self.switch_id}")
    
    def handle_error(self, message: Header):
        """Handle error message."""
        print(f"Error message received from switch {self.switch_id}")
    
    def handle_barrier_reply(self, message: BarrierReply):
        """Handle barrier reply message."""
        print(f"Barrier reply received from switch {self.switch_id}")

    def handle_multipart_reply(self, message: MultipartReply):
        """Handle multipart reply message."""
        # Look up the specific handler for this multipart type
        handler_func = self.ofpmp_handlers.get(message.multipart_type)
        if handler_func is not None:
            # Call handler with just the message parameter
            handler_func(message)
        else:
            print(f"No handler for multipart type: {message.multipart_type}")

    def handle_port_desc_reply(self, message: MultipartReply):
        """Handle port description reply message."""
        print(f"Port Description Reply received from switch {self.switch_id}")
        
        # Extract port information from the multipart reply
        ports = {}
        for port in message.body:
            port_info = {
                'port_no': port.port_no,
                'hw_addr': port.hw_addr,
                'name': port.name,
                'config': port.config,
                'state': port.state,
                'curr': port.curr,
                'advertised': port.advertised,
                'supported': port.supported,
                'peer': port.peer,
                'curr_speed': port.curr_speed,
                'max_speed': port.max_speed
            }
            ports[int(port.port_no)] = port_info
            print(f"Port {port.port_no}: {port.name} (MAC: {port.hw_addr})")
        
        # Store port information in switch info
        self.switch_info['ports'] = ports
        print(f"Total ports stored: {len(ports)}")

    def handle_table_features_reply(self, message: MultipartReply):
        """Handle table features reply message."""
        print(f"Table Features Reply received from switch {self.switch_id}")
        
        # Handle table features reply - store table information
        tables = {}
        for table in message.body:
            table_info = {
                'table_id': table.table_id,
                'name': table.name,
                'metadata_match': table.metadata_match,
                'metadata_write': table.metadata_write,
                'config': table.config,
                'max_entries': table.max_entries,
                'properties': []
            }
            
            # Extract properties for each table
            for table_property in table.properties:
                property_info = {}
                property_info['type'] = table_property.property_type
                if isinstance(table_property, InstructionsProperty):
                    property_info['instruction_ids'] = [inst.instruction_type for inst in table_property.instruction_ids]
                elif isinstance(table_property, NextTablesProperty):
                    property_info['next_table_ids'] = table_property.next_table_ids
                elif isinstance(table_property, ActionsProperty):
                    property_info['action_ids'] = [action.action_type for action in table_property.action_ids]
                elif isinstance(table_property, OxmProperty):
                    property_info['match_fields'] = [field.oxm_class for field in table_property.oxm_ids]
                else:
                    property_info['data'] = str(table_property)
                
                table_info['properties'].append(property_info)
            
            tables[int(table.table_id)] = table_info
        
        # Print detailed information about each table
        for i, table in tables.items():
            print(f"Table {i}:")
            print(f"  ID: {table['table_id']}")
            print(f"  Name: {table['name']}")
            print(f"  Max Entries: {table['max_entries']}")
            print(f"  Properties:")
            for prop in table['properties']:
                print(f"    Type: {prop['type']}")
                if 'instruction_ids' in prop:
                    print(f"      Instructions: {prop['instruction_ids']}")
                if 'action_ids' in prop:
                    print(f"      Actions: {prop['action_ids']}")
                if 'match_fields' in prop:
                    print(f"      Match Fields: {prop['match_fields']}")
                if 'next_table_ids' in prop:
                    print(f"      Next Tables: {prop['next_table_ids']}")
        # Store table information in switch info
        self.switch_info['tables'] = tables
        print(f"Total tables stored: {len(tables)}")


    def handle_port_stats_reply(self, message: MultipartReply):
        """Handle port statistics reply message."""
        print(f"Port stats reply received from switch {self.switch_id}")
    
    def handle_port_status(self, message: PortStatus):
        """Handle port status change message."""
        print(f"Port status message received from switch {self.switch_id}")
        # This is a generic handler that will be overridden by watchers
        pass

    @contextmanager
    def watch(self, message_type, listener_func=None):
        """Context manager to watch for specific message types.
        
        Args:
            message_type: The OpenFlow message type to watch for
            listener_func: Optional function to call when message is received
        """
        # Register the listener if provided
        if listener_func is not None:
            self.watchers[message_type].append(listener_func)
        
        try:
            yield self
        finally:
            # Remove the listener if it was added
            if listener_func is not None and listener_func in self.watchers[message_type]:
                self.watchers[message_type].remove(listener_func)


class OpenFlowController:
    def __init__(self, host: str = '0.0.0.0', port: int = 6653):
        self.host = host
        self.port = port
        self.socket: socket.socket = None
        self.clients: dict[str, SwitchConnection] = {}  # switch_id -> connection
        self.packet_count: int = 0
        self.packet_reasons: dict[int, int] = defaultdict(int)
        self.running: bool = False
        
    def get_switch_by_dpid(self, dpid: int) -> SwitchConnection:
        """
        Look up a switch connection by its datapath ID.
        
        Args:
            dpid: The datapath ID to search for
            
        Returns:
            SwitchConnection object if found, None otherwise
        """
        for switch_conn in self.clients.values():
            # Check if switch_info has been populated with a dpid
            if switch_conn.switch_info.get('dpid') == dpid:
                return switch_conn
        return None
        
    def start(self) -> None:
        """Start the OpenFlow controller."""
        print(f"Starting OpenFlow controller on {self.host}:{self.port}")
        
        # Create TCP socket
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((self.host, self.port))
        self.socket.listen(5)
        self.running = True
        
        # Start the main loop in a separate thread
        main_loop_thread = threading.Thread(target=self._main_loop)
        main_loop_thread.daemon = True
        main_loop_thread.start()
        
    def _main_loop(self) -> None:
        """Main loop for accepting connections."""
        try:
            while self.running:
                # Accept new connections
                try:
                    conn, addr = self.socket.accept()
                    print(f"New switch connected: {addr}")
                    
                    # Create and start a switch connection
                    switch_conn = SwitchConnection(self, conn, addr)
                    switch_conn.start()
                    
                    # Store reference to the connection
                    self.clients[switch_conn.switch_id] = switch_conn
                    
                except socket.error:
                    if self.running:
                        print("Socket error occurred")
                    break
                    
        except Exception as e:
            print(f"Error in main loop: {e}")
            
    def stop(self) -> None:
        """Stop the controller."""
        self.running = False
        if self.socket:
            self.socket.close()
        # Stop all clients gracefully
        for switch_id, conn in list(self.clients.items()):
            conn.stop()
            # Remove from clients dictionary
            if switch_id in self.clients:
                del self.clients[switch_id]


def main() -> None:
    """Main function to start the controller."""
    controller = OpenFlowController()
    try:
        controller.start()
        # Wait for keyboard interrupt
        while controller.running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nShutting down controller...")
        controller.stop()
    except Exception as e:
        print(f"Error in main: {e}")
        controller.stop()

if __name__ == '__main__':
    main()
