# This file is part of Xpra.
# Copyright (C) 2008, 2009 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2012-2018 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.util import envbool
from xpra.gtk_common.error import xsync
from xpra.x11.gtk_x11.prop import prop_get
from xpra.gtk_common.gtk_util import get_xwindow
from xpra.x11.bindings.window_bindings import X11WindowBindings #@UnresolvedImport
from xpra.log import Logger

log = Logger("x11", "window")

X11Window = X11WindowBindings()


FORCE_REPLACE_WM = envbool("XPRA_FORCE_REPLACE_WM", False)
def wm_check(display, wm_name, upgrading=False):
    with xsync:
        #there should only be one screen... but let's check all of them
        for i in range(display.get_n_screens()):
            screen = display.get_screen(i)
            root = screen.get_root_window()
            wm_prop = "WM_S%s" % i
            cwm_prop = "_NEW_WM_CM_S%s" % i
            wm_so = X11Window.XGetSelectionOwner(wm_prop)
            cwm_so = X11Window.XGetSelectionOwner(cwm_prop)
            log("ewmh selection owner for %s: %s", wm_prop, wm_so)
            log("compositing window manager %s: %s", cwm_prop, cwm_so)

            try:
                ewmh_wm = prop_get(root, "_NET_SUPPORTING_WM_CHECK", "window", ignore_errors=True, raise_xerrors=False)
            except:
                #errors here generally indicate that the window is gone
                #which is fine: it means the previous window manager is no longer active
                continue
            def xid(w):
                if w:
                    return "%#x" % get_xwindow(w)
                return None
            log("_NET_SUPPORTING_WM_CHECK for screen %i: %s (root=%s)", i, xid(ewmh_wm), xid(root))
            if ewmh_wm:
                try:
                    name = prop_get(ewmh_wm, "_NET_WM_NAME", "utf8", ignore_errors=False, raise_xerrors=False)
                except:
                    name = None
                if upgrading and name and name==wm_name:
                    log.info("found previous Xpra instance")
                else:
                    log.warn("Warning: found an existing window manager on screen %s using window %#x: %s", i, get_xwindow(ewmh_wm), name or "unknown")
                if (wm_so is None or wm_so==0) and (cwm_so is None or cwm_so==0):
                    if FORCE_REPLACE_WM:
                        log.warn("XPRA_FORCE_REPLACE_WM is set, replacing it forcibly")
                    else:
                        log.error("it does not own the selection '%s' or '%s' so we cannot take over and make it exit", wm_prop, cwm_prop)
                        log.error("please stop %s so you can run xpra on this display", name or "the existing window manager")
                        log.warn("if you are certain that the window manager is already gone,")
                        log.warn(" you may set XPRA_FORCE_REPLACE_WM=1 to force xpra to continue, at your own risk")
                        return False
    return True
