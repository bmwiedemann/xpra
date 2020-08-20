#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2011-2020 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import unittest

from xpra.net.net_util import (
    get_info, get_interfaces, get_interfaces_addresses, #get_interface,
    get_gateways, get_bind_IPs, do_get_bind_ifacemask,
    get_ssl_info, get_interface,
    if_nametoindex, if_indextoname, get_iface,
    get_free_tcp_port,
    )


class TestVersionUtilModule(unittest.TestCase):

    def test_tcp_port(self):
        assert get_free_tcp_port()>0

    def test_netifaces(self):
        ifaces = get_interfaces()
        if not ifaces:
            return
        for iface in ifaces:
            do_get_bind_ifacemask(iface)
            if if_nametoindex:
                try:
                    i = if_nametoindex(iface)
                except Exception:
                    pass
                else:
                    if if_indextoname:
                        assert if_indextoname(i)==iface
            get_iface(iface)
        ia = get_interfaces_addresses()
        assert ia
        #for iface, address in ia.items():
        #    iface2 = get_interface(address)
        #    assert iface2==iface, "expected %s but got %s" % (iface, iface2)
        get_gateways()
        get_bind_IPs()
        get_ssl_info()
        get_info()

        if if_indextoname:
            assert if_indextoname(-1) is None

        def invalid_iface(s):
            v = get_iface(s)
            if v is not None:
                raise Exception("invalid IP '%s' should not return interface '%s'" % (s, v))
        invalid_iface(None)
        invalid_iface("")
        invalid_iface("%")
        invalid_iface(":")
        invalid_iface("INVALIDHOSTNAME")
        invalid_iface("10.0.0")
        get_iface("localhost")

        assert get_interface("invalid") is None

    def test_ssl_info(self):
        assert get_ssl_info(True)


def main():
    unittest.main()

if __name__ == '__main__':
    main()
