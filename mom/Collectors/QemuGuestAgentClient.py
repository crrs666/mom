# Memory Overcommitment Manager
# Copyright (C) 2011 Adam Litke, IBM Corporation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public
# License along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

import logging
import socket
import time
import json
import base64

class ProtocolError(Exception):
    """
    Indicates an error during agent communication
    """
    def __init__(self, errno, msg):
        self.errno = errno
        self.msg = msg

    def __str__(self):
        return "ProtocolError (%s): %s" % (self.errno, self.msg)

class QemuAgentRet:
    """
    Describes the return value from guest agent API calls.
    A call can either return an error or data.

    If an error occurs, the error dict will contain two keys:
        class: The type of error as reported by qemu
        data: A dictionary containing additional details about the error

    If the command succeeded, the data dict will contain the actual
    return value.  Only one of 'error' and 'data' will be set.
    """
    def __init__(self, json_str):
        obj = json.loads(json_str)
        if 'error' in obj:
            self.error = obj['error']
            self.data = None
        else:
            self.error = None
            self.data = obj['return']

class QemuGuestAgentClient:
    """
    QemuGuestAgentClient: Communicate with the Qemu guest agent. This class
    manages the connection state and exposes a set of callable APIs via the
    'api' member. If the class is initialized with the path to the local unix
    socket, it uses _QemuGuestAgentSocketClient class to communicate with the
    agent. Otherwise, it tries to use _QemuGuestAgentLibvirtClient class to
    communicate via hypervisor_iface's qemuAgentCommand method.
    The list of currently-supported functions is:

    ping:        Ping the guest agent
    file_open:   Open a file for reading or writing
    file_close:  Close a previously opened file
    file_read:   Read some data from an open file
    file_write:  Write to an open file
    """
    def __init__(self, uuid, hypervisor_iface, where):
        if where is None:
            self.client = _QemuGuestAgentLibvirtClient(
                uuid, hypervisor_iface)
        else:
            self.client = _QemuGuestAgentSocketClient(where)
        self.api = self.client.api

class _QemuGuestAgentLibvirtClient:
    """
    Communicate with the Qemu guest agent using hypervisor_iface's
    qemuAgentCommand method. If the method is unavailable, it raises KeyError.
    """
    def __init__(self, uuid, hypervisor_iface):
        """
        Initialize the client for a particular guest
        """
        try:
            getattr(hypervisor_iface, 'qemuAgentCommand')
        except KeyError:
            raise Exception("hypervisor does not support qemuAgentCommand")
        self.hypervisor_iface = hypervisor_iface
        self.uuid = uuid
        self.api = _QemuGuestAgentAPI(self)

    def _call(self, command, args={}):
        """
        Make the actual agent RPC call.  First marshall the arguments, then
        send the request. Finally, receive the response and return a structured
        Python class: QemuAgentRet.
        """
        request = { 'execute': command, 'arguments': args }
        json_str = json.dumps(request)

        response = self.hypervisor_iface.qemuAgentCommand(self.uuid, json_str)
        return QemuAgentRet(response)

