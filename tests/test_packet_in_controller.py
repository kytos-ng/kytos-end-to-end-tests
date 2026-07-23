#!/usr/bin/env python3
"""
Simple OpenFlow controller to receive packet_in messages for testing purposes.
Supports multiple switch connections via TCP.
"""

import sys
import socket
import struct
import threading
import time
from collections import defaultdict
import select

# OpenFlow protocol constants
OFP_VERSION = 0x01
OFP_PACKET_IN = 10
OFP_PACKET_OUT = 13
OFP_FLOW_MOD = 9
OFP_FLOW_REMOVED = 11
OFP_FEATURES_REPLY = 5
OFP_HELLO = 0
OFP_ERROR = 1
OFP_BARRIER_REPLY = 15

class OpenFlowController:
    def __init__(self, host='0.0.0.0', port=6653):
        self.host = host
        self.port = port
        self.socket = None
        self.clients = {}  # switch_id -> connection
        self.packet_count = 0
        self.packet_reasons = defaultdict(int)
        self.running = False
        
    def start(self):
        """Start the OpenFlow controller."""
        print("Starting OpenFlow controller on {}:{}".format(self.host, self.port))
        
        # Create TCP socket
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((self.host, self.port))
        self.socket.listen(5)
        self.running = True
        
        try:
            while self.running:
                # Accept new connections
                try:
                    conn, addr = self.socket.accept()
                    print("New switch connected: {}".format(addr))
                    
                    # Start a thread to handle this switch
                    client_thread = threading.Thread(
                        target=self.handle_client, 
                        args=(conn, addr)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                    
                except socket.error:
                    if self.running:
                        print("Socket error occurred")
                    break
                    
        except KeyboardInterrupt:
            print("\nShutting down controller...")
        finally:
            self.stop()
    
    def handle_client(self, conn, addr):
        """Handle communication with a single switch."""
        switch_id = str(addr)
        self.clients[switch_id] = conn
        
        try:
            while self.running:
                # Try to receive data
                data = conn.recv(8192)
                if not data:
                    break
                    
                # Process OpenFlow message(s)
                self.process_messages(data, switch_id)
                
        except socket.error:
            print("Connection lost with switch {}".format(addr))
        finally:
            if switch_id in self.clients:
                del self.clients[switch_id]
            conn.close()
    
    def process_messages(self, data, switch_id):
        """Process one or more OpenFlow messages from data."""
        offset = 0
        while offset < len(data):
            # Ensure we have at least the header
            if offset + 8 > len(data):
                print("Incomplete header data")
                break
                
            # Parse OpenFlow header to get length
            header_data = data[offset:offset+8]
            try:
                version, msg_type, length, xid = struct.unpack('!BBHI', header_data)
            except struct.error:
                print("Failed to parse header")
                break
                
            # Check if we have the full message
            if length > len(data) - offset:
                print("Message truncated, waiting for more data")
                break
                
            # Extract full message
            full_message = data[offset:offset+length]
            self.process_message(full_message, switch_id)
            
            # Move to next message
            offset += length
            
    def process_message(self, data, switch_id):
        """Process a single OpenFlow message."""
        if len(data) < 8:
            return
            
        # Parse OpenFlow header
        version, msg_type, length, xid = struct.unpack('!BBHI', data[:8])
        
        if msg_type == OFP_PACKET_IN:
            self.handle_packet_in(data, switch_id, xid)
        elif msg_type == OFP_HELLO:
            self.handle_hello(data, switch_id)
        elif msg_type == OFP_ERROR:
            self.handle_error(data, switch_id)
        elif msg_type == OFP_BARRIER_REPLY:
            self.handle_barrier_reply(data, switch_id)
        else:
            print(f"Unknown message type received from switch {switch_id}")
    
    def handle_packet_in(self, data, switch_id, xid):
        """Handle packet_in message."""
        if len(data) < 20:
            print("Invalid packet_in message")
            return
            
        # Parse packet_in header
        try:
            buffer_id, total_len, reason, table_id = struct.unpack('!IHBB', data[8:16])
        except struct.error:
            print("Failed to parse packet_in header")
            return
            
        self.packet_count += 1
        self.packet_reasons[reason] += 1
        
        print("Packet-in received from switch {}: buffer_id={}, total_len={}, table={}, reason={}".format(
            switch_id, buffer_id, total_len, table_id, reason))
        print("Total packet-ins so far: {}".format(self.packet_count))
        print("Reason distribution: {}".format(dict(self.packet_reasons)))
        
        # Send back a flow mod reply to acknowledge (this is required by OpenFlow spec)
        self.send_flow_mod_reply(switch_id, xid)
    
    def handle_hello(self, data, switch_id):
        """Handle hello message."""
        print("Hello message received from switch {}".format(switch_id))
    
    def handle_error(self, data, switch_id):
        """Handle error message."""
        print("Error message received from switch {}".format(switch_id))
    
    def handle_barrier_reply(self, data, switch_id):
        """Handle barrier reply message."""
        print("Barrier reply received from switch {}".format(switch_id))
    
    def send_flow_mod_reply(self, switch_id, xid):
        """Send flow mod reply back to switch."""
        try:
            # Create a basic flow mod reply message
            # Format: version(1) + type(1) + length(2) + xid(4) + padding(4)
            reply_data = struct.pack('!BBHI', OFP_VERSION, OFP_FLOW_MOD, 16, xid)
            if switch_id in self.clients:
                self.clients[switch_id].send(reply_data)
        except Exception as e:
            print("Error sending flow mod reply: {}".format(e))
    
    def stop(self):
        """Stop the controller."""
        self.running = False
        if self.socket:
            self.socket.close()
        for conn in self.clients.values():
            try:
                conn.close()
            except:
                pass

def main():
    """Main function to start the controller."""
    controller = OpenFlowController()
    try:
        controller.start()
    except KeyboardInterrupt:
        print("\nExiting controller...")

if __name__ == '__main__':
    main()