#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2017-2020 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import sys
from gi.repository import GLib, Gtk

from xpra.gtk_common.gobject_compat import register_os_signals
from xpra.os_util import monotonic_time
from xpra.util import AdHocStruct, typedict
from xpra.gtk_common.gtk_util import (
    add_close_accel, scaled_image, get_icon_pixbuf,
    get_pixbuf_from_data, TableBuilder,
    )
from xpra.log import Logger, enable_debug_for

log = Logger("util")


_instance = None
def getServerCommandsWindow(client):
    global _instance
    if _instance is None:
        _instance = ServerCommandsWindow(client)
    return _instance


class ServerCommandsWindow:

    def __init__(self, client):
        assert client
        self.client = client
        self.populate_timer = None
        self.commands_info = {}
        self.table = None
        self.window = Gtk.Window()
        self.window.set_border_width(20)
        self.window.connect("delete-event", self.close)
        self.window.set_default_size(400, 150)
        self.window.set_title("Server Commands")

        icon_pixbuf = get_icon_pixbuf("list.png")
        if icon_pixbuf:
            self.window.set_icon(icon_pixbuf)
        self.window.set_position(Gtk.WindowPosition.CENTER)

        vbox = Gtk.VBox(False, 0)
        vbox.set_spacing(10)

        self.alignment = Gtk.Alignment(xalign=0.5, yalign=0.5, xscale=1.0, yscale=1.0)
        vbox.pack_start(self.alignment, expand=True, fill=True)

        # Buttons:
        hbox = Gtk.HBox(False, 20)
        vbox.pack_start(hbox)
        def btn(label, tooltip, callback, icon_name=None):
            b = self.btn(label, tooltip, callback, icon_name)
            hbox.pack_start(b)
        if self.client.server_start_new_commands:
            btn("Start New", "Run a command on the server", self.client.show_start_new_command, "forward.png")
        btn("Close", "", self.close, "quit.png")

        add_close_accel(self.window, self.close)
        vbox.show_all()
        self.window.vbox = vbox
        self.window.add(vbox)

    def btn(self, label, tooltip, callback, icon_name=None):
        btn = Gtk.Button(label)
        settings = btn.get_settings()
        settings.set_property('gtk-button-images', True)
        btn.set_tooltip_text(tooltip)
        btn.connect("clicked", callback)
        icon = get_icon_pixbuf(icon_name)
        if icon:
            btn.set_image(scaled_image(icon, 24))
        return btn

    def populate_table(self):
        commands_info = typedict(self.client.server_last_info).dictget("commands", {})
        if self.commands_info!=commands_info and commands_info:
            log("populate_table() new commands_info=%s", commands_info)
            self.commands_info = commands_info
            if self.table:
                self.alignment.remove(self.table)
            tb = TableBuilder(rows=1, columns=2, row_spacings=15)
            self.table = tb.get_table()
            headers = [Gtk.Label(""), Gtk.Label("PID"), Gtk.Label("Command"), Gtk.Label("Exit Code")]
            if self.client.server_commands_signals:
                headers.append(Gtk.Label("Send Signal"))
            tb.add_row(*headers)
            for procinfo in self.commands_info.values():
                if not isinstance(procinfo, dict):
                    continue
                #some records aren't procinfos:
                pi = typedict(procinfo)
                command = pi.strtupleget("command")
                pid = pi.intget("pid", 0)
                returncode = pi.intget("returncode", None)
                if pid>0 and command:
                    cmd_str = " ".join(command)
                    rstr = ""
                    if returncode is not None:
                        rstr = "%s" % returncode
                    #find the windows matching this pid
                    windows = ()
                    from xpra.client import mixin_features
                    if mixin_features.windows:
                        windows = tuple(w for w in self.client._id_to_window.values() if getattr(w, "_metadata", {}).get("pid")==pid)
                        log("windows matching pid=%i: %s", pid, windows)
                    icon = Gtk.Label()
                    if windows:
                        try:
                            icons = tuple(getattr(w, "_current_icon", None) for w in windows)
                            icons = tuple(x for x in icons if x is not None)
                            log("icons: %s", icons)
                            if icons:
                                from PIL import Image
                                img = icons[0].resize((24, 24), Image.ANTIALIAS)
                                has_alpha = img.mode=="RGBA"
                                width, height = img.size
                                rowstride = width * (3+int(has_alpha))
                                pixbuf = get_pixbuf_from_data(img.tobytes(), has_alpha, width, height, rowstride)
                                icon = Gtk.Image()
                                icon.set_from_pixbuf(pixbuf)
                        except Exception:
                            log("failed to get window icon", exc_info=True)
                    items = [icon, Gtk.Label("%s" % pid), Gtk.Label(cmd_str), Gtk.Label(rstr)]
                    if self.client.server_commands_signals:
                        if returncode is None:
                            items.append(self.signal_button(pid))
                        else:
                            items.append(Gtk.Label(""))
                    tb.add_row(*items)
            self.alignment.add(self.table)
            self.table.show_all()
        self.client.send_info_request()
        return True

    def signal_button(self, pid):
        hbox = Gtk.HBox()
        combo = Gtk.ComboBoxText()
        for x in self.client.server_commands_signals:
            combo.append_text(x)
        def send(*_args):
            a = combo.get_active()
            if a>=0:
                signame = self.client.server_commands_signals[a]
                self.client.send("command-signal", pid, signame)
        b = self.btn("Send", None, send, "forward.png")
        hbox.pack_start(combo)
        hbox.pack_start(b)
        return hbox

    def schedule_timer(self):
        if not self.populate_timer:
            self.populate_table()
            self.populate_timer = GLib.timeout_add(1000, self.populate_table)

    def cancel_timer(self):
        if self.populate_timer:
            GLib.source_remove(self.populate_timer)
            self.populate_timer = None


    def show(self):
        log("show()")
        self.window.show_all()
        self.window.present()
        self.schedule_timer()

    def close(self, *args):
        log("close%s", args)
        self.window.hide()
        self.cancel_timer()
        return True

    def destroy(self, *args):
        log("destroy%s", args)
        self.cancel_timer()
        if self.window:
            self.window.destroy()
            self.window = None


    def run(self):
        log("run()")
        Gtk.main()
        log("run() Gtk.main done")

    def quit(self, *args):
        log("quit%s", args)
        self.destroy()
        Gtk.main_quit()


