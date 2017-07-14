# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2016-2017 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import gtk.gdk
import gobject
import socket

from xpra.util import updict, log_screen_sizes, envbool
from xpra.os_util import get_generic_os_name
from xpra.platform.paths import get_icon
from xpra.platform.gui import get_wm_name
from xpra.gtk_common.gobject_util import one_arg_signal, no_arg_signal
from xpra.gtk_common.gobject_compat import import_glib
from xpra.gtk_common.error import xswallow
from xpra.gtk_common.gtk_util import get_screen_sizes, get_root_size
from xpra.x11.gtk2.models.model_stub import WindowModelStub
from xpra.x11.gtk2.gdk_bindings import (
                               add_catchall_receiver,       #@UnresolvedImport
                               remove_catchall_receiver,    #@UnresolvedImport
                               add_event_receiver,          #@UnresolvedImport
                               init_x11_filter,             #@UnresolvedImport
                               cleanup_x11_filter,          #@UnresolvedImport
                               cleanup_all_event_receivers  #@UnresolvedImport
                               )
from xpra.x11.bindings.window_bindings import X11WindowBindings #@UnresolvedImport
from xpra.x11.gtk2.window_damage import WindowDamageHandler
X11Window = X11WindowBindings()
from xpra.x11.bindings.keyboard_bindings import X11KeyboardBindings #@UnresolvedImport
X11Keyboard = X11KeyboardBindings()
from xpra.x11.bindings.randr_bindings import RandRBindings #@UnresolvedImport
RandR = RandRBindings()
from xpra.x11.x11_server_base import X11ServerBase, mouselog
from xpra.gtk_common.error import xsync

from xpra.log import Logger
log = Logger("server")
windowlog = Logger("server", "window")
geomlog = Logger("server", "window", "geometry")
traylog = Logger("server", "tray")
settingslog = Logger("x11", "xsettings")
metadatalog = Logger("x11", "metadata")
screenlog = Logger("screen")


glib = import_glib()

FORCE_SCREEN_MISMATCH = envbool("XPRA_FORCE_SCREEN_MISMATCH", False)


