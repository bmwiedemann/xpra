# This file is part of Xpra.
# Copyright (C) 2016, 2017 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

XPRA_MDNS_TYPE = '_xpra._tcp.'


def get_listener_class():
    from xpra.os_util import get_util_logger
    log = get_util_logger()
    try:
        from xpra.net.mdns.avahi_listener import AvahiListener
        log("AvahiListener=%s", AvahiListener)
        return AvahiListener
    except ImportError as e:
        log("failed to import AvahiListener: %s", e)
        try:
            from xpra.net.mdns.zeroconf_listener import ZeroconfListener
            log("ZeroconfListener=%s", ZeroconfListener)
            return ZeroconfListener
        except ImportError as e:
            log("failed to import ZeroconfListener: %s", e)
    return None
