# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2013 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import gtk.gdk
import gobject
import os.path

#ensure that we use gtk as display source:
from xpra.x11.gtk_x11 import gdk_display_source
assert gdk_display_source

from xpra.x11.bindings.randr_bindings import RandRBindings  #@UnresolvedImport
RandR = RandRBindings()
from xpra.x11.bindings.keyboard_bindings import X11KeyboardBindings #@UnresolvedImport
X11Keyboard = X11KeyboardBindings()
from xpra.x11.bindings.core_bindings import X11CoreBindings #@UnresolvedImport
X11Core = X11CoreBindings()
from xpra.x11.gtk_x11.prop import prop_set
from xpra.x11.gtk_x11.error import XError, trap
from xpra.server.server_uuid import save_uuid, get_uuid

from xpra.log import Logger
log = Logger()

from xpra.util import log_screen_sizes, prettify_plug_name
from xpra.server.gtk_server_base import GTKServerBase
from xpra.x11.xkbhelper import clean_keyboard_state
from xpra.x11.xsettings import XSettingsManager

MAX_CONCURRENT_CONNECTIONS = 20


def window_name(window):
    from xpra.x11.gtk_x11.prop import prop_get
    return prop_get(window, "_NET_WM_NAME", "utf8", True) or "unknown"

def window_info(window):
    from xpra.x11.gtk_x11.prop import prop_get
    net_wm_name = prop_get(window, "_NET_WM_NAME", "utf8", True)
    return "%s %s (%s / %s)" % (net_wm_name, window, window.get_geometry(), window.is_visible())


from xpra.x11.gtk_x11.gdk_bindings import get_children #@UnresolvedImport
def dump_windows():
    root = gtk.gdk.get_default_root_window()
    log("root window: %s" % root)
    children = get_children(root)
    log("%s windows" % len(children))
    for window in get_children(root):
        log("found window: %s", window_info(window))


