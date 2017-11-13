# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2017 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.


from xpra.log import Logger
log = Logger("server", "gtk")
screenlog = Logger("server", "screen")
clipboardlog = Logger("server", "clipboard")
cursorlog = Logger("server", "cursor")
netlog = Logger("network")

from xpra.util import flatten_dict
from xpra.os_util import monotonic_time
from xpra.gtk_common.gobject_compat import import_gdk, import_glib, import_gobject, is_gtk3
from xpra.gtk_common.quit import (gtk_main_quit_really,
                           gtk_main_quit_on_fatal_exceptions_enable,
                           gtk_main_quit_on_fatal_exceptions_disable)
from xpra.server.server_base import ServerBase
from xpra.gtk_common.gtk_util import get_gtk_version_info, gtk_main, display_get_default, get_root_size, get_default_keymap

glib = import_glib()
glib.threads_init()
gobject = import_gobject()
gobject.threads_init()
gdk = import_gdk()


class GTKServerBase(ServerBase):
    """
        This is the base class for servers.
        It provides all the generic functions but is not tied
        to a specific backend (X11 or otherwise).
        See X11ServerBase, XpraServer and XpraX11ShadowServer
    """

    def __init__(self):
        log("GTKServerBase.__init__()")
        self.idle_add = glib.idle_add
        self.timeout_add = glib.timeout_add
        self.source_remove = glib.source_remove
        ServerBase.__init__(self)

    def watch_keymap_changes(self):
        ### Set up keymap change notification:
        keymap = get_default_keymap()
        keymap.connect("keys-changed", self._keys_changed)

    def signal_quit(self, signum, frame):
        gtk_main_quit_on_fatal_exceptions_disable()
        ServerBase.signal_quit(self, signum, frame)

    def do_quit(self):
        log("do_quit: calling gtk_main_quit_really")
        gtk_main_quit_on_fatal_exceptions_disable()
        gtk_main_quit_really()
        log("do_quit: gtk_main_quit_really done")

    def do_run(self):
        gtk_main_quit_on_fatal_exceptions_enable()
        log("do_run() calling %s", gtk_main)
        gtk_main()
        log("do_run() end of gtk.main()")


    def make_hello(self, source):
        capabilities = ServerBase.make_hello(self, source)
        if source.wants_display:
            display = display_get_default()
            max_size = tuple(display.get_maximal_cursor_size())
            capabilities.update({
                "display"               : display.get_name(),
                "cursor.default_size"   : display.get_default_cursor_size(),
                "cursor.max_size"       : max_size,
                })
        if source.wants_versions:
            capabilities.update(flatten_dict(get_gtk_version_info()))
        return capabilities

    def get_ui_info(self, proto, *args):
        info = ServerBase.get_ui_info(self, proto, *args)
        info.setdefault("server", {}).update({
                                              "display"             : display_get_default().get_name(),
                                              "root_window_size"    : self.get_root_window_size(),
                                              })
        info.setdefault("cursor", {}).update(self.get_ui_cursor_info())
        return info

    def send_initial_cursors(self, ss, _sharing=False):
        #cursors: get sizes and send:
        display = display_get_default()
        self.cursor_sizes = display.get_default_cursor_size(), display.get_maximal_cursor_size()
        cursorlog("send_initial_cursors() cursor_sizes=%s", self.cursor_sizes)
        ss.send_cursor()

    def get_ui_cursor_info(self):
        #(from UI thread)
        #now cursor size info:
        display = display_get_default()
        pos = display.get_default_screen().get_root_window().get_pointer()[-3:-1]
        cinfo = {"position" : pos}
        for prop, size in {
            "default" : display.get_default_cursor_size(),
            "max"     : tuple(display.get_maximal_cursor_size()),
            }.items():
            if size is None:
                continue
            cinfo["%s_size" % prop] = size
        return cinfo

    def do_get_info(self, proto, *args):
        start = monotonic_time()
        info = ServerBase.do_get_info(self, proto, *args)
        vi = get_gtk_version_info()
        vi["type"] = "Python/gtk"
        info.setdefault("server", {}).update(vi)
        info.setdefault("features", {})["randr"] = self.randr
        log("GTKServerBase.do_get_info took %ims", (monotonic_time()-start)*1000)
        return info

    def get_root_window_size(self):
        return get_root_size()

    def get_max_screen_size(self):
        max_w, max_h = get_root_size()
        return max_w, max_h

    def configure_best_screen_size(self):
        root_w, root_h = get_root_size()
        return root_w, root_h

    def calculate_workarea(self, maxw, maxh):
        screenlog("calculate_workarea(%s, %s)", maxw, maxh)
        workarea = gdk.Rectangle()
        workarea.width = maxw
        workarea.height = maxh
        for ss in self._server_sources.values():
            screen_sizes = ss.screen_sizes
            screenlog("calculate_workarea() screen_sizes(%s)=%s", ss, screen_sizes)
            if not screen_sizes:
                continue
            for display in screen_sizes:
                #avoid error with old/broken clients:
                if not display or type(display) not in (list, tuple):
                    continue
                #display: [':0.0', 2560, 1600, 677, 423, [['DFP2', 0, 0, 2560, 1600, 646, 406]], 0, 0, 2560, 1574]
                if len(display)>=10:
                    work_x, work_y, work_w, work_h = display[6:10]
                    display_workarea = gdk.Rectangle()
                    display_workarea.x = work_x
                    display_workarea.y = work_y
                    display_workarea.width = work_w
                    display_workarea.height = work_h
                    screenlog("calculate_workarea() found %s for display %s", display_workarea, display[0])
                    if is_gtk3():
                        success, workarea = workarea.intersect(display_workarea)
                        assert success
                    else:
                        workarea = workarea.intersect(display_workarea)
        #sanity checks:
        screenlog("calculate_workarea(%s, %s) workarea=%s", maxw, maxh, workarea)
        if workarea.width==0 or workarea.height==0:
            screenlog.warn("Warning: failed to calculate a common workarea")
            screenlog.warn(" using the full display area: %ix%i", maxw, maxh)
            workarea = gdk.Rectangle()
            workarea.width = maxw
            workarea.height = maxh
        self.set_workarea(workarea)

    def set_workarea(self, workarea):
        pass

    def set_desktop_geometry(self, width, height):
        pass

    def set_dpi(self, xdpi, ydpi):
        pass


    def _move_pointer(self, _wid, pos, *_args):
        x, y = pos
        display = display_get_default()
        display.warp_pointer(display.get_default_screen(), x, y)

    def do_process_button_action(self, *args):
        pass


    def _process_map_window(self, proto, packet):
        log.info("_process_map_window(%s, %s)", proto, packet)

    def _process_unmap_window(self, proto, packet):
        log.info("_process_unmap_window(%s, %s)", proto, packet)

    def _process_close_window(self, proto, packet):
        log.info("_process_close_window(%s, %s)", proto, packet)

    def _process_configure_window(self, proto, packet):
        log.info("_process_configure_window(%s, %s)", proto, packet)


    def send_clipboard_packet(self, *parts):
        #overriden so we can inject the nesting check:
        if self.clipboard_nesting_check("sending", parts[0], self._clipboard_client):
            ServerBase.send_clipboard_packet(self, *parts)

    def process_clipboard_packet(self, ss, packet):
        #overriden so we can inject the nesting check:
        def do_check():
            if self.clipboard_nesting_check("receiving", packet[0], ss):
                ServerBase.process_clipboard_packet(self, ss, packet)
        #the nesting check calls gtk, so we must call it from the main thread:
        self.idle_add(do_check)

    def clipboard_nesting_check(self, action, packet_type, ss):
        clipboardlog("clipboard_nesting_check(%s, %s, %s)", action, packet_type, ss)
        cc = self._clipboard_client
        if cc is None:
            clipboardlog("not %s clipboard packet '%s': no clipboard client", action, packet_type)
            return False
        if not cc.clipboard_enabled:
            clipboardlog("not %s clipboard packet '%s': client %s has clipboard disabled", action, packet_type, cc)
            return False
        from xpra.clipboard.clipboard_base import nesting_check
        if not nesting_check():
            #turn off clipboard at our end:
            self.set_clipboard_enabled_status(ss, False)
            #if we can, tell the client to do the same:
            if ss.clipboard_set_enabled:
                ss.send_clipboard_enabled("probable clipboard loop detected")
            return  False
        return True
