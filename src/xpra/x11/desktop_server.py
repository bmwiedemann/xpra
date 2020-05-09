# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2016-2019 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import socket
from gi.repository import GObject, Gdk, Gio

from xpra.os_util import get_generic_os_name, load_binary_file
from xpra.util import updict, log_screen_sizes, envbool, csv
from xpra.platform.paths import get_icon, get_icon_filename
from xpra.platform.gui import get_wm_name
from xpra.server import server_features
from xpra.gtk_common.gobject_util import one_arg_signal, no_arg_signal
from xpra.gtk_common.error import XError
from xpra.gtk_common.gtk_util import get_screen_sizes, get_root_size
from xpra.x11.models.model_stub import WindowModelStub
from xpra.x11.gtk_x11.gdk_bindings import (
    add_catchall_receiver, remove_catchall_receiver,
    add_event_receiver,          #@UnresolvedImport
   )
from xpra.x11.bindings.window_bindings import X11WindowBindings #@UnresolvedImport
from xpra.x11.xroot_props import XRootPropWatcher
from xpra.x11.gtk_x11.window_damage import WindowDamageHandler
from xpra.x11.bindings.keyboard_bindings import X11KeyboardBindings #@UnresolvedImport
from xpra.x11.bindings.randr_bindings import RandRBindings #@UnresolvedImport
from xpra.x11.x11_server_base import X11ServerBase, mouselog
from xpra.gtk_common.error import xsync, xlog
from xpra.log import Logger

log = Logger("server")

X11Window = X11WindowBindings()
X11Keyboard = X11KeyboardBindings()
RandR = RandRBindings()

windowlog = Logger("server", "window")
geomlog = Logger("server", "window", "geometry")
settingslog = Logger("x11", "xsettings")
metadatalog = Logger("x11", "metadata")
screenlog = Logger("screen")
iconlog = Logger("icon")

MODIFY_GSETTINGS = envbool("XPRA_MODIFY_GSETTINGS", True)