class X11ServerBase(GTKServerBase):
    """
        Base class for X11 servers,
        adds X11 specific methods to GTKServerBase.
        (see XpraServer or XpraX11ShadowServer for actual implementations)
    """

    def init(self, clobber, opts):
        self.xsettings_enabled = opts.xsettings
        self.clobber = clobber
        self.fake_xinerama = opts.fake_xinerama
        self.x11_init()
        GTKServerBase.init(self, opts)


    def x11_init(self):
        self.init_x11_atoms()
        self.randr = RandR.has_randr()
        if self.randr and len(RandR.get_screen_sizes())<=1:
            log.info("no RandR support")
            #disable randr when we are dealing with a Xvfb
            #with only one resolution available
            #since we don't support adding them on the fly yet
            self.randr = False
        if self.randr:
            display = gtk.gdk.display_get_default()
            i=0
            while i<display.get_n_screens():
                screen = display.get_screen(i)
                screen.connect("size-changed", self._screen_size_changed)
                i += 1
        log("randr enabled: %s", self.randr)
        self._settings = {}
        self._xsettings_manager = None

    def init_x11_atoms(self):
        #some applications (like openoffice), do not work properly
        #if some x11 atoms aren't defined, so we define them in advance:
        for atom_name in ["_NET_WM_WINDOW_TYPE",
                          "_NET_WM_WINDOW_TYPE_NORMAL",
                          "_NET_WM_WINDOW_TYPE_DESKTOP",
                          "_NET_WM_WINDOW_TYPE_DOCK",
                          "_NET_WM_WINDOW_TYPE_TOOLBAR",
                          "_NET_WM_WINDOW_TYPE_MENU",
                          "_NET_WM_WINDOW_TYPE_UTILITY",
                          "_NET_WM_WINDOW_TYPE_SPLASH",
                          "_NET_WM_WINDOW_TYPE_DIALOG",
                          "_NET_WM_WINDOW_TYPE_DROPDOWN_MENU",
                          "_NET_WM_WINDOW_TYPE_POPUP_MENU",
                          "_NET_WM_WINDOW_TYPE_TOOLTIP",
                          "_NET_WM_WINDOW_TYPE_NOTIFICATION",
                          "_NET_WM_WINDOW_TYPE_COMBO",
                          "_NET_WM_WINDOW_TYPE_DND",
                          "_NET_WM_WINDOW_TYPE_NORMAL"
                          ]:
            X11Core.get_xatom(atom_name)

    def init_keyboard(self):
        GTKServerBase.init_keyboard(self)
        #clear all modifiers
        clean_keyboard_state()


    def get_uuid(self):
        return get_uuid()

    def save_uuid(self):
        save_uuid(self.uuid)

    def set_keyboard_repeat(self, key_repeat):
        if key_repeat:
            self.key_repeat_delay, self.key_repeat_interval = key_repeat
            if self.key_repeat_delay>0 and self.key_repeat_interval>0:
                X11Keyboard.set_key_repeat_rate(self.key_repeat_delay, self.key_repeat_interval)
                log.info("setting key repeat rate from client: %sms delay / %sms interval", self.key_repeat_delay, self.key_repeat_interval)
        else:
            #dont do any jitter compensation:
            self.key_repeat_delay = -1
            self.key_repeat_interval = -1
            #but do set a default repeat rate:
            X11Keyboard.set_key_repeat_rate(500, 30)


    def make_hello(self):
        capabilities = GTKServerBase.make_hello(self)
        capabilities["resize_screen"] = self.randr
        return capabilities

    def do_get_info(self, proto, server_sources, window_ids):
        info = GTKServerBase.do_get_info(self, proto, server_sources, window_ids)
        info["server.type"] = "Python/gtk/x11"
        try:
            from xpra.x11.gtk_x11.composite import CompositeHelper
            info["server.XShm"] = CompositeHelper.XShmEnabled
        except:
            pass
        return info

    def get_window_info(self, window):
        info = GTKServerBase.get_window_info(self, window)
        info["XShm"] = window.uses_XShm()
        return info


    def set_keymap(self, server_source, force=False):
        try:
            #prevent _keys_changed() from firing:
            #(using a flag instead of keymap.disconnect(handler) as this did not seem to work!)
            self.keymap_changing = True

            self.keyboard_config = server_source.set_keymap(self.keyboard_config, self.keys_pressed, force)
        finally:
            # re-enable via idle_add to give all the pending
            # events a chance to run first (and get ignored)
            def reenable_keymap_changes(*args):
                self.keymap_changing = False
                self._keys_changed()
            gobject.idle_add(reenable_keymap_changes)


    def _clear_keys_pressed(self):
        #make sure the timers don't fire and interfere:
        if len(self.keys_repeat_timers)>0:
            for timer in self.keys_repeat_timers.values():
                gobject.source_remove(timer)
            self.keys_repeat_timers = {}
        #clear all the keys we know about:
        if len(self.keys_pressed)>0:
            log("clearing keys pressed: %s", self.keys_pressed)
            for keycode in self.keys_pressed.keys():
                X11Keyboard.xtest_fake_key(keycode, False)
            self.keys_pressed = {}
        #this will take care of any remaining ones we are not aware of:
        #(there should not be any - but we want to be certain)
        X11Keyboard.unpress_all_keys()


    def get_max_screen_size(self):
        from xpra.x11.gtk_x11.window import MAX_WINDOW_SIZE
        max_w, max_h = gtk.gdk.get_default_root_window().get_size()
        sizes = RandR.get_screen_sizes()
        if self.randr and len(sizes)>=1:
            for w,h in sizes:
                max_w = max(max_w, w)
                max_h = max(max_h, h)
        if max_w>MAX_WINDOW_SIZE or max_h>MAX_WINDOW_SIZE:
            log.warn("maximum size is very large: %sx%s, you may encounter window sizing problems", max_w, max_h)
        return max_w, max_h


    def set_best_screen_size(self):
        #return ServerBase.set_best_screen_size(self)
        """ sets the screen size to use the largest width and height used by any of the clients """
        root_w, root_h = gtk.gdk.get_default_root_window().get_size()
        if not self.randr:
            return root_w, root_h
        max_w, max_h = 0, 0
        sss = self._server_sources.values()
        for ss in sss:
            client_size = ss.desktop_size
            if not client_size:
                continue
            if ss.screen_sizes and len(sss)>1:
                log.info("* %s:", ss.uuid)
            w, h = client_size
            log_screen_sizes(w, h, ss.screen_sizes)
            max_w = max(max_w, w)
            max_h = max(max_h, h)
        log.info("maximum client resolution is %sx%s (current server resolution is %sx%s)", max_w, max_h, root_w, root_h)
        if max_w>0 and max_h>0:
            return self.set_screen_size(max_w, max_h)
        return  root_w, root_h

    def set_screen_size(self, desired_w, desired_h):
        root_w, root_h = gtk.gdk.get_default_root_window().get_size()
        if desired_w==root_w and desired_h==root_h:
            return    root_w,root_h    #unlikely: perfect match already!
        #try to find the best screen size to resize to:
        new_size = None
        for w,h in RandR.get_screen_sizes():
            if w<desired_w or h<desired_h:
                continue            #size is too small for client
            if new_size:
                ew,eh = new_size
                if ew*eh<w*h:
                    continue        #we found a better (smaller) candidate already
            new_size = w,h
        if not new_size:
            log.warn("resolution not found for %sx%s", desired_w, desired_h)
            return  root_w, root_h
        log("best resolution for client(%sx%s) is: %s", desired_w, desired_h, new_size)
        #now actually apply the new settings:
        w, h = new_size
        xinerama_saved = self.save_fakexinerama_config()
        #we can only keep things unchanged if xinerama was also unchanged
        #(it seems that some apps will only query xinerama again if randr is used?)
        if (w==root_w and h==root_h) and not xinerama_saved:
            log.info("best resolution matching %sx%s is unchanged: %sx%s", desired_w, desired_h, w, h)
            return  root_w, root_h
        try:
            log.debug("calling RandR.set_screen_size(%s, %s)", w, h)
            RandR.set_screen_size(w, h)
            log.debug("calling RandR.get_screen_size()")
            root_w, root_h = RandR.get_screen_size()
            log.debug("RandR.get_screen_size()=%s,%s", root_w, root_h)
            if root_w!=w or root_h!=h:
                log.error("odd, failed to set the new resolution, "
                          "tried to set it to %sx%s and ended up with %sx%s", w, h, root_w, root_h)
            else:
                log.info("new resolution matching %sx%s : screen now set to %sx%s", desired_w, desired_h, root_w, root_h)
        except Exception, e:
            log.error("ouch, failed to set new resolution: %s", e, exc_info=True)
        return  root_w, root_h

    def save_fakexinerama_config(self):
        xinerama_files = [
                          #the new fakexinerama file:
                          os.path.expanduser("~/.%s-fakexinerama" % os.environ.get("DISPLAY")),
                          #compat file for "old" version found on github:
                          os.path.expanduser("~/.fakexinerama"),
                          ]
        def delfile():
            for f in xinerama_files:
                if os.path.exists(f) and os.path.isfile(f):
                    try:
                        os.unlink(f)
                    except Exception, e:
                        log.warn("failed to delete fake xinerama file %s: %s", f, e)
        if not self.fake_xinerama or len(self._server_sources)!=1:
            return delfile()
        source = self._server_sources.values()[0]
        ss = source.screen_sizes
        if len(ss)==0:
            log.warn("cannot save fake xinerama settings: no display found")
            return delfile()
        if len(ss)>1:
            log.warn("cannot save fake xinerama settings: more than one display found")
            return delfile()
        if len(ss)==2 and type(ss[0])==int and type(ss[1])==int:
            #just WxH, not enough display information
            log.warn("cannot save fake xinerama settings: missing display data from client %s", source)
            return delfile()
        display_info = ss[0]
        if len(display_info)<10:
            log.warn("cannot save fake xinerama settings: incomplete display data from client %s", source)
            return delfile()
        #display_name, width, height, width_mm, height_mm, \
        #monitors, work_x, work_y, work_width, work_height = s[:11]
        monitors = display_info[5]
        if len(monitors)>=10:
            log.warn("cannot save fake xinerama settings: too many monitors! (%s)" % len(monitors))
            return delfile()
        #generate the data:
        data = ["# %s monitors:" % len(monitors),
                "%s" % len(monitors)]
        i = 0
        for m in monitors:
            if len(m)<7:
                log.warn("cannot save fake xinerama settings: incomplete monitor data for monitor: %s", m)
                continue
            plug_name, x, y, width, height, wmm, hmm = m[:8]
            data.append("# %s (%smm x %smm)" % (prettify_plug_name(plug_name, "monitor %s" % i), wmm, hmm))
            data.append("%s %s %s %s" % (x, y, width, height))
            i += 1
        data.append("")
        contents = "\n".join(data)
        for filename in xinerama_files:
            try:
                f = None
                try:
                    f = open(filename, 'wb')
                    f.write(contents)
                except Exception, e:
                    log.warn("error writing fake xinerama file %s: %s", filename, e)
                    pass
            finally:
                if f:
                    f.close()
        log("saved %s monitors to fake xinerama files: %s", len(monitors), xinerama_files)

    def _process_server_settings(self, proto, packet):
        settings = packet[1]
        if not self.xsettings_enabled:
            log("ignoring xsettings update: %s", settings)
            return
        self.update_server_settings(settings)

    def update_server_settings(self, settings):
        old_settings = dict(self._settings)
        log("server_settings: old=%s, updating with=%s", old_settings, settings)
        self._settings.update(settings)
        root = gtk.gdk.get_default_root_window()
        for k, v in settings.items():
            #cook the "resource-manager" value to add the DPI:
            if k == "resource-manager" and self.dpi>0:
                value = v.decode("utf-8")
                #parse the resources into a dict:
                values={}
                options = value.split("\n")
                for option in options:
                    if not option:
                        continue
                    parts = option.split(":\t")
                    if len(parts)!=2:
                        continue
                    values[parts[0]] = parts[1]
                values["Xft.dpi"] = self.dpi
                values["gnome.Xft/DPI"] = self.dpi*1024
                log("server_settings: resource-manager values=%s", values)
                #convert the dict back into a resource string:
                value = ''
                for vk, vv in values.items():
                    value += "%s:\t%s\n" % (vk, vv)
                value += '\n'
                #record the actual value used
                self._settings["resource-manager"] = value
                v = value.encode("utf-8")

            if k not in old_settings or v != old_settings[k]:
                def root_set(p):
                    log("server_settings: setting %s to %s", p, v)
                    prop_set(root, p, "latin1", v.decode("utf-8"))
                if k == "xsettings-blob":
                    if self._xsettings_manager is None:
                        self._xsettings_manager = XSettingsManager()
                    self._xsettings_manager.set_blob_in_place(v)
                elif k == "resource-manager":
                    root_set("RESOURCE_MANAGER")
                elif self.pulseaudio:
                    if k == "pulse-cookie":
                        root_set("PULSE_COOKIE")
                    elif k == "pulse-id":
                        root_set("PULSE_ID")
                    elif k == "pulse-server":
                        root_set("PULSE_SERVER")


    def fake_key(self, keycode, press):
        trap.call_synced(X11Keyboard.xtest_fake_key, keycode, press)


    def _move_pointer(self, wid, pos):
        x, y = pos
        display = gtk.gdk.display_get_default()
        display.warp_pointer(display.get_default_screen(), x, y)

    def _process_mouse_common(self, proto, wid, pointer, modifiers):
        ss = self._server_sources.get(proto)
        if ss is None:
            return
        pos = gtk.gdk.get_default_root_window().get_pointer()[:2]
        if pos==pointer:
            return
        trap.swallow_synced(self._move_pointer, wid, pointer)
        ss.make_keymask_match(modifiers)

    def _process_button_action(self, proto, packet):
        ss = self._server_sources.get(proto)
        if ss is None:
            return
        wid, button, pressed, pointer, modifiers = packet[1:6]
        self._process_mouse_common(proto, wid, pointer, modifiers)
        ss.user_event()
        try:
            trap.call_synced(X11Keyboard.xtest_fake_button, button, pressed)
        except XError:
            log.warn("Failed to pass on (un)press of mouse button %s"
                     + " (perhaps your Xvfb does not support mousewheels?)",
                     button)