class DesktopModel(WindowModelStub, WindowDamageHandler):
    __gsignals__ = {}
    __gsignals__.update(WindowDamageHandler.__common_gsignals__)
    __gsignals__.update({
                         "resized"                  : no_arg_signal,
                         "client-contents-changed"  : one_arg_signal,
                         })

    __gproperties__ = {
        "iconic": (gobject.TYPE_BOOLEAN,
                   "ICCCM 'iconic' state -- any sort of 'not on desktop'.", "",
                   False,
                   gobject.PARAM_READWRITE),
        "focused": (gobject.TYPE_BOOLEAN,
                       "Is the window focused", "",
                       False,
                       gobject.PARAM_READWRITE),
        "size-hints": (gobject.TYPE_PYOBJECT,
                       "Client hints on constraining its size", "",
                       gobject.PARAM_READABLE),
        }


    _property_names         = ["xid", "client-machine", "window-type", "shadow", "size-hints", "class-instance", "focused", "title", "depth", "icon"]
    _dynamic_property_names = ["size-hints"]

    def __init__(self, root):
        WindowDamageHandler.__init__(self, root)
        WindowModelStub.__init__(self)
        self.resize_timer = None
        self.resize_value = None

    def __repr__(self):
        return "DesktopModel(%#x)" % (self.client_window.xid)


    def setup(self):
        WindowDamageHandler.setup(self)
        screen = self.client_window.get_screen()
        screen.connect("size-changed", self._screen_size_changed)
        self.update_size_hints(screen)
        self._depth = X11Window.get_depth(self.client_window.xid)
        self._managed = True
        self._setup_done = True

    def unmanage(self, exiting=False):
        WindowDamageHandler.destroy(self)
        WindowModelStub.unmanage(self, exiting)
        self._managed = False
        rt = self.resize_timer
        if rt:
            self.resize_timer = None
            glib.source_remove(rt)


    def get_geometry(self):
        return self.client_window.get_geometry()[:4]

    def get_dimensions(self):
        return self.client_window.get_geometry()[2:4]

    def uses_XShm(self):
        return bool(self._xshm_handle)


    def get_default_window_icon(self):
        icon_name = get_generic_os_name()+".png"
        return get_icon(icon_name)


    def get_property(self, prop):
        if prop=="xid":
            return self.client_window.xid
        elif prop=="depth":
            return self._depth
        elif prop=="title":
            return get_wm_name() or "xpra desktop"
        elif prop=="client-machine":
            return socket.gethostname()
        elif prop=="window-type":
            return ["NORMAL"]
        elif prop=="shadow":
            return True
        elif prop=="class-instance":
            return ("xpra-desktop", "Xpra-Desktop")
        elif prop=="icon":
            try:
                icon_name = get_wm_name()+".png"
                icon = get_icon(icon_name)
                log("get_icon(%s)=%s", icon_name, icon)
                return icon
            except:
                log("failed to return window icon")
                return None
        else:
            return gobject.GObject.get_property(self, prop)

    def resize(self, w, h):
        geomlog("resize(%i, %i)", w, h)
        if not RandR.has_randr():
            geomlog.error("Error: cannot honour resize request,")
            geomlog.error(" not RandR support on display")
            return
        #FIXME: small race if the user resizes with randr,
        #at the same time as he resizes the window..
        self.resize_value = (w, h)
        if not self.resize_timer:
            self.resize_timer = glib.timeout_add(250, self.do_resize)

    def do_resize(self):
        self.resize_timer = None
        try:
            w, h = self.resize_value
            with xsync:
                screen_sizes = RandR.get_screen_sizes()
                #hack: force mistmatch
                if FORCE_SCREEN_MISMATCH:
                    screen_sizes = [(sw,sh) for sw,sh in screen_sizes if (sw!=w and sh!=h)]
                geomlog("screen sizes=%s", screen_sizes)
                if (w,h) not in screen_sizes:
                    geomlog.warn("Warning: invalid screen size %ix%i", w, h)
                    #find the nearest:
                    distances = {}
                    lower_distances = {}        #for sizes lower than requested
                    for sw, sh in screen_sizes:
                        distance = abs(sw*sh - w*h)
                        distances.setdefault(distance, []).append((sw, sh))
                        if sw<=w and sh<=h:
                            lower_distances.setdefault(distance, []).append((sw, sh))
                    geomlog("lower distances=%s", distances)
                    if lower_distances:
                        nearest = lower_distances[sorted(lower_distances.keys())[0]]
                    else:
                        geomlog("distances=%s", distances)
                        nearest = distances[sorted(distances.keys())[0]]
                    geomlog("nearest matches: %s", nearest)
                    w, h = nearest[0]
                    geomlog.warn(" using %ix%i instead", w, h)
                    if RandR.get_screen_size()==(w,h):
                        #this is already the resolution we have,
                        #but the client has other ideas,
                        #so tell the client we ain't budging:
                        self.emit("resized")
                        return
                RandR.set_screen_size(w, h)
        except Exception as e:
            geomlog("resize(%i, %i)", w, h, exc_info=True)
            geomlog.error("Error: failed to resize desktop display to %ix%i:", w, h)
            geomlog.error(" %s", e)

    def _screen_size_changed(self, screen):
        w, h = screen.get_width(), screen.get_height()
        screenlog("screen size changed: new size %ix%i", w, h)
        screenlog("root window geometry=%s", self.client_window.get_geometry())
        self.invalidate_pixmap()
        self.update_size_hints(screen)
        self.emit("resized")

    def update_size_hints(self, screen):
        w, h = screen.get_width(), screen.get_height()
        screenlog("screen dimensions: %ix%i", w, h)
        if RandR.has_randr():
            #TODO: get all of this from randr:
            #screen_sizes = RandR.get_screen_sizes()
            size_hints = {
                "maximum-size"  : (8192, 4096),
                "minimum-size"  : (640, 640),
                "base-size"     : (640, 640),
                "increment"     : (128, 128),
                "minimum-aspect-ratio"  : (1, 3),
                "maximum-aspect-ratio"  : (3, 1),
                }
        else:
            size = w, h
            size_hints = {
                "maximum-size"  : size,
                "minimum-size"  : size,
                "base-size"     : size,
                }
        self._updateprop("size-hints", size_hints)


    def do_xpra_damage_event(self, event):
        self.emit("client-contents-changed", event)

gobject.type_register(DesktopModel)


