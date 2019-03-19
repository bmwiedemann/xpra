# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2018 Antoine Martin <antoine@xpra.org>
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.os_util import bytestostr, strtobytes, hexstr
from xpra.util import nonl, typedict, envbool, iround
from xpra.gtk_common.error import xswallow, xsync
from xpra.x11.x11_server_core import X11ServerCore, XTestPointerDevice
from xpra.x11.bindings.keyboard_bindings import X11KeyboardBindings #@UnresolvedImport
from xpra.log import Logger

log = Logger("x11", "server")
mouselog = Logger("x11", "server", "mouse")
screenlog = Logger("server", "screen")

X11Keyboard = X11KeyboardBindings()

SCALED_FONT_ANTIALIAS = envbool("XPRA_SCALED_FONT_ANTIALIAS", False)


def _get_antialias_hintstyle(antialias):
    hintstyle = antialias.strget("hintstyle", "").lower()
    if hintstyle in ("hintnone", "hintslight", "hintmedium", "hintfull"):
        #X11 clients can give us what we need directly:
        return hintstyle
    #win32 style contrast value:
    contrast = antialias.intget("contrast", -1)
    if contrast>1600:
        return "hintfull"
    if contrast>1000:
        return "hintmedium"
    if contrast>0:
        return "hintslight"
    return "hintnone"


