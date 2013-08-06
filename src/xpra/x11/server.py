# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2013 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

# Todo:
#   xsync resize stuff
#   shape?
#   any other interesting metadata? _NET_WM_TYPE, WM_TRANSIENT_FOR, etc.?

import gtk.gdk
import gobject

from xpra.util import AdHocStruct
from xpra.gtk_common.gobject_util import one_arg_signal
from xpra.x11.gtk_x11.wm import Wm
from xpra.x11.gtk_x11.tray import get_tray_window, SystemTray
from xpra.x11.gtk_x11.gdk_bindings import (get_xwindow,                 #@UnresolvedImport
                               add_event_receiver,          #@UnresolvedImport
                               get_children,                #@UnresolvedImport
                               init_x11_filter,             #@UnresolvedImport
                               )
from xpra.x11.bindings.window_bindings import X11WindowBindings #@UnresolvedImport
X11Window = X11WindowBindings()
from xpra.x11.bindings.keyboard_bindings import X11KeyboardBindings #@UnresolvedImport
X11Keyboard = X11KeyboardBindings()
from xpra.x11.gtk_x11.window import OverrideRedirectWindowModel, SystemTrayWindowModel, Unmanageable
from xpra.x11.gtk_x11.error import trap

from xpra.log import Logger
log = Logger()

import xpra
from xpra.os_util import StringIOClass
from xpra.x11.x11_server_base import X11ServerBase
from xpra.net.protocol import zlib_compress, Compressed


class DesktopManager(gtk.Widget):
    def __init__(self):
        gtk.Widget.__init__(self)
        self.set_property("can-focus", True)
        self.set_flags(gtk.NO_WINDOW)
        self._models = {}

    ## For communicating with the main WM:

    def add_window(self, model, x, y, w, h):
        assert self.flags() & gtk.REALIZED
        s = AdHocStruct()
        s.shown = False
        s.geom = [x, y, w, h]
        self._models[model] = s
        model.connect("unmanaged", self._unmanaged)
        model.connect("ownership-election", self._elect_me)
        def new_geom(window_model, *args):
            log("new_geom(%s,%s)", window_model, args)
        model.connect("notify::geometry", new_geom)
        model.ownership_election()

    def window_geometry(self, model):
        return self._models[model].geom

    def show_window(self, model):
        self._models[model].shown = True
        model.ownership_election()
        if model.get_property("iconic"):
            model.set_property("iconic", False)

    def is_shown(self, model):
        return self._models[model].shown

    def configure_window(self, model, x, y, w, h):
        log("DesktopManager.configure_window(%s, %s, %s, %s, %s)", model, x, y, w, h)
        if not self.visible(model):
            self._models[model].shown = True
            model.set_property("iconic", False)
            model.ownership_election()
        self._models[model].geom = [x, y, w, h]
        model.maybe_recalculate_geometry_for(self)

    def hide_window(self, model):
        if not model.get_property("iconic"):
            model.set_property("iconic", True)
        self._models[model].shown = False
        model.ownership_election()

    def visible(self, model):
        return self._models[model].shown

    ## For communicating with WindowModels:

    def _unmanaged(self, model, wm_exiting):
        del self._models[model]

    def _elect_me(self, model):
        if self.visible(model):
            return (1, self)
        else:
            return (-1, self)

    def take_window(self, model, window):
        window.reparent(self.window, 0, 0)

    def window_size(self, model):
        w, h = self._models[model].geom[2:4]
        return w, h

    def window_position(self, model, w, h):
        [x, y, w0, h0] = self._models[model].geom
        if abs(w0-w)>1 or abs(h0-h)>1:
            log.warn("Uh-oh, our size doesn't fit window sizing constraints: "
                     "%sx%s vs %sx%s", w0, h0, w, h)
        return x, y

    def get_transient_for(self, window, window_to_id):
        transient_for = window.get_property("transient-for")
        if transient_for is None:
            return None
        log("found transient_for=%s, xid=%s", transient_for, hex(transient_for.xid))
        #try to find the model for this window:
        for model in self._models.keys():
            log("testing model %s: %s", model, hex(model.client_window.xid))
            if model.client_window.xid==transient_for.xid:
                wid = window_to_id.get(model)
                log("found match, window id=%s", wid)
                return wid
        root = gtk.gdk.get_default_root_window()
        if root.xid==transient_for.xid:
            return -1       #-1 is the backwards compatible marker for root...
        log("not found transient_for=%s, xid=%s", transient_for, hex(transient_for.xid))
        return  None