class DesktopModel(WindowModelStub, WindowDamageHandler):
    __gsignals__ = {}
    __gsignals__.update(WindowDamageHandler.__common_gsignals__)
    __gsignals__.update({
                         "resized"                  : no_arg_signal,
                         "client-contents-changed"  : one_arg_signal,
                         })

    __gproperties__ = {
        "iconic": (GObject.TYPE_BOOLEAN,
                   "ICCCM 'iconic' state -- any sort of 'not on desktop'.", "",
                   False,
                   GObject.ParamFlags.READWRITE),
        "focused": (GObject.TYPE_BOOLEAN,
                       "Is the window focused", "",
                       False,
                       GObject.ParamFlags.READWRITE),
        "size-hints": (GObject.TYPE_PYOBJECT,
                       "Client hints on constraining its size", "",
                       GObject.ParamFlags.READABLE),
        "wm-name": (GObject.TYPE_PYOBJECT,
                       "The name of the window manager or session manager", "",
                       GObject.ParamFlags.READABLE),
        "icons": (GObject.TYPE_PYOBJECT,
                       "The icon of the window manager or session manager", "",
                       GObject.ParamFlags.READABLE),
        }


    _property_names         = [
        "xid", "client-machine", "window-type",
        "shadow", "size-hints", "class-instance",
        "focused", "title", "depth", "icons",
        ]
    _dynamic_property_names = ["size-hints", "title", "icons"]

    def __init__(self, root, resize_exact=False):
        WindowDamageHandler.__init__(self, root)
        WindowModelStub.__init__(self)
        self.root_prop_watcher = XRootPropWatcher(["WINDOW_MANAGER", "_NET_SUPPORTING_WM_CHECK"], root)
        self.root_prop_watcher.connect("root-prop-changed", self.root_prop_changed)
        self.update_icon()
        self.resize_exact = resize_exact

    def __repr__(self):
        return "DesktopModel(%#x)" % self.client_window.get_xid()


    def setup(self):
        WindowDamageHandler.setup(self)
        screen = self.client_window.get_screen()
        screen.connect("size-changed", self._screen_size_changed)
        self.update_size_hints(screen)
        self._depth = X11Window.get_depth(self.client_window.get_xid())
        self._managed = True
        self._setup_done = True

    def unmanage(self, exiting=False):
        WindowDamageHandler.destroy(self)
        WindowModelStub.unmanage(self, exiting)
        self._managed = False
        rpw = self.root_prop_watcher
        if rpw:
            self.root_prop_watcher = None
            rpw.cleanup()

    def root_prop_changed(self, watcher, prop):
        iconlog("root_prop_changed(%s, %s)", watcher, prop)
        if self.update_wm_name():
            self.update_icon()

    def update_wm_name(self):
        try:
            wm_name = get_wm_name()     #pylint: disable=assignment-from-none
        except Exception:
            wm_name = ""
        iconlog("update_wm_name() wm-name=%s", wm_name)
        return self._updateprop("wm-name", wm_name)

    def update_icon(self):
        icons = None
        try:
            wm_name = get_wm_name()     #pylint: disable=assignment-from-none
            if not wm_name:
                return
            icon_name = get_icon_filename(wm_name.lower()+".png")
            from PIL import Image
            img = Image.open(icon_name)
            iconlog("Image(%s)=%s", icon_name, img)
            if img:
                icon_data = load_binary_file(icon_name)
                assert icon_data
                w, h = img.size
                icon = (w, h, "png", icon_data)
                icons = (icon,)
        except Exception:
            iconlog("failed to return window icon", exc_info=True)
        return self._updateprop("icons", icons)


    def get_geometry(self):
        return self.client_window.get_geometry()[:4]

    def get_dimensions(self):
        return self.client_window.get_geometry()[2:4]

    def uses_XShm(self):
        return bool(self._xshm_handle)


    def get_default_window_icon(self, _size):
        icon_name = get_generic_os_name()+".png"
        icon = get_icon(icon_name)
        if not icon:
            return None
        return icon.get_width(), icon.get_height(), "RGBA", icon.get_pixels()


    def get_property(self, prop):
        if prop=="xid":
            return int(self.client_window.get_xid())
        if prop=="depth":
            return self._depth
        if prop=="title":
            return get_wm_name() or "xpra desktop"
        if prop=="client-machine":
            return socket.gethostname()
        if prop=="window-type":
            return ["NORMAL"]
        if prop=="shadow":
            return True
        if prop=="class-instance":
            return ("xpra-desktop", "Xpra-Desktop")
        return GObject.GObject.get_property(self, prop)

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
        size_hints = {}
        def use_fixed_size():
            size = w, h
            size_hints.update({
                "maximum-size"  : size,
                "minimum-size"  : size,
                "base-size"     : size,
                })
        if RandR.has_randr():
            if self.resize_exact:
                #assume resize_exact is enabled
                #no size restrictions
                size_hints = {}
            else:
                try:
                    with xsync:
                        screen_sizes = RandR.get_xrr_screen_sizes()
                except XError:
                    screenlog("failed to query screen sizes", exc_info=True)
                else:
                    if not screen_sizes:
                        use_fixed_size()
                    else:
                        #find the maximum size supported:
                        max_size = {}
                        for tw, th in screen_sizes:
                            max_size[tw*th] = (tw, th)
                        max_pixels = sorted(max_size.keys())[-1]
                        size_hints["maximum-size"] = max_size[max_pixels]
                        #find the best increment we can use:
                        inc_hits = {}
                        #we should also figure out what the potential increments are,
                        #rather than hardcoding them here:
                        INC_VALUES = (16, 32, 64, 128, 256)
                        for inc in INC_VALUES:
                            hits = 0
                            for tsize in screen_sizes:
                                tw, th = tsize
                                if (tw+inc, th+inc) in screen_sizes:
                                    hits += 1
                            inc_hits[inc] = hits
                        screenlog("size increment hits: %s", inc_hits)
                        max_hits = max(inc_hits.values())
                        if max_hits>16:
                            #find the first increment value matching the max hits
                            for inc in INC_VALUES:
                                if inc_hits[inc]==max_hits:
                                    break
                            #TODO: also get these values from the screen sizes:
                            size_hints.update({
                                "base-size"             : (640, 640),
                                "minimum-size"          : (640, 640),
                                "increment"             : (128, 128),
                                "minimum-aspect-ratio"  : (1, 3),
                                "maximum-aspect-ratio"  : (3, 1),
                                })
        else:
            use_fixed_size()
        screenlog("size-hints=%s", size_hints)
        self._updateprop("size-hints", size_hints)


    def do_xpra_damage_event(self, event):
        self.emit("client-contents-changed", event)

