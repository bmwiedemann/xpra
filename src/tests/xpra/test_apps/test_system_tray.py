#!/usr/bin/env python

import sys
import gtk


class StatusIcon:
    def __init__(self, name="test", tooltip="StatusIcon Example"):
        self.name = name
        self.statusicon = gtk.StatusIcon()
        self.counter = 0
        self.statusicon.set_name(name)
        self.statusicon.set_from_stock(gtk.STOCK_HOME)
        self.statusicon.connect("popup-menu", self.popup_menu)
        self.statusicon.connect("activate", self.activate)
        self.statusicon.set_tooltip(tooltip)
        #build list of stock icons:
        self.stock = {}
        try:
            from xpra.client.notifications.dbus_notifier import DBUS_Notifier
            self.notifier = DBUS_Notifier(self.notification_closed, self.notification_action)
            self.notifier.app_name_format = "%s"
        except Exception as e:
            import traceback
            traceback.print_stack()
            print("Failed to instantiate the dbus notifier: %s" % e)
        self.nid = 1
        for x in dir(gtk):
            if x.startswith("STOCK_"):
                self.stock[x[len("STOCK_"):]] = getattr(gtk, x)

    def activate(self, *_args):
        self.counter += 1
        name, stock = list(self.stock.items())[self.counter % len(self.stock)]
        print("setting tray icon to: %s" % name)
        self.statusicon.set_from_stock(stock)

    def popup_menu(self, icon, button, time):
        menu = gtk.Menu()
        quit_menu = gtk.MenuItem("Quit")
        quit_menu.connect("activate", gtk.main_quit)
        menu.append(quit_menu)
        notify_menu = gtk.MenuItem("Send Notification")
        notify_menu.connect("activate", self.notify)
        menu.append(notify_menu)
        menu.show_all()
        menu.popup(None, None, gtk.status_icon_position_menu, button, time, self.statusicon)

    def notification_closed(self, nid, reason, text):
        print("notification_closed(%i, %i, %s)" % (nid, reason, text))

    def notification_action(self, nid, action):
        print("notification_action(%s, %s)" % (nid, action))

    def notify(self, *_args):
        actions = ["0", "Hello", "1", "Goodbye"]
        hints = {}
        self.notifier.show_notify("dbus-id", None, self.nid, self.name, 0, "", "Notification Summary", "Notification Body", actions, hints, 60*1000, "")
        self.nid += 1


def main():
    name = "test"
    if len(sys.argv)>=2:
        name = sys.argv[1]
    tooltip = "StatusIcon Example"
    if len(sys.argv)>=3:
        tooltip = sys.argv[2]
    from xpra.platform import program_context
    with program_context(name, name):
        StatusIcon(name, tooltip)
        gtk.main()


if __name__ == "__main__":
    main()
