# This file is part of Xpra.
# Copyright (C) 2016 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.


import sys
from xpra.net.vsock import bind_vsocket, CID_HOST, PORT_ANY       #@UnresolvedImport

from xpra.log import Logger
log = Logger("network")


def main(args):
    cid = CID_HOST
    port = PORT_ANY
    if len(args)>0:
        cid = int(args[0])
    if len(args)>1:
        port = int(args[1])
    sock = bind_vsocket(cid=cid, port=port)
    log("sock=%s", sock)
    sock.listen(1)
    log("listening")
    while True:
        connection, client_address = sock.accept()
        log("new connection! %s", (connection, client_address))
        data = connection.recv(1024)
        log("got data: %s" % data)
        connection.send("hello")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