class X11ServerBase(X11ServerCore):
    """
        Base class for X11 servers,
        adds uinput, icc and xsettings synchronization to the X11ServerCore class
        (see XpraServer or DesktopServer for actual implementations)
    """

    def __init__(self):
        X11ServerCore.__init__(self)
        self._default_xsettings = {}
        self._settings = {}
        self.double_click_time = 0
        self.double_click_distance = 0
        self.dpi = 0
        self.default_dpi = 0
        self._xsettings_manager = None
        self._xsettings_enabled = False

    def do_init(self, opts):
        X11ServerCore.do_init(self, opts)
        self._xsettings_enabled = opts.xsettings
        if self._xsettings_enabled:
            from xpra.x11.xsettings import XSettingsHelper
            self._default_xsettings = XSettingsHelper().get_settings()
            log("_default_xsettings=%s", self._default_xsettings)
            self.init_all_server_settings()

    def configure_best_screen_size(self):
        root_w, root_h = X11ServerCore.configure_best_screen_size(self)
        if self.touchpad_device:
            self.touchpad_device.root_w = root_w
            self.touchpad_device.root_h = root_h
        return root_w, root_h

    def last_client_exited(self):
        self.reset_settings()
        X11ServerCore.last_client_exited(self)

    def init_virtual_devices(self, devices):
        #(this runs in the main thread - before the main loop starts)
        #for the time being, we only use the pointer if there is one:
        pointer = devices.get("pointer")
        touchpad = devices.get("touchpad")
        mouselog("init_virtual_devices(%s) got pointer=%s, touchpad=%s", devices, pointer, touchpad)
        self.input_devices = "xtest"
        if pointer:
            uinput_device = pointer.get("uinput")
            device_path = pointer.get("device")
            if uinput_device:
                from xpra.x11.uinput_device import UInputPointerDevice
                self.input_devices = "uinput"
                self.pointer_device = UInputPointerDevice(uinput_device, device_path)
                self.verify_uinput_pointer_device()
        if self.input_devices=="uinput" and touchpad:
            uinput_device = touchpad.get("uinput")
            device_path = touchpad.get("device")
            if uinput_device:
                from xpra.x11.uinput_device import UInputTouchpadDevice
                root_w, root_h = self.get_root_window_size()
                self.touchpad_device = UInputTouchpadDevice(uinput_device, device_path, root_w, root_h)
        try:
            mouselog.info("pointer device emulation using %s", str(self.pointer_device).replace("PointerDevice", ""))
        except Exception as e:
            mouselog("cannot get pointer device class from %s: %s", self.pointer_device, e)

    def verify_uinput_pointer_device(self):
        xtest = XTestPointerDevice()
        ox, oy = 100, 100
        with xsync:
            xtest.move_pointer(0, ox, oy)
        nx, ny = 200, 200
        self.pointer_device.move_pointer(0, nx, ny)
        def verify_uinput_moved():
            pos = None  #@UnusedVariable
            with xswallow:
                pos = X11Keyboard.query_pointer()
                mouselog("X11Keyboard.query_pointer=%s", pos)
            if pos==(ox, oy):
                mouselog.warn("Warning: %s failed verification", self.pointer_device)
                mouselog.warn(" expected pointer at %s, now at %s", (nx, ny), pos)
                mouselog.warn(" usign XTest fallback")
                self.pointer_device = xtest
                self.input_devices = "xtest"
        self.timeout_add(1000, verify_uinput_moved)


    def dpi_changed(self):
        #re-apply the same settings, which will apply the new dpi override to it:
        self.update_server_settings()


    def set_icc_profile(self):
        ui_clients = [s for s in self._server_sources.values() if s.ui_client]
        if len(ui_clients)!=1:
            screenlog("%i UI clients, resetting ICC profile to default", len(ui_clients))
            self.reset_icc_profile()
            return
        icc = typedict(ui_clients[0].icc)
        data = None
        for x in ("data", "icc-data", "icc-profile"):
            data = icc.strget(x)
            if data:
                break
        if not data:
            screenlog("no icc data found in %s", icc)
            self.reset_icc_profile()
            return
        screenlog("set_icc_profile() icc data for %s: %s (%i bytes)",
                  ui_clients[0], hexstr(data or ""), len(data or ""))
        from xpra.x11.gtk_x11.prop import prop_set
        #each CARD32 contains just one 8-bit value - don't ask me why
        def o(x):
            try:
                return ord(x)
            except:
                return x
        prop_set(self.root_window, "_ICC_PROFILE", ["u32"], [o(x) for x in data])
        prop_set(self.root_window, "_ICC_PROFILE_IN_X_VERSION", "u32", 0*100+4) #0.4 -> 0*100+4*1

    def reset_icc_profile(self):
        screenlog("reset_icc_profile()")
        from xpra.x11.gtk_x11.prop import prop_del
        prop_del(self.root_window, "_ICC_PROFILE")
        prop_del(self.root_window, "_ICC_PROFILE_IN_X_VERSION")


    def reset_settings(self):
        if not self._xsettings_enabled:
            return
        log("resetting xsettings to: %s", self._default_xsettings)
        self.set_xsettings(self._default_xsettings or (0, ()))

    def set_xsettings(self, v):
        if not self._xsettings_enabled:
            return
        log("set_xsettings(%s)", v)
        with xsync:
            if self._xsettings_manager is None:
                from xpra.x11.xsettings import XSettingsManager
                self._xsettings_manager = XSettingsManager()
            self._xsettings_manager.set_settings(v)

    def init_all_server_settings(self):
        log("init_all_server_settings() dpi=%i, default_dpi=%i", self.dpi, self.default_dpi)
        #almost like update_all, except we use the default_dpi,
        #since this is called before the first client connects
        self.do_update_server_settings({
            "resource-manager"  : "",
            "xsettings-blob"    : (0, [])
            }, reset = True, dpi = self.default_dpi, cursor_size=24)

    def update_all_server_settings(self, reset=False):
        self.update_server_settings({
            "resource-manager"  : "",
            "xsettings-blob"    : (0, []),
            }, reset=reset)

    def update_server_settings(self, settings=None, reset=False):
        self.do_update_server_settings(settings or self._settings, reset,
                                self.dpi, self.double_click_time, self.double_click_distance, self.antialias, self.cursor_size)

    def do_update_server_settings(self, settings, reset=False,
                                  dpi=0, double_click_time=0, double_click_distance=(-1, -1), antialias={}, cursor_size=-1):
        if not self._xsettings_enabled:
            log("ignoring xsettings update: %s", settings)
            return
        if reset:
            #FIXME: preserve serial? (what happens when we change values which had the same serial?)
            self.reset_settings()
            self._settings = {}
            if self._default_xsettings:
                #try to parse default xsettings into a dict:
                try:
                    for _, prop_name, value, _ in self._default_xsettings[1]:
                        self._settings[prop_name] = value
                except Exception as e:
                    log("failed to parse %s", self._default_xsettings)
                    log.warn("Warning: failed to parse default XSettings:")
                    log.warn(" %s", e)
        old_settings = dict(self._settings)
        log("server_settings: old=%s, updating with=%s", nonl(old_settings), nonl(settings))
        log("overrides: dpi=%s, double click time=%s, double click distance=%s",
            dpi, double_click_time, double_click_distance)
        log("overrides: antialias=%s", antialias)
        self._settings.update(settings)
        for k, v in settings.items():
            #cook the "resource-manager" value to add the DPI and/or antialias values:
            if k=="resource-manager" and (dpi>0 or antialias or cursor_size>0):
                value = bytestostr(v)
                #parse the resources into a dict:
                values={}
                options = value.split("\n")
                for option in options:
                    if not option:
                        continue
                    parts = option.split(":\t", 1)
                    if len(parts)!=2:
                        log("skipped invalid option: '%s'", option)
                        continue
                    values[parts[0]] = parts[1]
                if cursor_size>0:
                    values["Xcursor.size"] = cursor_size
                if dpi>0:
                    values["Xft.dpi"] = dpi
                    values["Xft/DPI"] = dpi*1024
                    values["gnome.Xft/DPI"] = dpi*1024
                if antialias:
                    ad = typedict(antialias)
                    subpixel_order = "none"
                    sss = tuple(self._server_sources.values())
                    if len(sss)==1:
                        #only honour sub-pixel hinting if a single client is connected
                        #and only when it is not using any scaling (or overriden with SCALED_FONT_ANTIALIAS):
                        ss = sss[0]
                        ds_unscaled = getattr(ss, "desktop_size_unscaled", None)
                        ds_scaled = getattr(ss, "desktop_size", None)
                        if SCALED_FONT_ANTIALIAS or (not ds_unscaled or ds_unscaled==ds_scaled):
                            subpixel_order = ad.strget("orientation", "none").lower()
                    values.update({
                                   "Xft.antialias"  : ad.intget("enabled", -1),
                                   "Xft.hinting"    : ad.intget("hinting", -1),
                                   "Xft.rgba"       : subpixel_order,
                                   "Xft.hintstyle"  : _get_antialias_hintstyle(ad)})
                log("server_settings: resource-manager values=%s", nonl(values))
                #convert the dict back into a resource string:
                value = ''
                for vk, vv in values.items():
                    value += "%s:\t%s\n" % (vk, vv)
                #record the actual value used
                self._settings["resource-manager"] = value
                v = value.encode("utf-8")

            #cook xsettings to add various settings:
            #(as those may not be present in xsettings on some platforms.. like win32 and osx)
            if k=="xsettings-blob" and \
            (self.double_click_time>0 or self.double_click_distance!=(-1, -1) or antialias or dpi>0):
                from xpra.x11.xsettings_prop import XSettingsTypeInteger, XSettingsTypeString
                def set_xsettings_value(name, value_type, value):
                    #remove existing one, if any:
                    serial, values = v
                    new_values = [(_t,_n,_v,_s) for (_t,_n,_v,_s) in values if _n!=name]
                    new_values.append((value_type, name, value, 0))
                    return serial, new_values
                def set_xsettings_int(name, value):
                    if value<0: #not set, return v unchanged
                        return v
                    return set_xsettings_value(name, XSettingsTypeInteger, value)
                if dpi>0:
                    v = set_xsettings_int("Xft/DPI", dpi*1024)
                if double_click_time>0:
                    v = set_xsettings_int("Net/DoubleClickTime", self.double_click_time)
                if antialias:
                    ad = typedict(antialias)
                    v = set_xsettings_int("Xft/Antialias",  ad.intget("enabled", -1))
                    v = set_xsettings_int("Xft/Hinting",    ad.intget("hinting", -1))
                    v = set_xsettings_value("Xft/RGBA",     XSettingsTypeString, ad.strget("orientation", "none").lower())
                    v = set_xsettings_value("Xft/HintStyle", XSettingsTypeString, _get_antialias_hintstyle(ad))
                if double_click_distance!=(-1, -1):
                    #some platforms give us a value for each axis,
                    #but X11 only has one, so take the average
                    try:
                        x,y = double_click_distance
                        if x>0 and y>0:
                            d = iround((x+y)/2.0)
                            d = max(1, min(128, d))     #sanitize it a bit
                            v = set_xsettings_int("Net/DoubleClickDistance", d)
                    except Exception as e:
                        log.warn("error setting double click distance from %s: %s", double_click_distance, e)

            if k not in old_settings or v != old_settings[k]:
                def root_set(p):
                    from xpra.x11.gtk_x11.prop import prop_set
                    log("server_settings: setting %s to %s", nonl(p), nonl(v))
                    prop_set(self.root_window, p, "latin1", strtobytes(v).decode("latin1"))
                if k == "xsettings-blob":
                    self.set_xsettings(v)
                elif k == "resource-manager":
                    root_set("RESOURCE_MANAGER")