class _QemuGuestAgentSocketClient:
    """
    Communicate with the Qemu guest agent using a standalone unix socket.
    The class should be initialized with the path to the local unix socket
    over which a connection to the agent will be attempted.
    """
    def __init__(self, where):
        """
        Initialize the client for a particular unix socket
        """
        self.logger = logging.getLogger('mom.Collectors._QemuGuestAgentSocketClient')
        self.api = _QemuGuestAgentAPI(self)
        self.where = where
        self.sock = None

    def _reset_conn(self, sock):
        """
        After the client connects to the guest agent, there may be stale
        responses left in the channel from previous sessions.  This
        method makes use of the 'guest-sync' API to synchronize the
        channel.

        Be careful to choose a unique sequential number so that we are
        confident that the agent response is a result of this call.
        """
        seq = int(time.time() % 2147483647) # Long_max
        request = { 'execute': 'guest-sync', 'arguments': { 'id': seq } }
        req_str = json.dumps(request)
        self._sock_send(sock, req_str.encode('utf-8'))

        # Read data from the channel until we get a matching response
        while True:
            response = self._sock_recv_until(sock, b"\n").decode('utf-8')
            resp_obj = json.loads(response)
            if 'return' in resp_obj:
                try:
                    if resp_obj['return'] == seq:
                        break
                except TypeError:
                    pass

    def _connect(self):
        sock_type = socket.AF_UNIX
        self.logger.debug("Connecting to %s" % self.where)
        try:
            self.sock = socket.socket(sock_type, socket.SOCK_STREAM)
            self.sock.settimeout(2)
            self.sock.connect(self.where)
            self._reset_conn(self.sock)
        except socket.timeout:
            self._sock_close(self.sock)
            self.sock = None
            raise ProtocolError(-1, "Timed out")
        except socket.error as e:
            self._sock_close(self.sock)
            self.sock = None
            raise ProtocolError(e.errno, "Connection failed: %s" % e.strerror)

    def _make_connection(self):
        """
        We only need to initiate the connection once since our channel
        connection is persistent.
        """
        if self.sock is None:
            self._connect()
        return self.sock

    def _sock_send(self, sock, msg):
        """
        Send a message via a socket connection.
        """
        sent = 0
        while sent < len(msg):
            try:
                ret = sock.send(msg[sent:])
            except socket.timeout:
                self._sock_close(self.sock)
                self.sock = None
                raise ProtocolError(-1, "Timed out")
            except socket.error as e:
                self._sock_close(self.sock)
                self.sock = None
                raise ProtocolError(e.errno, e.strerror)

            if ret == 0:
                self._sock_close(self.sock)
                self.sock = None
                raise ProtocolError(-1, "Unable to send on socket")
            sent = sent + ret

    def _sock_recv_until(self, sock, token):
        """
        Receive data from the socket one byte at a time until the token is read
        """
        data = b""
        while True:
            if len(data) > 4096:
                return None
            try:
                ch = sock.recv(1)
            except socket.timeout:
                self._sock_close(self.sock)
                self.sock = None
                raise ProtocolError(-1, "Timed out")
            except socket.error as e:
                self._sock_close(self.sock)
                self.sock = None
                raise ProtocolError(e.errno, e.strerror)
            if ch == b'':
                self.logger.debug("Connection closed")
                return None
            data += ch
            if data[-len(token):] == token:
                return data

    def _sock_close(self, sock):
        """
        Properly close down the socket
        """
        try:
            sock.shutdown(socket.SHUT_RDWR)
            sock.close()
        except socket.error:
            pass

    def _call(self, command, args={}):
        """
        Make the actual agent RPC call.  First marshall the arguments, then
        send the request.  Finally, receive the response and return a structured
        Python class: QemuAgentRet.
        """
        request = { 'execute': command, 'arguments': args }
        json_str = json.dumps(request)

        sock = self._make_connection()
        self._sock_send(sock, json_str.encode('utf-8'))
        response = self._sock_recv_until(sock, b"\n").decode('utf-8')
        return QemuAgentRet(response)

class _QemuGuestAgentAPI():
    """
    Wrapper functions for the supported Qemu guest agent API calls.
    """
    def __init__(self, client):
        self.client = client

    def ping(self):
        return self.client._call('guest-ping')

    def file_open(self, path, mode="r"):
        args = { 'path': path, 'mode': mode }
        return self.client._call('guest-file-open', args)

    def file_close(self, handle):
        args = { 'handle': handle }
        return self.client._call('guest-file-close', args)

    def file_read(self, handle, count):
        args = { 'handle': handle, 'count': count }
        ret = self.client._call('guest-file-read', args)
        if ret.data:
            # Decode the buffer before returning it
            ret.data['buf'] = base64.b64decode(ret.data['buf-b64'])
        return ret

    def file_write(self, handle, buffer):
        args = { 'handle': handle, 'buf-b64': base64.b64encode(buffer) }
        return self.client._call('guest-file-write', args)