"""
    A server class for RFB / VNC-like desktop displays,
    used with the "start-desktop" subcommand.
"""
class XpraDesktopServer(gobject.GObject, X11ServerBase):
    __gsignals__ = {
        "xpra-xkb-event"        : one_arg_signal,
        "xpra-cursor-event"     : one_arg_signal,
        "xpra-motion-event"     : one_arg_signal,
        }

    def __init__(self):
        gobject.GObject.__init__(self)
        X11ServerBase.__init__(self)

    def x11_init(self):
        X11ServerBase.x11_init(self)
        assert init_x11_filter() is True
        display = gtk.gdk.display_get_default()
        screens = display.get_n_screens()
        for n in range(screens):
            screen = display.get_screen(n)
            root = screen.get_root_window()
            add_event_receiver(root, self)
        add_catchall_receiver("xpra-motion-event", self)
        add_catchall_receiver("xpra-xkb-event", self)
        X11Keyboard.selectBellNotification(True)

    def do_cleanup(self, *args):
        X11ServerBase.do_cleanup(self)
        remove_catchall_receiver("xpra-motion-event", self)
        cleanup_x11_filter()
        with xswallow:
            cleanup_all_event_receivers()


    def print_screen_info(self):
        X11ServerBase.print_screen_info(self)
        root_w, root_h = get_root_size()
        sss = get_screen_sizes()
        log_screen_sizes(root_w, root_h, sss)

    def parse_screen_info(self, ss):
        return self.do_parse_screen_info(ss, ss.desktop_mode_size)

    def _screen_size_changed(self, screen):
        #this is not relevant.. don't send it
        pass

    def get_best_screen_size(self, desired_w, desired_h, bigger=False):
        return self.do_get_best_screen_size(desired_w, desired_h, bigger)

    def configure_best_screen_size(self):
        """ for the first client, honour desktop_mode_size if set """
        root_w, root_h = self.root_window.get_size()
        if not self.randr:
            screenlog("configure_best_screen_size() no randr")
            return root_w, root_h
        sss = self._server_sources.values()
        if len(sss)!=1:
            screenlog.info("screen used by %i clients:", len(sss))
            return root_w, root_h
        requested_size = sss[0].desktop_mode_size
        if not requested_size:
            screenlog("configure_best_screen_size() client did not request a specific desktop mode size")
            return root_w, root_h
        w, h = requested_size
        screenlog("client requested desktop mode resolution is %sx%s (current server resolution is %sx%s)", w, h, root_w, root_h)
        if w<=0 or h<=0:
            screenlog("configure_best_screen_size() client requested an invalid desktop mode size: %s", requested_size)
            return root_w, root_h
        return self.set_screen_size(w, h)

    def set_desktop_geometry_attributes(self, w, h):
        #geometry is not synced with the client's for desktop servers
        pass


    def get_server_mode(self):
        return "X11 desktop"

    def make_hello(self, source):
        capabilities = X11ServerBase.make_hello(self, source)
        if source.wants_features:
            capabilities.update({
                                 "pointer.grabs"    : True,
                                 "desktop"          : True,
                                 })
            updict(capabilities, "window", {
                "decorations"            : True,
                "resize-counter"         : True,
                "configure.skip-geometry": True,
                "configure.pointer"      : True,
                "states"                 : ["iconified", "focused"],
                })
        return capabilities


    def load_existing_windows(self):
        #at present, just one  window is forwarded:
        #the root window covering the whole display
        display = gtk.gdk.display_get_default()
        screens = display.get_n_screens()
        for n in range(screens):
            screen = display.get_screen(n)
            root = screen.get_root_window()
            model = DesktopModel(root)
            model.setup()
            windowlog("adding root window model %s", model)
            X11ServerBase._add_new_window_common(self, model)
            model.managed_connect("client-contents-changed", self._contents_changed)
            model.managed_connect("resized", self._window_resized_signaled)


    def _window_resized_signaled(self, window, *args):
        #the vfb has been resized
        wid = self._window_to_id[window]
        x, y, w, h = window.get_geometry()
        geomlog("window_resized_signaled(%s) geometry=%s", window, (x, y, w, h))
        for ss in self._server_sources.values():
            ss.resize_window(wid, window, w, h)
            ss.damage(wid, window, 0, 0, w, h)


    def send_initial_windows(self, ss, sharing=False):
        # We send the new-window packets sorted by id because this sorts them
        # from oldest to newest -- and preserving window creation order means
        # that the earliest override-redirect windows will be on the bottom,
        # which is usually how things work.  (I don't know that anyone cares
        # about this kind of correctness at all, but hey, doesn't hurt.)
        windowlog("send_initial_windows(%s, %s) will send: %s", ss, sharing, self._id_to_window)
        for wid,window in sorted(self._id_to_window.items()):
            x, y, w, h = window.get_geometry()
            wprops = self.client_properties.get("%s|%s" % (wid, ss.uuid))
            ss.new_window("new-window", wid, window, x, y, w, h, wprops)
            ss.damage(wid, window, 0, 0, w, h)


    def _lost_window(self, window, wm_exiting=False):
        pass

    def _contents_changed(self, window, event):
        log("contents changed on %s: %s", window, event)
        self._damage(window, event.x, event.y, event.width, event.height)


    def _set_window_state(self, proto, wid, window, new_window_state):
        if not new_window_state:
            return []
        metadatalog("set_window_state%s", (wid, window, new_window_state))
        changes = []
        #boolean: but not a wm_state and renamed in the model... (iconic vs iconified!)
        iconified = new_window_state.get("iconified")
        if iconified is not None:
            if window._updateprop("iconic", iconified):
                changes.append("iconified")
        focused = new_window_state.get("focused")
        if focused is not None:
            if window._updateprop("focused", focused):
                changes.append("focused")
        return changes


    def get_screen_number(self, wid):
        model = self._id_to_window.get(wid)
        return model.client_window.get_screen().get_number()

    def get_window_position(self, window):
        #we export the whole desktop as a window:
        return 0, 0


    def _process_map_window(self, proto, packet):
        wid, x, y, w, h = packet[1:6]
        window = self._id_to_window.get(wid)
        if not window:
            windowlog("cannot map window %s: already removed!", wid)
            return
        geomlog("client mapped window %s - %s, at: %s", wid, window, (x, y, w, h))
        if len(packet)>=8:
            self._set_window_state(proto, wid, window, packet[7])
        if len(packet)>=7:
            self._set_client_properties(proto, wid, window, packet[6])
        self._window_mapped_at(proto, wid, window, (x, y, w, h))
        self._damage(window, 0, 0, w, h)


    def _process_unmap_window(self, proto, packet):
        wid = packet[1]
        window = self._id_to_window.get(wid)
        if not window:
            log("cannot map window %s: already removed!", wid)
            return
        if len(packet)>=4:
            #optional window_state added in 0.15 to update flags
            #during iconification events:
            self._set_window_state(proto, wid, window, packet[3])
        assert not window.is_OR()
        self._window_mapped_at(proto, wid, window, None)
        #TODO: handle inconification?
        #iconified = len(packet)>=3 and bool(packet[2])


    def _process_configure_window(self, proto, packet):
        wid, x, y, w, h = packet[1:6]
        if len(packet)>=13:
            pwid = packet[10]
            pointer = packet[11]
            modifiers = packet[12]
            self._update_modifiers(proto, wid, modifiers)
            self._process_mouse_common(proto, pwid, pointer)
        #some "configure-window" packets are only meant for metadata updates:
        skip_geometry = len(packet)>=10 and packet[9]
        window = self._id_to_window.get(wid)
        if not window:
            geomlog("cannot map window %s: already removed!", wid)
            return
        damage = False
        if len(packet)>=9:
            changes = self._set_window_state(proto, wid, window, packet[8])
            damage = len(changes)>0
        if not skip_geometry:
            owx, owy, oww, owh = window.get_geometry()
            geomlog("_process_configure_window(%s) old window geometry: %s", packet[1:], (owx, owy, oww, owh))
            window.resize(w, h)
        if len(packet)>=7:
            cprops = packet[6]
            if cprops:
                metadatalog("window client properties updates: %s", cprops)
                self._set_client_properties(proto, wid, window, cprops)
        self._window_mapped_at(proto, wid, window, (x, y, w, h))
        if damage:
            self._damage(window, 0, 0, w, h)


    def _move_pointer(self, wid, pos, *args):
        if wid>=0:
            window = self._id_to_window.get(wid)
            if not window:
                mouselog("_move_pointer(%s, %s) invalid window id", wid, pos)
            else:
                #TODO: just like shadow server, adjust for window position
                pass
        X11ServerBase._move_pointer(self, wid, pos, *args)


    def _process_close_window(self, proto, packet):
        #disconnect?
        pass


    def _process_desktop_size(self, proto, packet):
        pass
    def calculate_desktops(self):
        pass
    def calculate_workarea(self, w, h):
        pass


    def make_dbus_server(self):
        from xpra.x11.dbus.x11_dbus_server import X11_DBUS_Server
        self.dbus_server = X11_DBUS_Server(self, os.environ.get("DISPLAY", "").lstrip(":"))


    def do_make_screenshot_packet(self):
        log("grabbing screenshot")
        regions = []
        offset_x, offset_y = 0, 0
        for wid in reversed(sorted(self._id_to_window.keys())):
            window = self._id_to_window.get(wid)
            log("screenshot: window(%s)=%s", wid, window)
            if window is None:
                continue
            if not window.is_managed():
                log("screenshot: window %s is not/no longer managed", wid)
                continue
            x, y, w, h = window.get_geometry()
            log("screenshot: geometry(%s)=%s", window, (x, y, w, h))
            try:
                with xsync:
                    img = window.get_image(0, 0, w, h)
            except:
                log.warn("screenshot: window %s could not be captured", wid)
                continue
            if img is None:
                log.warn("screenshot: no pixels for window %s", wid)
                continue
            log("screenshot: image=%s, size=%s", img, img.get_size())
            if img.get_pixel_format() not in ("RGB", "RGBA", "XRGB", "BGRX", "ARGB", "BGRA"):
                log.warn("window pixels for window %s using an unexpected rgb format: %s", wid, img.get_pixel_format())
                continue
            regions.append((wid, offset_x+x, offset_y+y, img))
            #tile them horizontally:
            offset_x += w
            offset_y += 0
        return self.make_screenshot_packet_from_regions(regions)


gobject.type_register(XpraDesktopServer)
