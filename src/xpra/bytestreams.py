# This file is part of Parti.
# Copyright (C) 2011, 2012 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008, 2009, 2010 Nathaniel Smith <njs@pobox.com>
# Parti is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import errno

#on some platforms (ie: OpenBSD), reading and writing from sockets
#raises an IOError but we should continue if the error code is EINTR
#this wrapper takes care of it.
def untilConcludes(f, *a, **kw):
    while True:
        try:
            return f(*a, **kw)
        except (IOError, OSError), e:
            if e.args[0] == errno.EINTR:
                continue
            raise

class Connection(object):
    def __init__(self, target):
        self.target = target
        self.input_bytecount = 0
        self.output_bytecount = 0

    def _write(self, *args):
        w = untilConcludes(*args)
        self.output_bytecount += w
        return w

    def _read(self, *args):
        r = untilConcludes(*args)
        self.input_bytecount += len(r)
        return r

# A simple, portable abstraction for a blocking, low-level
# (os.read/os.write-style interface) two-way byte stream:
# client.py relies on self.filename to locate the unix domain
# socket (if it exists)
class TwoFileConnection(Connection):
    def __init__(self, writeable, readable, abort_test=None, target=None):
        Connection.__init__(self, target)
        self._writeable = writeable
        self._readable = readable
        self._abort_test = abort_test
        self.filename = None

    def may_abort(self, action):
        """ if abort_test is defined, run it """
        if self._abort_test:
            self._abort_test(action)

    def read(self, n):
        self.may_abort("read")
        return self._read(os.read, self._readable.fileno(), n)

    def write(self, buf):
        self.may_abort("write")
        return self._write(os.write, self._writeable.fileno(), buf)

    def close(self):
        self._writeable.close()
        self._readable.close()

    def __str__(self):
        return "TwoFileConnection(%s)" % str(self.target)


class SocketConnection(Connection):
    def __init__(self, s, local, remote, target):
        Connection.__init__(self, target)
        self._s = s
        self.local = local
        self.remote = remote
        if type(remote)==str:
            self.filename = remote
        else:
            self.filename = None

    def read(self, n):
        return self._read(self._s.recv, n)

    def write(self, buf):
        return self._write(self._s.send, buf)

    def close(self):
        return self._s.close()

    def __str__(self):
        return "SocketConnection(%s - %s)" % (self.local, self.remote)