gobject.type_register(DesktopManager)


class XpraServer(gobject.GObject, X11ServerBase):
    __gsignals__ = {
        "xpra-child-map-event": one_arg_signal,
        "xpra-cursor-event": one_arg_signal,
        }

    def __init__(self):
        gobject.GObject.__init__(self)
        X11ServerBase.__init__(self)

    def init(self, clobber, sockets, opts):
        X11ServerBase.init(self, clobber, sockets, opts)

    def x11_init(self):
        X11ServerBase.x11_init(self)
        init_x11_filter()

        self._has_focus = 0
        # Do this before creating the Wm object, to avoid clobbering its
        # selecting SubstructureRedirect.
        root = gtk.gdk.get_default_root_window()
        root.set_events(root.get_events() | gtk.gdk.SUBSTRUCTURE_MASK)
        root.property_change(gtk.gdk.atom_intern("XPRA_SERVER", False),
                            gtk.gdk.atom_intern("STRING", False),
                            8,
                            gtk.gdk.PROP_MODE_REPLACE,
                            xpra.__version__)
        add_event_receiver(root, self)

        ### Create the WM object
        self._wm = Wm(self.clobber)
        self._wm.connect("new-window", self._new_window_signaled)
        self._wm.connect("bell", self._bell_signaled)
        self._wm.connect("quit", lambda _: self.quit(True))

        self.default_cursor_data = None
        self.last_cursor_serial = None
        self.send_cursor_pending = False
        self.cursor_data = None
        def get_default_cursor():
            self.default_cursor_data = X11Keyboard.get_cursor_image()
            log("get_default_cursor=%s", self.default_cursor_data)
        trap.swallow_synced(get_default_cursor)
        self._wm.enableCursors(True)


    def do_get_info(self, proto, server_sources, window_ids):
        info = X11ServerBase.do_get_info(self, proto, server_sources, window_ids)
        log("do_get_info: adding cursor=%s", self.cursor_data)
        #copy to prevent race:
        cd = self.cursor_data
        if cd is None:
            info["cursor"] = "None"
            return info
        #pixels:
        info["cursor.is_default"] = bool(self.default_cursor_data and len(self.default_cursor_data)>=8 and len(cd)>=8 and cd[7]==cd[7])
        i = 0
        for x in ("x", "y", "width", "height", "xhot", "yhot", "serial", None, "name"):
            if x:
                v = cd[i] or ""
                info["cursor." + x] = v
            i += 1
        return info

    def get_window_info(self, window):
        info = X11ServerBase.get_window_info(self, window)
        info["focused"] = self._window_to_id.get(window, -1)==self._has_focus
        return info


    def set_workarea(self, workarea):
        self._wm.set_workarea(workarea.x, workarea.y, workarea.width, workarea.height)

    def get_transient_for(self, window):
        return self._desktop_manager.get_transient_for(window, self._window_to_id)

    def is_shown(self, window):
        return self._desktop_manager.is_shown(window)

    def cleanup(self, *args):
        if self._tray:
            self._tray.cleanup()
            self._tray = None
        X11ServerBase.cleanup(self)

    def load_existing_windows(self, system_tray):
        # Tray handler:
        self._tray = None
        if system_tray:
            try:
                self._tray = SystemTray()
            except Exception, e:
                log.error("cannot setup tray forwarding: %s", e, exc_info=True)

        ### Create our window managing data structures:
        self._desktop_manager = DesktopManager()
        self._wm.get_property("toplevel").add(self._desktop_manager)
        self._desktop_manager.show_all()

        ### Load in existing windows:
        for window in self._wm.get_property("windows"):
            self._add_new_window(window)

        root = gtk.gdk.get_default_root_window()
        for window in get_children(root):
            if X11Window.is_override_redirect(get_xwindow(window)) and X11Window.is_mapped(get_xwindow(window)):
                self._add_new_or_window(window)

    def send_windows_and_cursors(self, ss):
        # We send the new-window packets sorted by id because this sorts them
        # from oldest to newest -- and preserving window creation order means
        # that the earliest override-redirect windows will be on the bottom,
        # which is usually how things work.  (I don't know that anyone cares
        # about this kind of correctness at all, but hey, doesn't hurt.)
        log("send_windows_and_cursors(%s) will send: %s", ss, self._id_to_window)
        for wid in sorted(self._id_to_window.keys()):
            window = self._id_to_window[wid]
            if not window.is_managed():
                #we keep references to windows that aren't meant to be displayed..
                continue
            #most of the code here is duplicated from the send functions
            #so we can send just to the new client and request damage
            #just for the new client too:
            if window.is_tray():
                #code more or less duplicated from _send_new_tray_window_packet:
                w, h = window.get_property("geometry")[2:4]
                if ss.system_tray:
                    ss.new_tray(wid, window, w, h)
                    ss.damage(wid, window, 0, 0, w, h)
                else:
                    #park it outside the visible area
                    window.move_resize(-200, -200, w, h)
            elif window.is_OR():
                #code more or less duplicated from _send_new_or_window_packet:
                x, y, w, h = window.get_property("geometry")
                wprops = self.client_properties.get("%s|%s" % (wid, ss.uuid))
                ss.new_window("new-override-redirect", wid, window, x, y, w, h, wprops)
                ss.damage(wid, window, 0, 0, w, h)
            else:
                #code more or less duplicated from send_new_window_packet:
                self._desktop_manager.hide_window(window)
                x, y, w, h = self._desktop_manager.window_geometry(window)
                wprops = self.client_properties.get("%s|%s" % (wid, ss.uuid))
                ss.new_window("new-window", wid, window, x, y, w, h, wprops)
        ss.send_cursor(self.cursor_data)



    def _new_window_signaled(self, wm, window):
        self._add_new_window(window)

    def do_xpra_child_map_event(self, event):
        log("do_xpra_child_map_event(%s)", event)
        if event.override_redirect:
            self._add_new_or_window(event.window)

    def _add_new_window_common(self, window):
        wid = X11ServerBase._add_new_window_common(self, window)
        window.managed_connect("client-contents-changed", self._contents_changed)
        window.managed_connect("unmanaged", self._lost_window)
        return wid

    _window_export_properties = ("title", "size-hints", "fullscreen", "maximized")
    def _add_new_window(self, window):
        self._add_new_window_common(window)
        for prop in self._window_export_properties:
            window.connect("notify::%s" % prop, self._update_metadata)
        _, _, w, h, _ = window.get_property("client-window").get_geometry()
        x, y, _, _, _ = window.corral_window.get_geometry()
        log("Discovered new ordinary window: %s (geometry=%s)", window, (x, y, w, h))
        self._desktop_manager.add_window(window, x, y, w, h)
        window.connect("notify::geometry", self._window_resized_signaled)
        self._send_new_window_packet(window)

    def _window_resized_signaled(self, window, *args):
        nw,nh = window.get_property("actual-size")
        geom = self._desktop_manager.window_geometry(window)
        log("XpraServer._window_resized_signaled(%s,%s) actual-size=%sx%s, current geometry=%s", window, args, nw, nh, geom)
        geom[2:4] = nw,nh
        for ss in self._server_sources.values():
            ss.resize_window(self._window_to_id[window], window, nw, nh)

    def _add_new_or_window(self, raw_window):
        xid = get_xwindow(raw_window)
        if raw_window.get_window_type()==gtk.gdk.WINDOW_TEMP:
            #ignoring one of gtk's temporary windows
            #all the windows we manage should be gtk.gdk.WINDOW_FOREIGN
            log("ignoring TEMP window %s", hex(xid))
            return
        WINDOW_MODEL_KEY = "_xpra_window_model_"
        wid = raw_window.get_data(WINDOW_MODEL_KEY)
        window = self._id_to_window.get(wid)
        if window:
            if window.is_managed():
                log("found existing window model %s for %s, will refresh it", type(window), hex(xid))
                geometry = window.get_property("geometry")
                _, _, w, h = geometry
                self._damage(window, 0, 0, w, h, options={"min_delay" : 50})
                return
            log("found existing model %s (but no longer managed!) for %s", type(window), hex(xid))
            #TODO: we could try to re-use the existing model and window ID,
            #but for now it is just easier to create a new one:
            self._lost_window(window)
        tray_window = get_tray_window(raw_window)
        log("Discovered new override-redirect window: %s (tray=%s)", hex(xid), tray_window)
        try:
            if tray_window is not None:
                assert self._tray
                window = SystemTrayWindowModel(raw_window)
                wid = self._add_new_window_common(window)
                raw_window.set_data(WINDOW_MODEL_KEY, wid)
                window.call_setup()
                self._send_new_tray_window_packet(wid, window)
            else:
                window = OverrideRedirectWindowModel(raw_window)
                wid = self._add_new_window_common(window)
                raw_window.set_data(WINDOW_MODEL_KEY, wid)
                window.call_setup()
                window.connect("notify::geometry", self._or_window_geometry_changed)
                self._send_new_or_window_packet(window)
        except Unmanageable, e:
            if window:
                #if window is set, we failed after instantiating it,
                #so we need to fail it manually:
                window.setup_failed(e)
                if window in self._window_to_id:
                    self._lost_window(window, False)
            else:
                log.warn("cannot add %s: %s", hex(xid), e)
            #from now on, we return to the gtk main loop,
            #so we *should* get a signal when the window goes away

    def _or_window_geometry_changed(self, window, pspec=None):
        (x, y, w, h) = window.get_property("geometry")
        if w>=32768 or h>=32768:
            self.error("not sending new invalid window dimensions: %x%s !", w, h)
            return
        log("or_window_geometry_changed: %s (window=%s)", window.get_property("geometry"), window)
        wid = self._window_to_id[window]
        for ss in self._server_sources.values():
            ss.or_window_geometry(wid, window, x, y, w, h)


    def do_xpra_cursor_event(self, event):
        if not self.cursors:
            return
        if self.last_cursor_serial==event.cursor_serial:
            log("ignoring cursor event with the same serial number")
            return
        self.last_cursor_serial = event.cursor_serial
        if not self.send_cursor_pending:
            self.send_cursor_pending = True
            gobject.timeout_add(10, self.send_cursor)

    def send_cursor(self):
        self.send_cursor_pending = False
        self.cursor_data = X11Keyboard.get_cursor_image()
        if self.cursor_data:
            pixels = self.cursor_data[7]
            log("send_cursor() cursor=%s", self.cursor_data[:7]+["%s bytes" % len(pixels)]+self.cursor_data[8:])
            if self.default_cursor_data and pixels==self.default_cursor_data[7]:
                log("send_cursor(): default cursor - clearing it")
                self.cursor_data = None
            elif pixels is not None:
                if len(pixels)<64:
                    self.cursor_data[7] = str(pixels)
                else:
                    self.cursor_data[7] = zlib_compress("cursor", pixels)
        else:
            log("send_cursor() failed to get cursor image")
        for ss in self._server_sources.values():
            ss.send_cursor(self.cursor_data)
        return False

    def _bell_signaled(self, wm, event):
        log("bell signaled on window %s", get_xwindow(event.window))
        if not self.bell:
            return
        wid = 0
        if event.window!=gtk.gdk.get_default_root_window() and event.window_model is not None:
            try:
                wid = self._window_to_id[event.window_model]
            except:
                pass
        log("_bell_signaled(%s,%r) wid=%s", wm, event, wid)
        for ss in self._server_sources.values():
            ss.bell(wid, event.device, event.percent, event.pitch, event.duration, event.bell_class, event.bell_id, event.bell_name or "")



    def _focus(self, server_source, wid, modifiers):
        log("_focus(%s, %s, %s) has_focus=%s", server_source, wid, modifiers, self._has_focus)
        if self._has_focus==wid:
            #nothing to do!
            return
        had_focus = self._id_to_window.get(self._has_focus)
        def reset_focus():
            log("reset_focus() %s / %s had focus", self._has_focus, had_focus)
            self._clear_keys_pressed()
            # FIXME: kind of a hack:
            self._has_focus = 0
            self._wm.get_property("toplevel").reset_x_focus()

        if wid == 0:
            #wid==0 means root window
            return reset_focus()
        window = self._id_to_window.get(wid)
        if not window:
            #not found! (go back to root)
            return reset_focus()
        if window.is_OR():
            log.warn("focus(..) cannot focus OR window: %s", window)
            return
        log("focus(%s, %s, %s) giving focus to %s", server_source, wid, modifiers, window)
        window.give_client_focus()
        if server_source and modifiers is not None:
            server_source.make_keymask_match(modifiers)
        self._has_focus = wid


    def _send_new_window_packet(self, window):
        geometry = self._desktop_manager.window_geometry(window)
        self._do_send_new_window_packet("new-window", window, geometry)

    def _send_new_or_window_packet(self, window, options=None):
        geometry = window.get_property("geometry")
        self._do_send_new_window_packet("new-override-redirect", window, geometry)
        (_, _, w, h) = geometry
        self._damage(window, 0, 0, w, h, options=options)

    def _send_new_tray_window_packet(self, wid, window, options=None):
        (_, _, w, h) = window.get_property("geometry")
        for ss in self._server_sources.values():
            ss.new_tray(wid, window, w, h)
        self._damage(window, 0, 0, w, h, options=options)


    def _update_metadata(self, window, pspec):
        wid = self._window_to_id[window]
        for ss in self._server_sources.values():
            ss.window_metadata(wid, window, pspec.name)

    def _lost_window(self, window, wm_exiting=False):
        wid = self._window_to_id[window]
        for ss in self._server_sources.values():
            ss.lost_window(wid, window)
        del self._window_to_id[window]
        del self._id_to_window[wid]
        for ss in self._server_sources.values():
            ss.remove_window(wid, window)

    def _contents_changed(self, window, event):
        if window.is_OR() or self._desktop_manager.visible(window):
            self._damage(window, event.x, event.y, event.width, event.height)

    def _process_map_window(self, proto, packet):
        wid, x, y, width, height = packet[1:6]
        window = self._id_to_window.get(wid)
        if not window:
            log("cannot map window %s: already removed!", wid)
            return
        assert not window.is_OR()
        self._desktop_manager.configure_window(window, x, y, width, height)
        self._desktop_manager.show_window(window)
        if len(packet)>=7:
            self._set_client_properties(proto, wid, window, packet[6])
        self._damage(window, 0, 0, width, height)


    def _process_unmap_window(self, proto, packet):
        wid = packet[1]
        window = self._id_to_window.get(wid)
        if not window:
            log("cannot map window %s: already removed!", wid)
            return
        assert not window.is_OR()
        for ss in self._server_sources.values():
            ss.unmap_window(wid, window)
        self._desktop_manager.hide_window(window)

    def _process_configure_window(self, proto, packet):
        wid, x, y, w, h = packet[1:6]
        window = self._id_to_window.get(wid)
        if not window:
            log("cannot map window %s: already removed!", wid)
            return
        if window.is_tray():
            assert self._tray
            self._tray.move_resize(window, x, y, w, h)
        else:
            assert not window.is_OR()
            owx, owy, oww, owh = self._desktop_manager.window_geometry(window)
            log("_process_configure_window(%s) old window geometry: %s", packet[1:], (owx, owy, oww, owh))
            self._desktop_manager.configure_window(window, x, y, w, h)
        if len(packet)>=7:
            self._set_client_properties(proto, wid, window, packet[6])
        if self._desktop_manager.visible(window) and (oww!=w or owh!=h):
            self._damage(window, 0, 0, w, h)

    def _process_move_window(self, proto, packet):
        wid, x, y = packet[1:4]
        window = self._id_to_window.get(wid)
        log("_process_move_window(%s)", packet[1:])
        if not window:
            log("cannot move window %s: already removed!", wid)
            return
        assert not window.is_OR()
        _, _, w, h = self._desktop_manager.window_geometry(window)
        self._desktop_manager.configure_window(window, x, y, w, h)

    def _process_resize_window(self, proto, packet):
        wid, w, h = packet[1:4]
        window = self._id_to_window.get(wid)
        log("_process_resize_window(%s)", packet[1:])
        if not window:
            log("cannot resize window %s: already removed!", wid)
            return
        assert not window.is_OR()
        self._cancel_damage(wid, window)
        x, y, _, _ = self._desktop_manager.window_geometry(window)
        self._desktop_manager.configure_window(window, x, y, w, h)
        _, _, ww, wh = self._desktop_manager.window_geometry(window)
        visible = self._desktop_manager.visible(window)
        log("resize_window to %sx%s, desktop manager set it to %sx%s, visible=%s", w, h, ww, wh, visible)
        if visible:
            self._damage(window, 0, 0, w, h)

    def _process_mouse_common(self, proto, wid, pointer, modifiers):
        ss = self._server_sources.get(proto)
        if ss is None:
            return      #gone already!
        ss.make_keymask_match(modifiers)
        window = self._id_to_window.get(wid)
        if not window:
            log("_process_mouse_common() invalid window id: %s", wid)
            return
        def raise_and_move():
            window.raise_window()
            self._move_pointer(pointer)
        trap.swallow_synced(raise_and_move)


    def _process_close_window(self, proto, packet):
        wid = packet[1]
        window = self._id_to_window.get(wid, None)
        if window:
            window.request_close()
        else:
            log("cannot close window %s: it is already gone!", wid)


    def make_screenshot_packet(self):
        try:
            return self.do_make_screenshot_packet()
        except:
            log.error("make_screenshot_packet()", exc_info=True)
            return None

    def do_make_screenshot_packet(self):
        debug = log.debug
        debug("grabbing screenshot")
        regions = []
        OR_regions = []
        for wid in reversed(sorted(self._id_to_window.keys())):
            window = self._id_to_window.get(wid)
            debug("screenshot: window(%s)=%s", wid, window)
            if window is None:
                continue
            if window.is_tray():
                debug("screenshot: skipping tray window %s", wid)
                continue
            if not window.is_managed():
                debug("screenshot: window %s is not/no longer managed", wid)
                continue
            if window.is_OR():
                x, y = window.get_property("geometry")[:2]
            else:
                x, y = self._desktop_manager.window_geometry(window)[:2]
            debug("screenshot: position(%s)=%s,%s", window, x, y)
            w, h = window.get_dimensions()
            debug("screenshot: size(%s)=%sx%s", window, w, h)
            try:
                img = trap.call_synced(window.get_image, 0, 0, w, h, log.info)
            except:
                log.warn("screenshot: window %s could not be captured", wid)
                continue
            if img is None:
                log.warn("screenshot: no pixels for window %s", wid)
                continue
            debug("screenshot: image=%s, size=%s", (img, img.get_size()))
            if img.get_pixel_format() not in ("RGB", "RGBA", "XRGB", "BGRX", "ARGB", "BGRA"):
                log.warn("window pixels for window %s using an unexpected rgb format: %s", wid, img.get_pixel_format())
                continue
            item = (wid, x, y, img)
            if window.is_OR():
                OR_regions.append(item)
            elif self._has_focus==wid:
                #window with focus first (drawn last)
                regions.insert(0, item)
            else:
                regions.append(item)
        all_regions = OR_regions+regions
        if len(all_regions)==0:
            debug("screenshot: no regions found, returning empty 0x0 image!")
            return ["screenshot", 0, 0, "png", -1, ""]
        debug("screenshot: found regions=%s, OR_regions=%s", len(regions), len(OR_regions))
        #in theory, we could run the rest in a non-UI thread since we're done with GTK..
        minx = min([x for (_,x,_,_) in all_regions])
        miny = min([y for (_,_,y,_) in all_regions])
        maxx = max([(x+img.get_width()) for (_,x,_,img) in all_regions])
        maxy = max([(y+img.get_height()) for (_,_,y,img) in all_regions])
        width = maxx-minx
        height = maxy-miny
        debug("screenshot: %sx%s, min x=%s y=%s", width, height, minx, miny)
        from PIL import Image                           #@UnresolvedImport
        screenshot = Image.new("RGBA", (width, height))
        for wid, x, y, img in reversed(all_regions):
            pixel_format = img.get_pixel_format()
            target_format = {
                     "XRGB"   : "RGB",
                     "BGRX"   : "RGB",
                     "BGRA"   : "RGBA"}.get(pixel_format, pixel_format)
            try:
                window_image = Image.frombuffer(target_format, (w, h), img.get_pixels(), "raw", pixel_format, img.get_rowstride())
            except:
                log.warn("failed to parse window pixels in %s format", pixel_format)
                continue
            tx = x-minx
            ty = y-miny
            screenshot.paste(window_image, (tx, ty))
        buf = StringIOClass()
        screenshot.save(buf, "png")
        data = buf.getvalue()
        buf.close()
        packet = ["screenshot", width, height, "png", width*4, Compressed("png", data)]
        debug("screenshot: %sx%s %s", packet[1], packet[2], packet[-1])
        return packet


gobject.type_register(XpraServer)
