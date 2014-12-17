#!/usr/bin/env python
# coding:utf-8
import socket
import base64
import struct
import encrypt
import errno
try:
    import urllib.request as urllib2
    import urllib.parse as urlparse
    urlquote = urlparse.quote
    urlquote = urlparse.unquote
except ImportError:
    import urllib2
    import urlparse
    urlquote = urllib2.quote
    unquote = urllib2.unquote
try:
    from cStringIO import StringIO
except ImportError:
    try:
        from StringIO import StringIO
    except ImportError:
        from io import BytesIO as StringIO


class sssocket(object):
    bufsize = 8192

    def __init__(self, ssServer=None, timeout=10, parentproxy='', iplist=None):
        self.ssServer = ssServer
        self.timeout = timeout
        self.parentproxy = parentproxy
        self.pproxyparse = urlparse.urlparse(parentproxy)
        self._sock = None
        self.crypto = None
        self.connected = False
        self._rbuffer = StringIO()

    def connect(self, address):
        self.__address = address
        p = urlparse.urlparse(self.ssServer)
        sshost, ssport, ssmethod, sspassword = (p.hostname, p.port, p.username, p.password)
        self.crypto = encrypt.Encryptor(sspassword, ssmethod)
        if not self.parentproxy:
            self._sock = socket.create_connection((sshost, ssport), 1)
        elif self.parentproxy.startswith('http://'):
            self._sock = socket.create_connection((self.pproxyparse.hostname, self.pproxyparse.port or 80), 1)
            s = 'CONNECT %s:%s HTTP/1.1\r\nHost: %s\r\n' % (sshost, ssport, sshost)
            if self.pproxyparse.username:
                a = '%s:%s' % (self.pproxyparse.username, self.pproxyparse.password)
                s += 'Proxy-Authorization: Basic %s\r\n' % base64.b64encode(a.encode())
            s += '\r\n'
            self._sock.sendall(s.encode())
            remoterfile = self._sock.makefile('rb', 0)
            data = remoterfile.readline()
            if b'200' not in data:
                raise IOError(0, 'bad response: %s' % data)
            while not data in (b'\r\n', b'\n', b''):
                data = remoterfile.readline()
        else:
            raise IOError(0, 'sssocket does not support parent proxy server: %s for now' % self.parentproxy)
        self.settimeout(self.timeout)
        self.setsockopt = self._sock.setsockopt
        self.fileno = self._sock.fileno

    def recv(self, size):
        if not self.connected:
            self.sendall(b'')
        buf = self._rbuffer
        buf.seek(0, 2)  # seek end
        buf_len = buf.tell()
        self._rbuffer = StringIO()  # reset _rbuf.  we consume it via buf.
        if buf_len < size:
            # Not enough data in buffer?  Try to read.
            data = self.crypto.decrypt(self._sock.recv(size - buf_len))
            if len(data) == size and not buf_len:
                # Shortcut.  Avoid buffer data copies
                return data
            buf.write(data)
            del data  # explicit free
        buf.seek(0)
        rv = buf.read(size)
        self._rbuffer.write(buf.read())
        return rv

    def sendall(self, data):
        if self.connected:
            self._sock.sendall(self.crypto.encrypt(data))
        else:
            host, port = self.__address
            self._sock.sendall(self.crypto.encrypt(b''.join([b'\x03',
                                                   chr(len(host)).encode(),
                                                   host.encode(),
                                                   struct.pack(b">H", port),
                                                   data])))
            self.connected = True

    def readline(self, size=-1):
        buf = self._rbuffer
        buf.seek(0, 2)  # seek end
        if buf.tell() > 0:
            # check if we already have it in our buffer
            buf.seek(0)
            bline = buf.readline(size)
            if bline.endswith('\n') or len(bline) == size:
                self._rbuffer = StringIO()
                self._rbuffer.write(buf.read())
                return bline
            del bline
        if size < 0:
            # Read until \n or EOF, whichever comes first
            buf.seek(0, 2)  # seek end
            self._rbuffer = StringIO()  # reset _rbuf.  we consume it via buf.
            while True:
                try:
                    data = self.recv(self.bufsize)
                except socket.error as e:
                    if e.args[0] == errno.EINTR:
                        continue
                    raise
                if not data:
                    break
                nl = data.find(b'\n')
                if nl >= 0:
                    nl += 1
                    buf.write(data[:nl])
                    self._rbuffer.write(data[nl:])
                    break
                buf.write(data)
            del data
            return buf.getvalue()
        else:
            # Read until size bytes or \n or EOF seen, whichever comes first
            buf.seek(0, 2)  # seek end
            buf_len = buf.tell()
            if buf_len >= size:
                buf.seek(0)
                rv = buf.read(size)
                self._rbuffer = StringIO()
                self._rbuffer.write(buf.read())
                return rv
            self._rbuffer = StringIO()  # reset _rbuf.  we consume it via buf.
            while True:
                try:
                    data = self.recv(self.bufsize)
                except socket.error as e:
                    if e.args[0] == errno.EINTR:
                        continue
                    raise
                if not data:
                    break
                left = size - buf_len
                # did we just receive a newline?
                nl = data.find(b'\n', 0, left)
                if nl >= 0:
                    nl += 1
                    # save the excess data to _rbuf
                    self._rbuffer.write(data[nl:])
                    if buf_len:
                        buf.write(data[:nl])
                        break
                    else:
                        # Shortcut.  Avoid data copy through buf when returning
                        # a substring of our first recv().
                        return data[:nl]
                n = len(data)
                if n == size and not buf_len:
                    # Shortcut.  Avoid data copy through buf when
                    # returning exactly all of our first recv().
                    return data
                if n >= left:
                    buf.write(data[:left])
                    self._rbuffer.write(data[left:])
                    break
                buf.write(data)
                buf_len += n
                #assert buf_len == buf.tell()
            return buf.getvalue()

    def close(self):
        if self._sock:
            self._sock.close()

    def __del__(self):
        self.close()

    def dup(self):
        new = sssocket()
        new.ssServer = self.ssServer
        new.timeout = self.timeout
        new.parentproxy = self.parentproxy
        new.pproxyparse = self.pproxyparse
        new._sock = self._sock.dup()
        new.crypto = self.crypto
        new.connected = self.connected
        new._rbuffer = self._rbuffer
        return new

    def settimeout(self, timeout):
        self._sock.settimeout(timeout)
