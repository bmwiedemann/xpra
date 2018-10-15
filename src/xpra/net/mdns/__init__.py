# This file is part of Xpra.
# Copyright (C) 2016-2018 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

XPRA_MDNS_TYPE = "_xpra._tcp."
RFB_MDNS_TYPE = "_rfb._tcp"

from xpra.util import envbool

AVAHI = envbool("XPRA_AVAHI", True)
ZEROCONF = envbool("XPRA_ZEROCONF", True)
SHOW_INTERFACE = envbool("XPRA_MDNS_SHOW_INTERFACE", True)            #publishes the name of the interface we broadcast from


def get_listener_class():
    from xpra.os_util import get_util_logger
    log = get_util_logger()
    log("mdns.get_listener_class() AVAHI=%s, ZEROCONF=%s", AVAHI, ZEROCONF)
    if AVAHI:
        try:
            from xpra.net.mdns.avahi_listener import AvahiListener
            log("AvahiListener=%s", AvahiListener)
            return AvahiListener
        except ImportError as e:
            log("failed to import AvahiListener: %s", e)
    if ZEROCONF:
        try:
            from xpra.net.mdns.zeroconf_listener import ZeroconfListener
            log("ZeroconfListener=%s", ZeroconfListener)
            return ZeroconfListener
        except ImportError as e:
            log("failed to import ZeroconfListener: %s", e)
    return None
