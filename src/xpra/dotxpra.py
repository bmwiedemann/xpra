# This file is part of Xpra.
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2011-2013 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os.path
import glob
import socket
import errno
import stat
import time
import sys


class ServerSockInUse(Exception):
    pass

def osexpand(s, actual_username=""):
    if len(actual_username)>0 and s.startswith("~/"):
        #replace "~/" with "~$actual_username/"
        s = "~%s/%s" % (actual_username, s[2:])
    return os.path.expandvars(os.path.expanduser(s))


class DotXpra(object):
    def __init__(self, sockdir=None, confdir=None, actual_username=""):
        from xpra.platform.paths import get_default_socket_dir, get_user_conf_dir
        self._confdir = osexpand(confdir or get_user_conf_dir(), actual_username)
        self._sockdir = osexpand(sockdir or get_default_socket_dir(), actual_username)
        if not os.path.exists(self._confdir):
            os.mkdir(self._confdir, 0o700)
        if not os.path.exists(self._sockdir):
            os.mkdir(self._sockdir, 0o700)
        hostname = os.environ.get("XPRA_SOCKET_HOSTNAME", socket.gethostname())
        self._prefix = "%s-" % (hostname,)

    def sockdir(self):
        return self._sockdir

    def confdir(self):
        return self._confdir

    def normalize_local_display_name(self, local_display_name):
        if not local_display_name.startswith(":"):
            local_display_name = ":" + local_display_name
        if "." in local_display_name:
            local_display_name = local_display_name[:local_display_name.rindex(".")]
        assert local_display_name.startswith(":")
        for char in local_display_name[1:]:
            assert char in "0123456789", "invalid character in display name: %s" % char
        return local_display_name

    def norm_make_path(self, name, dirpath):
        return os.path.join(dirpath, self._prefix + name)

    def socket_path(self, local_display_name):
        return self.norm_make_path(local_display_name[1:], self._sockdir)

    def log_path(self, local_display_name):
        return self.norm_make_path(local_display_name[1:], self._confdir)

    LIVE = "LIVE"
    DEAD = "DEAD"
    UNKNOWN = "UNKNOWN"
    def server_state(self, local_display_name, timeout=5):
        socket_path = self.socket_path(local_display_name)
        return self.get_server_state(socket_path, timeout)

    def get_server_state(self, socket_path, timeout=5):
        if not os.path.exists(socket_path):
            return self.DEAD
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(timeout)
        try:
            sock.connect(socket_path)
        except socket.error, e:
            err = e.args[0]
            if err==errno.ECONNREFUSED:
                #could be the server is starting up
                return self.UNKNOWN
            if err in (errno.EWOULDBLOCK, errno.ENOENT):
                return self.DEAD
        else:
            sock.close()
            return self.LIVE
        return self.UNKNOWN

    # Same as socket_path, but preps for the server:
    def server_socket_path(self, local_display_name, clobber, wait_for_unknown=0):
        socket_path = self.socket_path(local_display_name)
        if not clobber:
            state = self.server_state(local_display_name)
            counter = 0
            while state==self.UNKNOWN and counter<wait_for_unknown:
                if counter==0:
                    sys.stdout.write("%s is not responding, waiting for it to timeout before clearing it" % socket_path)
                sys.stdout.write(".")
                sys.stdout.flush()
                counter += 1
                time.sleep(1)
                state = self.server_state(local_display_name)
            if counter>0:
                sys.stdout.write("\n")
                sys.stdout.flush()
            if state not in (self.DEAD, self.UNKNOWN):
                raise ServerSockInUse((state, local_display_name))
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        return socket_path

    def sockets(self, check_uid=0):
        results = []
        base = os.path.join(self._sockdir, self._prefix)
        potential_sockets = glob.glob(base + "*")
        for path in sorted(potential_sockets):
            s = os.stat(path)
            if stat.S_ISSOCK(s.st_mode):
                if check_uid>0:
                    if s.st_uid!=check_uid:
                        #socket uid does not match
                        continue
                state = self.get_server_state(path)
                local_display = ":"+path[len(base):]
                results.append((state, local_display))
        return results
