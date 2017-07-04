# This file is part of Xpra.
# Copyright (C) 2008, 2009 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2012-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.gtk_common.gtk_util import get_xwindow
from xpra.x11.bindings.window_bindings import constants, X11WindowBindings #@UnresolvedImport
X11Window = X11WindowBindings()

from xpra.log import Logger
log = Logger("x11", "focus")

CurrentTime = constants["CurrentTime"]
SubstructureNotifyMask = constants["SubstructureNotifyMask"]
SubstructureRedirectMask = constants["SubstructureRedirectMask"]


def send_wm_take_focus(target, timestamp):
    xid = get_xwindow(target)
    log("sending WM_TAKE_FOCUS: %#x, X11 timestamp=%r", xid, timestamp)
    if timestamp<0:
        timestamp = CurrentTime    #better than nothing...
    elif timestamp>0xFFFFFFFF:
        raise OverflowError("invalid time: %#x" % timestamp)
    elif timestamp>0x7FFFFFFF:
        timestamp = int(0x100000000-timestamp)
        if timestamp<0x80000000:
            return -timestamp
        else:
            return -0x80000000
    X11Window.sendClientMessage(xid, xid, False, 0,
                      "WM_PROTOCOLS",
                      "WM_TAKE_FOCUS", timestamp)

def send_wm_delete_window(target):
    xid = get_xwindow(target)
    log("sending WM_DELETE_WINDOW to %#x", xid)
    X11Window.sendClientMessage(xid, xid, False, 0,
                      "WM_PROTOCOLS",
                      "WM_DELETE_WINDOW",
                      CurrentTime)

def send_wm_workspace(root, win, workspace=0):
    event_mask = SubstructureNotifyMask | SubstructureRedirectMask
    X11Window.sendClientMessage(get_xwindow(root), get_xwindow(win), False, event_mask,
                      "_NET_WM_DESKTOP",
                      workspace, CurrentTime)

def send_wm_request_frame_extents(root, win):
    event_mask = SubstructureNotifyMask | SubstructureRedirectMask
    X11Window.sendClientMessage(get_xwindow(root), get_xwindow(win), False, event_mask,
              "_NET_REQUEST_FRAME_EXTENTS",
              0, CurrentTime)