def main(): # pragma: no cover
    from xpra.platform import program_context
    from xpra.platform.gui import ready as gui_ready, init as gui_init
    gui_init()
    with program_context("Start-New-Command", "Start New Command"):
        #logging init:
        if "-v" in sys.argv:
            enable_debug_for("util")

        client = AdHocStruct()
        client.server_last_info_time = monotonic_time()
        commands_info = {
            0: {'returncode': None, 'name': 'xterm', 'pid': 542, 'dead': False, 'ignore': True, 'command': ('xterm',), 'forget': False},
            'start-child'              : (),
            'start-new'                : True,
            'start-after-connect-done' : True,
            'start'                    : ('xterm',),
            'start-after-connect'      : (),
            'start-child-on-connect'   : (),
            'exit-with-children'       : False,
            'start-child-after-connect': (),
            'start-on-connect'         : (),
            }
        client.server_last_info = {"commands" : commands_info}
        client.server_start_new_commands = True
        client.server_commands_signals = ("SIGINT", "SIGTERM", "SIGUSR1")
        def noop(*_args):
            pass
        client.send_info_request = noop
        client.send = noop
        window1 = AdHocStruct()
        window1._metadata = {"pid" : 542}
        client._id_to_window = {
            1 : window1
            }
        def show_start_new_command(*_args):
            from xpra.client.gtk_base.start_new_command import getStartNewCommand
            getStartNewCommand(None).show()
        client.show_start_new_command = show_start_new_command

        app = ServerCommandsWindow(client)
        app.close = app.quit
        register_os_signals(app.quit)
        try:
            gui_ready()
            app.show()
            app.run()
        except KeyboardInterrupt:
            pass
        return 0


if __name__ == "__main__":  # pragma: no cover
    v = main()
    sys.exit(v)