GObject.type_register(DesktopModel)



DESKTOPSERVER_BASES = [GObject.GObject]
if server_features.rfb:
    from xpra.server.rfb.rfb_server import RFBServer
    DESKTOPSERVER_BASES.append(RFBServer)
DESKTOPSERVER_BASES.append(X11ServerBase)
DESKTOPSERVER_BASES = tuple(DESKTOPSERVER_BASES)
DesktopServerBaseClass = type('DesktopServerBaseClass', DESKTOPSERVER_BASES, {})
log("DesktopServerBaseClass%s", DESKTOPSERVER_BASES)


"""
    A server class for RFB / VNC-like desktop displays,
    used with the "start-desktop" subcommand.
"""
class XpraDesktopServer(DesktopServerBaseClass):
    __gsignals__ = {
        "xpra-xkb-event"        : one_arg_signal,
        "xpra-cursor-event"     : one_arg_signal,
        "xpra-motion-event"     : one_arg_signal,
        }

    def __init__(self, clobber=False):
        X11ServerBase.__init__(self, clobber)
        for c in DESKTOPSERVER_BASES:
            if c!=X11ServerBase:
                c.__init__(self)
        self.session_type = "desktop"
        self.resize_timer = None
        self.resize_value = None
        self.gsettings_modified = {}

    def init(self, opts):
        for c in DESKTOPSERVER_BASES:
            if c!=GObject.GObject:
                c.init(self, opts)

    def server_init(self):
        X11ServerBase.server_init(self)
        if self.randr:
            from xpra.x11.vfb_util import set_initial_resolution, DEFAULT_DESKTOP_VFB_RESOLUTION
            with xlog:
                set_initial_resolution(DEFAULT_DESKTOP_VFB_RESOLUTION)

    def x11_init(self):
        X11ServerBase.x11_init(self)
        display = Gdk.Display.get_default()
        screens = display.get_n_screens()
        for n in range(screens):
            screen = display.get_screen(n)
            root = screen.get_root_window()
            add_event_receiver(root, self)
        add_catchall_receiver("xpra-motion-event", self)
        add_catchall_receiver("xpra-xkb-event", self)
        with xlog:
            X11Keyboard.selectBellNotification(True)
        if MODIFY_GSETTINGS:
            self.modify_gsettings()

    def modify_gsettings(self):
        #try to suspend animations:
        self.gsettings_modified = self.do_modify_gsettings({
            "org.mate.interface" : ("gtk-enable-animations", "enable-animations"),
            "org.gnome.desktop.interface" : ("enable-animations",),
            "com.deepin.wrap.gnome.desktop.interface" : ("enable-animations",),
            })

    def do_modify_gsettings(self, defs, value=False):
        modified = {}
        schemas = Gio.Settings.list_schemas()
        for schema, attributes in defs.items():
            if schema not in schemas:
                continue
            try:
                s = Gio.Settings.new(schema)
                restore = []
                for attribute in attributes:
                    v = s.get_boolean(attribute)
                    if v:
                        s.set_boolean(attribute, value)
                        restore.append(attribute)
                if restore:
                    modified[schema] = restore
            except Exception as e:
                log("error accessing schema '%s' and attributes %s", schema, attributes, exc_info=True)
                log.error("Error accessing schema '%s' and attributes %s:", schema, csv(attributes))
                log.error(" %s", e)
        return modified

    def do_cleanup(self):
        self.cancel_resize_timer()
        remove_catchall_receiver("xpra-motion-event", self)
        X11ServerBase.do_cleanup(self)
        if MODIFY_GSETTINGS:
            self.restore_gsettings()

    def restore_gsettings(self):
        self.do_modify_gsettings(self.gsettings_modified, True)

    def notify_dpi_warning(self, body):
        pass

    def print_screen_info(self):
        super().print_screen_info()
        root_w, root_h = get_root_size()
        log.info(" initial resolution: %ix%i", root_w, root_h)
        sss = get_screen_sizes()
        log_screen_sizes(root_w, root_h, sss)

    def parse_screen_info(self, ss):
        return self.do_parse_screen_info(ss, ss.desktop_mode_size)

    def do_screen_changed(self, screen):
        #this is not relevant.. don't send it
        pass

    def get_best_screen_size(self, desired_w, desired_h, bigger=False):
        return self.do_get_best_screen_size(desired_w, desired_h, bigger)

    def configure_best_screen_size(self):
        """ for the first client, honour desktop_mode_size if set """
        root_w, root_h = self.root_window.get_geometry()[2:4]
        if not self.randr:
            screenlog("configure_best_screen_size() no randr")
            return root_w, root_h
        sss = tuple(self._server_sources.values())
        if len(sss)!=1:
            screenlog.info("screen used by %i clients:", len(sss))
            return root_w, root_h
        ss = sss[0]
        requested_size = ss.desktop_mode_size
        if not requested_size:
            screenlog("configure_best_screen_size() client did not request a specific desktop mode size")
            return root_w, root_h
        w, h = requested_size
        screenlog("client requested desktop mode resolution is %sx%s (current server resolution is %sx%s)",
                  w, h, root_w, root_h)
        if w<=0 or h<=0 or w>=32768 or h>=32768:
            screenlog("configure_best_screen_size() client requested an invalid desktop mode size: %s", requested_size)
            return root_w, root_h
        return self.set_screen_size(w, h, ss.screen_resize_bigger)

    def cancel_resize_timer(self):
        rt = self.resize_timer
        if rt:
            self.resize_timer = None
            self.source_remove(rt)

    def resize(self, w, h):
        geomlog("resize(%i, %i)", w, h)
        if not RandR.has_randr():
            geomlog.error("Error: cannot honour resize request,")
            geomlog.error(" no RandR support on this display")
            return
        #FIXME: small race if the user resizes with randr,
        #at the same time as he resizes the window..
        self.resize_value = (w, h)
        if not self.resize_timer:
            self.resize_timer = self.timeout_add(250, self.do_resize)

    def do_resize(self):
        self.resize_timer = None
        rw, rh = self.resize_value
        try:
            with xsync:
                ow, oh = RandR.get_screen_size()
            w, h = self.set_screen_size(rw, rh, False)
            if (ow, oh) == (w, h):
                #this is already the resolution we have,
                #but the client has other ideas,
                #so tell the client we ain't budging:
                for win in self._window_to_id.keys():
                    win.emit("resized")
        except Exception as e:
            geomlog("do_resize() %ix%i", rw, rh, exc_info=True)
            geomlog.error("Error: failed to resize desktop display to %ix%i:", rw, rh)
            geomlog.error(" %s", e)


    def set_desktop_geometry_attributes(self, w, h):
        #geometry is not synced with the client's for desktop servers
        pass


    def get_server_mode(self):
        return "GTK3 X11 desktop"

    def make_hello(self, source):
        capabilities = super().make_hello(source)
        if source.wants_features:
            capabilities.update({
                                 "pointer.grabs"    : True,
                                 "desktop"          : True,
                                 })
            updict(capabilities, "window", {
                "decorations"            : True,
                "states"                 : ["iconified", "focused"],
                })
            capabilities["screen_sizes"] = get_screen_sizes()
        return capabilities


    def load_existing_windows(self):
        #at present, just one  window is forwarded:
        #the root window covering the whole display
        display = Gdk.Display.get_default()
        screens = display.get_n_screens()
        with xsync:
            for n in range(screens):
                screen = display.get_screen(n)
                root = screen.get_root_window()
                model = DesktopModel(root, self.randr_exact_size)
                model.setup()
                windowlog("adding root window model %s", model)
                super()._add_new_window_common(model)
                model.managed_connect("client-contents-changed", self._contents_changed)
                model.managed_connect("resized", self._window_resized_signaled)


    def _window_resized_signaled(self, window):
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
            wprops = self.client_properties.get(wid, {}).get(ss.uuid)
            ss.new_window("new-window", wid, window, x, y, w, h, wprops)
            ss.damage(wid, window, 0, 0, w, h)


    def _lost_window(self, window, wm_exiting=False):
        pass

    def _contents_changed(self, window, event):
        log("contents changed on %s: %s", window, event)
        self.refresh_window_area(window, event.x, event.y, event.width, event.height)


    def _set_window_state(self, proto, wid, window, new_window_state):
        if not new_window_state:
            return []
        metadatalog("set_window_state%s", (proto, wid, window, new_window_state))
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

    def get_window_position(self, _window):
        #we export the whole desktop as a window:
        return 0, 0


    def _process_map_window(self, proto, packet):
        wid, x, y, w, h = packet[1:6]
        window = self._id_to_window.get(wid)
        if not window:
            windowlog("cannot map window %s: already removed!", wid)
            return
        geomlog("client mapped window %s - %s, at: %s", wid, window, (x, y, w, h))
        self._window_mapped_at(proto, wid, window, (x, y, w, h))
        if len(packet)>=8:
            self._set_window_state(proto, wid, window, packet[7])
        if len(packet)>=7:
            self._set_client_properties(proto, wid, window, packet[6])
        self.refresh_window_area(window, 0, 0, w, h)


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
        self._window_mapped_at(proto, wid, window)
        #TODO: handle inconification?
        #iconified = len(packet)>=3 and bool(packet[2])


    def _process_configure_window(self, proto, packet):
        wid, x, y, w, h = packet[1:6]
        if len(packet)>=13 and server_features.input_devices and not self.readonly:
            pwid = packet[10]
            pointer = packet[11]
            modifiers = packet[12]
            if self._process_mouse_common(proto, pwid, pointer):
                self._update_modifiers(proto, wid, modifiers)
        #some "configure-window" packets are only meant for metadata updates:
        skip_geometry = len(packet)>=10 and packet[9]
        window = self._id_to_window.get(wid)
        if not window:
            geomlog("cannot map window %s: already removed!", wid)
            return
        damage = False
        if len(packet)>=9:
            damage = bool(self._set_window_state(proto, wid, window, packet[8]))
        if not skip_geometry and not self.readonly:
            owx, owy, oww, owh = window.get_geometry()
            geomlog("_process_configure_window(%s) old window geometry: %s", packet[1:], (owx, owy, oww, owh))
            if oww!=w or owh!=h:
                self.resize(w, h)
        if len(packet)>=7:
            cprops = packet[6]
            if cprops:
                metadatalog("window client properties updates: %s", cprops)
                self._set_client_properties(proto, wid, window, cprops)
        self._window_mapped_at(proto, wid, window, (x, y, w, h))
        if damage:
            self.refresh_window_area(window, 0, 0, w, h)


    def _adjust_pointer(self, proto, wid, pointer):
        window = self._id_to_window.get(wid)
        if not window:
            self.suspend_cursor(proto)
            return None
        pointer = super()._adjust_pointer(proto, wid, pointer)
        #maybe the pointer is off-screen:
        ww, wh = window.get_dimensions()
        x, y = pointer[:2]
        if x<0 or x>=ww or y<0 or y>=wh:
            self.suspend_cursor(proto)
            return None
        self.restore_cursor(proto)
        return pointer

    def _move_pointer(self, wid, pos, *args):
        if wid>=0:
            window = self._id_to_window.get(wid)
            if not window:
                mouselog("_move_pointer(%s, %s) invalid window id", wid, pos)
            else:
                #TODO: just like shadow server, adjust for window position
                pass
        with xsync:
            X11ServerBase._move_pointer(self, wid, pos, -1, *args)


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


    def show_all_windows(self):
        log.warn("Warning: show_all_windows not implemented for desktop server")


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
            except Exception:
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

GObject.type_register(XpraDesktopServer)
