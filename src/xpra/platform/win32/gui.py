# This file is part of Xpra.
# Copyright (C) 2010 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2011-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

# Platform-specific code for Win32 -- the parts that may import gtk.

import os
from xpra.log import Logger
log = Logger("win32")

from xpra.platform.win32.win32_events import get_win32_event_listener
from xpra.util import AdHocStruct

UNGRAB_KEY = os.environ.get("XPRA_UNGRAB_KEY", "Escape")


KNOWN_EVENTS = {}
try:
    import win32con             #@UnresolvedImport
    for x in dir(win32con):
        if x.endswith("_EVENT"):
            v = getattr(win32con, x)
            KNOWN_EVENTS[v] = x
except:
    pass


def get_native_notifier_classes():
    try:
        from xpra.platform.win32.win32_notifier import Win32_Notifier
        return [Win32_Notifier]
    except:
        log.warn("cannot load native win32 notifier", exc_info=True)
        return []

def get_native_tray_classes():
    try:
        from xpra.platform.win32.win32_tray import Win32Tray
        return [Win32Tray]
    except:
        log.warn("cannot load native win32 tray", exc_info=True)
        return []

def get_native_system_tray_classes(*args):
    #Win32Tray cannot set the icon from data
    #so it cannot be used for application trays
    return get_native_tray_classes()


class ClientExtras(object):
    def __init__(self, client):
        self.client = client
        self._kh_warning = False
        self.setup_console_event_listener()
        try:
            import win32con                 #@Reimport @UnresolvedImport
            el = get_win32_event_listener(True)
            if el:
                el.add_event_callback(win32con.WM_ACTIVATEAPP, self.activateapp)
        except:
            log.error("cannot register focus callback")

    def cleanup(self):
        self.setup_console_event_listener(False)
        log("ClientExtras.cleanup() ended")
        el = get_win32_event_listener(False)
        if el:
            el.cleanup()
        self.client = None

    def activateapp(self, wParam, lParam):
        log("WM_ACTIVATEAPP: %s/%s UNGRAB_KEY=%s, client=%s", wParam, lParam, UNGRAB_KEY, self.client)
        if wParam==0 and self.client:
            #our app has lost focus
            wid = self.client.window_with_grab
            if wid is not None and UNGRAB_KEY:
                self.force_ungrab(wid)

    def force_ungrab(self, wid):
        kh = self.client.keyboard_helper
        if not kh:
            if not self._kh_warning:
                self._kh_warning = True
                log.warn("no keyboard support, cannot simulate keypress to lose grab!")
            return
        #xkbmap_keycodes is a list of: (keyval, name, keycode, group, level)
        ungrab_keys = [x for x in kh.xkbmap_keycodes if x[1]==UNGRAB_KEY]
        if len(ungrab_keys)==0:
            if not self._kh_warning:
                self._kh_warning = True
                log.warn("ungrab key %s not found, cannot simulate keypress to lose grab!", UNGRAB_KEY)
            return
        #ungrab_keys.append((65307, "Escape", 27, 0, 0))     #ugly hardcoded default value
        ungrab_key = ungrab_keys[0]
        log("lost focus whilst window has grab, simulating keypress: %s", ungrab_key)
        key_event = AdHocStruct()
        key_event.keyname = ungrab_key[1]
        key_event.pressed = True
        key_event.modifiers = []
        key_event.keyval = ungrab_key[0]
        keycode = ungrab_key[2]
        try:
            key_event.string = chr(keycode)
        except:
            key_event.string = str(keycode)
        key_event.keycode = keycode
        key_event.group = 0
        #press:
        kh.send_key_action(wid, key_event)
        #unpress:
        key_event.pressed = False
        kh.send_key_action(wid, key_event)

    def setup_console_event_listener(self, enable=1):
        try:
            import win32api     #@UnresolvedImport
            result = win32api.SetConsoleCtrlHandler(self.handle_console_event, enable)
            if result == 0:
                log.error("could not SetConsoleCtrlHandler (error %r)", win32api.GetLastError())
        except:
            pass

    def handle_console_event(self, event):
        log("handle_console_event(%s)", event)
        event_name = KNOWN_EVENTS.get(event, event)
        info_events = [win32con.CTRL_C_EVENT,
                       win32con.CTRL_LOGOFF_EVENT,
                       win32con.CTRL_BREAK_EVENT,
                       win32con.CTRL_SHUTDOWN_EVENT,
                       win32con.CTRL_CLOSE_EVENT]
        if event in info_events:
            log.info("received win32 console event %s", event_name)
        else:
            log.warn("unknown console event: %s", event_name)
        return 0
