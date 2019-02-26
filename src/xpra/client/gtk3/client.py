# This file is part of Xpra.
# Copyright (C) 2013-2019 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import gi

gi.require_version('Gdk', '3.0')                #@UndefinedVariable
from gi.repository import GObject               #@UnresolvedImport
from gi.repository import Gdk                   #@UnresolvedImport
from gi.repository import GLib                  #@UnresolvedImport

from xpra.os_util import OSX, POSIX
from xpra.gtk_common.gobject_compat import register_os_signals
from xpra.client.gtk_base.gtk_client_base import GTKXpraClient
from xpra.client.gtk3.client_window import ClientWindow
from xpra.platform.gui import get_xdpi, get_ydpi


class XpraClient(GTKXpraClient):

    ClientWindowClass = ClientWindow

    def __repr__(self):
        return "gtk3.client"

    def client_type(self):
        return "Python/GTK3"

    def client_toolkit(self):
        return "GTK3"


    def install_signal_handlers(self):
        register_os_signals(self.handle_app_signal)

    def get_notifier_classes(self):
        ncs = GTKXpraClient.get_notifier_classes(self)
        if not OSX:
            try:
                from xpra.client.gtk3.gtk3_notifier import GTK3_Notifier
                ncs.append(GTK3_Notifier)
            except Exception as e:
                from xpra.log import Logger
                log = Logger("gtk", "client")
                log.warn("Warning: failed to load the GTK3 notification class")
                log.warn(" %s", e)
        return ncs


    def get_xdpi(self):
        xdpi = get_xdpi()
        if xdpi>0:
            return xdpi
        return Gdk.Screen.get_default().get_resolution()

    def get_ydpi(self):
        ydpi = get_ydpi()
        if ydpi>0:
            return ydpi
        return Gdk.Screen.get_default().get_resolution()


    def get_tray_menu_helper_class(self):
        from xpra.client.gtk3.tray_menu import GTK3TrayMenu
        return GTK3TrayMenu


    def get_mouse_position(self):
        #with GTK3, we can get None values!
        p = self.get_root_window().get_pointer()[-3:-1]
        return self.sp(p[0] or 0, p[1] or 0)


GObject.type_register(XpraClient)
