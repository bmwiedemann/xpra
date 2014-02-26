#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2011-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import win32ts, win32con, win32api, win32gui        #@UnresolvedImport

from xpra.log import Logger
log = Logger("events", "win32")


#no idea where we're supposed to get those from:
WM_WTSSESSION_CHANGE        = 0x02b1
WM_DWMNCRENDERINGCHANGED    = 0x31F
IGNORE_EVENTS = {
            win32con.WM_DESTROY             : "WM_DESTROY",
            win32con.WM_COMMAND             : "WM_COMMAND",
            win32con.WM_DEVICECHANGE        : "WM_DEVICECHANGE",
            win32con.WM_DISPLAYCHANGE       : "WM_DISPLAYCHANGE",       #already taken care of by gtk event
            win32con.WM_WINDOWPOSCHANGING   : "WM_WINDOWPOSCHANGING",
            win32con.WM_GETMINMAXINFO       : "WM_GETMINMAXINFO",       #could be used to limit window size?
            WM_WTSSESSION_CHANGE            : "WM_WTSSESSION_CHANGE",
            WM_DWMNCRENDERINGCHANGED        : "WM_DWMNCRENDERINGCHANGED",
            }
LOG_EVENTS = {
            win32con.WM_POWERBROADCAST      : "WM_POWERBROADCAST: power management event",
            win32con.WM_TIMECHANGE          : "WM_TIMECHANGE: time change event",
            }
KNOWN_WM_EVENTS = IGNORE_EVENTS.copy()
for x in dir(win32con):
    if x.startswith("WM_"):
        v = getattr(win32con, x)
        KNOWN_WM_EVENTS[v] = x
NIN_BALLOONSHOW         = win32con.WM_USER + 2
NIN_BALLOONHIDE         = win32con.WM_USER + 3
NIN_BALLOONTIMEOUT      = win32con.WM_USER + 4
NIN_BALLOONUSERCLICK    = win32con.WM_USER + 5
BALLOON_EVENTS = {
            NIN_BALLOONSHOW             : "NIN_BALLOONSHOW",
            NIN_BALLOONHIDE             : "NIN_BALLOONHIDE",
            NIN_BALLOONTIMEOUT          : "NIN_BALLOONTIMEOUT",
            NIN_BALLOONUSERCLICK        : "NIN_BALLOONUSERCLICK",
          }
KNOWN_WM_EVENTS.update(BALLOON_EVENTS)


singleton = None
def get_win32_event_listener(create=True):
    global singleton
    if not singleton and create:
        singleton = Win32EventListener()
    return singleton


class Win32EventListener(object):

    def __init__(self):
        assert singleton is None
        self.wc = win32gui.WNDCLASS()
        self.wc.lpszClassName = 'XpraEventWindow'
        self.wc.style =  win32con.CS_GLOBALCLASS|win32con.CS_VREDRAW|win32con.CS_HREDRAW
        self.wc.hbrBackground = win32con.COLOR_WINDOW
        #shame we would have to register those in advance:
        self.wc.lpfnWndProc = {}    #win32con.WM_SOMETHING : OnSomething}
        win32gui.RegisterClass(self.wc)        
        self.hwnd = win32gui.CreateWindow(self.wc.lpszClassName,
                        'For events only',
                        win32con.WS_CAPTION,
                        100, 100, 900, 900, 0, 0, 0, None)
        self.old_win32_proc = None
        self.event_callbacks = {}
        self.detect_win32_session_events()
        log("Win32EventListener create with hwnd=%s", self.hwnd)

    def add_event_callback(self, event, callback):
        self.event_callbacks.setdefault(event, []).append(callback)

    def remove_event_callback(self, event, callback):
        l = self.event_callbacks.get(event)
        if l and callback in l:
            l.remove(callback)

    def cleanup(self):
        self.stop_win32_session_events()
        if self.hwnd:
            win32gui.DestroyWindow(self.hwnd)
            self.hwnd = None
            win32gui.UnregisterClass(self.wc.lpszClassName, None)

    def stop_win32_session_events(self):
        if not self.old_win32_proc:
            return
        try:
            if self.hwnd:
                win32api.SetWindowLong(self.hwnd, win32con.GWL_WNDPROC, self.old_win32_proc)
                self.old_win32_proc = None
                win32ts.WTSUnRegisterSessionNotification(self.hwnd)
            else:
                log.warn("stop_win32_session_events() missing handle!")
        except:
            log.error("stop_win32_session_events", exc_info=True)

    def detect_win32_session_events(self):
        """
        Use pywin32 to receive session notification events.
        """
        if self.hwnd is None:
            log.warn("detect_win32_session_events() missing handle!")
            return
        try:
            log("detect_win32_session_events() hwnd=%s", self.hwnd)
            #register our interest in those events:
            #http://timgolden.me.uk/python/win32_how_do_i/track-session-events.html#isenslogon
            #http://stackoverflow.com/questions/365058/detect-windows-logout-in-python
            #http://msdn.microsoft.com/en-us/library/aa383841.aspx
            #http://msdn.microsoft.com/en-us/library/aa383828.aspx
            win32ts.WTSRegisterSessionNotification(self.hwnd, win32ts.NOTIFY_FOR_THIS_SESSION)
            #catch all events: http://wiki.wxpython.org/HookingTheWndProc
            self.old_win32_proc = win32gui.SetWindowLong(self.hwnd, win32con.GWL_WNDPROC, self.MyWndProc)
        except Exception, e:
            log.error("failed to hook session notifications: %s", e)

    def MyWndProc(self, hWnd, msg, wParam, lParam):
        assert hWnd==self.hwnd, "invalid hwnd: %s (expected %s)" % (hWnd, self.hwnd)
        callbacks = self.event_callbacks.get(msg)
        event_name = KNOWN_WM_EVENTS.get(msg, msg)
        log("callbacks for event %s: %s", event_name, callbacks)
        if callbacks:
            for c in callbacks:
                try:
                    c(wParam, lParam)
                except:
                    log.error("error in callback %s", c, exc_info=True)
        elif msg in IGNORE_EVENTS:
            log("%s: %s / %s", IGNORE_EVENTS.get(msg), wParam, lParam)
        elif msg in LOG_EVENTS:
            log.info("%s: %s / %s", LOG_EVENTS.get(msg), wParam, lParam)
        #elif msg==win32con.WM_ACTIVATEAPP:
        #    log("WM_ACTIVATEAPP focus changed: %s / %s", wParam, lParam)
        else:
            log.warn("unexpected message: %s / %s / %s", event_name, wParam, lParam)
        # Pass all messages to the original WndProc
        try:
            return win32gui.CallWindowProc(self.old_win32_proc, hWnd, msg, wParam, lParam)
        except Exception, e:
            log.error("error delegating call for %s: %s", event_name, e)
