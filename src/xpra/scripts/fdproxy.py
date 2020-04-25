# This file is part of Xpra.
# Copyright (C) 2012-2020 Antoine Martin <antoine@xpra.org>
# Copyright (C) 2010 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import signal
import threading

from xpra.net.bytestreams import untilConcludes
from xpra.util import repr_ellipsized, envint, envbool
from xpra.os_util import hexstr, force_quit
from xpra.log import Logger

log = Logger("proxy")

SHOW_DATA = envbool("XPRA_PROXY_SHOW_DATA")
PROXY_BUFFER_SIZE = envint("XPRA_PROXY_BUFFER_SIZE", 65536)


def noretry(_e):
    return False

class XpraProxy:
    """
        This is the proxy command that runs
        when one uses the hidden subcommand
        "xpra _proxy" or when forwarding data
        using the tcp-proxy option.
        It simply forwards stdin/stdout to
        the server socket.
    """

    def __repr__(self):
        return "XpraProxy(%s: %s - %s)" % (self._name, self._client_conn, self._server_conn)

    def __init__(self, name, client_conn, server_conn, quit_cb=None):
        self._name = name
        self._client_conn = client_conn
        self._server_conn = server_conn
        self._quit_cb = quit_cb or self.do_quit
        self._closed = False
        self._to_client = threading.Thread(target=self._to_client_loop)
        self._to_client.setDaemon(True)
        self._to_server = threading.Thread(target=self._to_server_loop)
        self._to_server.setDaemon(True)
        self._exit_code = 0
        signal.signal(signal.SIGINT, self.signal_quit)
        signal.signal(signal.SIGTERM, self.signal_quit)

    def start_threads(self):
        self._to_client.start()
        self._to_server.start()

    def run(self):
        log("XpraProxy.run() %s", self._name)
        self.start_threads()
        self._to_client.join()
        self._to_server.join()
        log("XpraProxy.run() %s: all the threads have ended, calling quit() to close the connections", self._name)
        self.quit()

    def _to_client_loop(self):
        self._copy_loop("<-server %s" % self._name, self._server_conn, self._client_conn)
        self._closed = True

    def _to_server_loop(self):
        self._copy_loop("->server %s" % self._name, self._client_conn, self._server_conn)
        self._closed = True

    def _copy_loop(self, log_name, from_conn, to_conn):
        #log("XpraProxy._copy_loop(%s, %s, %s)", log_name, from_conn, to_conn)
        try:
            while not self._closed:
                log("%s: waiting for data", log_name)
                buf = untilConcludes(self.is_active, noretry, from_conn.read, PROXY_BUFFER_SIZE)
                if not buf:
                    log("%s: connection lost", log_name)
                    return
                if SHOW_DATA:
                    log("%s: %s bytes: %s", log_name, len(buf), repr_ellipsized(buf))
                    log("%s:           %s", log_name, repr_ellipsized(hexstr(buf)))
                while buf and not self._closed:
                    log("%s: writing %s bytes", log_name, len(buf))
                    written = untilConcludes(self.is_active, noretry, to_conn.write, buf)
                    buf = buf[written:]
                    log("%s: written %s bytes", log_name, written)
            log("%s copy loop ended", log_name)
        except Exception as e:
            log("%s: %s", log_name, e)
        finally:
            self.quit()

    def is_active(self):
        return not self._closed

    def signal_quit(self, signum, _frame=None):
        self._exit_code = 128+signum
        self.quit()

    def quit(self, *args):
        log("XpraProxy.quit(%s) %s: closing connections", args,  self._name)
        self._closed = True
        quit_cb = self._quit_cb
        try:
            self._client_conn.close()
        except OSError:
            pass
        try:
            self._server_conn.close()
        except OSError:
            pass
        if quit_cb:
            self._quit_cb = None
            quit_cb(self)

    def do_quit(self, _proxy):
        force_quit(self._exit_code)
