# This file is part of Xpra.
# Copyright (C) 2016 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import posixpath
import urllib

from xpra.log import Logger
log = Logger("network", "websocket")

from xpra.util import AdHocStruct
from xpra.net.bytestreams import SocketConnection
from websockify.websocket import WebSocketRequestHandler

WEBSOCKET_TCP_NODELAY = int(os.environ.get("WEBSOCKET_TCP_NODELAY", "1"))
WEBSOCKET_TCP_KEEPALIVE = int(os.environ.get("WEBSOCKET_TCP_KEEPALIVE", "1"))
WEBSOCKET_DEBUG = os.environ.get("XPRA_WEBSOCKET_DEBUG", "0")=="1"


class WSRequestHandler(WebSocketRequestHandler):

    disable_nagle_algorithm = WEBSOCKET_TCP_NODELAY
    keep_alive = WEBSOCKET_TCP_KEEPALIVE

    def __init__(self, sock, addr, new_websocket_client, web_root="/usr/share/xpra/www/"):
        self.web_root = web_root
        self._new_websocket_client = new_websocket_client
        server = AdHocStruct()
        server.logger = log
        server.run_once = True
        server.verbose = WEBSOCKET_DEBUG
        WebSocketRequestHandler.__init__(self, sock, addr, server)

    def new_websocket_client(self):
        self._new_websocket_client(self)

    def translate_path(self, path):
        #code duplicated from superclass since we can't easily inject the web_root..
        s = path
        # abandon query parameters
        path = path.split('?',1)[0]
        path = path.split('#',1)[0]
        # Don't forget explicit trailing slash when normalizing. Issue17324
        trailing_slash = path.rstrip().endswith('/')
        path = posixpath.normpath(urllib.unquote(path))
        words = path.split('/')
        words = filter(None, words)
        path = self.web_root
        for word in words:
            word = os.path.splitdrive(word)[1]
            word = os.path.split(word)[1]
            if word in (os.curdir, os.pardir):
                continue
            path = os.path.join(path, word)
        if trailing_slash:
            path += '/'
        log("translate_path(%s)=%s", s, path)
        return path


    def log_error(self, fmt, *args):
        #don't log 404s at error level:
        if len(args)==2 and args[0]==404:
            log(fmt, *args)
        else:
            log.error(fmt, *args)

    def log_message(self, fmt, *args):
        #log.warn("%s", (fmt, args))
        log(fmt, *args)

    def print_traffic(self, token="."):
        """ Show traffic flow mode. """
        if self.traffic:
            log(token)


class WebSocketConnection(SocketConnection):

    def __init__(self, socket, local, remote, target, info, ws_handler):
        SocketConnection.__init__(self, socket, local, remote, target, info)
        self.protocol_type = "websocket"
        self.ws_handler = ws_handler

    def read(self, n):
        while self.is_active():
            bufs, closed_string = self.ws_handler.recv_frames()
            if closed_string:
                self.active = False
            if len(bufs) == 1:
                self.input_bytecount += len(bufs[0])
                return bufs[0]
            elif len(bufs) > 1:
                buf = b''.join(bufs)
                self.input_bytecount += len(buf)
                return buf

    def write(self, buf):
        self.ws_handler.send_frames([buf])
        self.output_bytecount += len(buf)
        return len(buf)
