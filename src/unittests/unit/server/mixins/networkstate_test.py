#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2018-2019 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import unittest

from xpra.util import AdHocStruct
from unit.server.mixins.servermixintest_util import ServerMixinTest


class NetworkStateMixinTest(ServerMixinTest):

    def test_networkstate(self):
        from xpra.server.mixins.networkstate_server import NetworkStateServer
        from xpra.server.source.networkstate_mixin import NetworkStateMixin
        opts = AdHocStruct()
        opts.pings = 1
        opts.bandwidth_limit = "10Mbps"
        self._test_mixin_class(NetworkStateServer, opts, {}, NetworkStateMixin)
        self.handle_packet(("ping", 10))
        self.handle_packet(("ping", -1000))
        self.handle_packet(("ping_echo", 10, 500, 500, 600, 10))
        for v in (None, "foo", 1, 2.0, [], (), set()):
            try:
                self.handle_packet(("connection-data", v))
            except TypeError:
                pass
            else:
                raise Exception("should not allow %s (%s) as connection-data" % (v, type(v)))
        self.handle_packet(("connection-data", {}))
        for v in (None, "foo", 2.0, [], (), set()):
            try:
                self.handle_packet(("bandwidth-limit", v))
            except TypeError:
                pass
            else:
                raise Exception("should not allow %s (%s) as connection-data" % (v, type(v)))
        self.handle_packet(("bandwidth-limit", 10))


def main():
    unittest.main()


if __name__ == '__main__':
    main()
