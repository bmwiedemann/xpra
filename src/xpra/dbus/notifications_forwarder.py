# This file is part of Xpra.
# Copyright (C) 2011-2018 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import dbus.service

from xpra.dbus.helper import dbus_to_native
from xpra.util import envbool, csv
from xpra.log import Logger
log = Logger("dbus", "notify")

BUS_NAME="org.freedesktop.Notifications"
BUS_PATH="/org/freedesktop/Notifications"

CAPABILITIES = ["body", "icon-static"]
if envbool("XPRA_NOTIFICATIONS_ACTIONS", True):
    CAPABILITIES += ["actions", "action-icons"]


"""
We register this class as handling notifications on the session dbus,
optionally replacing an existing instance if one exists.

The generalized callback signatures are:
 notify_callback(dbus_id, nid, app_name, replaces_nid, app_icon, summary, body, actions, hints, expire_timeout)
 close_callback(nid)
"""
class DBUSNotificationsForwarder(dbus.service.Object):

    def __init__(self, bus, notify_callback=None, close_callback=None):
        self.bus = bus
        self.notify_callback = notify_callback
        self.close_callback = close_callback
        self.active_notifications = set()
        self.counter = 0
        self.dbus_id = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
        bus_name = dbus.service.BusName(BUS_NAME, bus=bus)
        dbus.service.Object.__init__(self, bus_name, BUS_PATH)

    @dbus.service.method(BUS_NAME, in_signature='susssasa{sv}i', out_signature='u')
    def Notify(self, app_name, replaces_nid, app_icon, summary, body, actions, hints, expire_timeout):
        log("Notify%s", (app_name, replaces_nid, app_icon, summary, body, actions, hints, expire_timeout))
        if replaces_nid==0:
            self.counter += 1
            nid = self.counter
        else:
            nid = replaces_nid
        log("Notify%s counter=%i, callback=%s", (app_name, replaces_nid, app_icon, summary, body, actions, hints, expire_timeout), self.counter, self.notify_callback)
        self.active_notifications.add(nid)
        if self.notify_callback:
            try:
                actions = tuple(str(x) for x in actions)
                hints = dbus_to_native(hints)
                args = self.dbus_id, int(nid), str(app_name), int(replaces_nid), str(app_icon), str(summary), str(body), actions, hints, int(expire_timeout)
            except Exception as e:
                log.error("Error: failed to parse Notify arguments:")
                log.error(" %s", e)
            try:
                self.notify_callback(*args)
            except Exception as e:
                log.error("Error calling notification handler", exc_info=True)
        log("Notify returning %s", nid)
        return nid

    @dbus.service.method(BUS_NAME, out_signature='ssss')
    def GetServerInformation(self):
        #name, vendor, version, spec-version
        from xpra import __version__
        v = ["xpra-notification-proxy", "xpra", __version__, "0.9"]
        log("GetServerInformation()=%s", v)
        return v

    @dbus.service.method(BUS_NAME, out_signature='as')
    def GetCapabilities(self):
        log("GetCapabilities()=%s", csv(CAPABILITIES))
        return CAPABILITIES

    @dbus.service.method(BUS_NAME, in_signature='u')
    def CloseNotification(self, nid):
        log("CloseNotification(%s) callback=%s", nid, self.close_callback)
        try:
            self.active_notifications.remove(nid)
        except KeyError:
            return
        if self.close_callback:
            self.close_callback(nid)
        self.NotificationClosed(nid, 3)     #3="The notification was closed by a call to CloseNotification"

    def is_notification_active(self, nid):
        return nid in self.active_notifications

    @dbus.service.signal(BUS_NAME, signature='uu')
    def NotificationClosed(self, nid, reason):
        pass

    @dbus.service.signal(BUS_NAME, signature='us')
    def ActionInvoked(self, nid, action_key):
        pass
    

    def release(self):
        try:
            self.bus.release_name(BUS_NAME)
        except Exception as e:
            log.error("failed to release dbus notification forwarder: %s", e)

    def __str__(self):
        return  "DBUS-NotificationsForwarder(%s)" % BUS_NAME


def register(notify_callback=None, close_callback=None, replace=False):
    from xpra.dbus.common import init_session_bus
    bus = init_session_bus()
    flags = dbus.bus.NAME_FLAG_DO_NOT_QUEUE
    if replace:
        flags |= dbus.bus.NAME_FLAG_REPLACE_EXISTING
    request = bus.request_name(BUS_NAME, flags)
    if request==dbus.bus.REQUEST_NAME_REPLY_EXISTS:
        raise Exception("the name '%s' is already claimed on the session bus" % BUS_NAME)
    log("notifications: bus name '%s', request=%s" % (BUS_NAME, request))
    return DBUSNotificationsForwarder(bus, notify_callback, close_callback)


def main():
    register()
    from xpra.gtk_common.gobject_compat import import_glib
    glib = import_glib()
    mainloop = glib.MainLoop()
    mainloop.run()

if __name__ == "__main__":
    main()
