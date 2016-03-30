# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2012-2013 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os, time

from xpra.gtk_common.gtk_util import get_xwindow
from xpra.x11.x11_server_base import X11ServerBase
from xpra.server.shadow.gtk_shadow_server_base import GTKShadowServerBase
from xpra.server.shadow.gtk_root_window_model import GTKRootWindowModel
from xpra.x11.bindings.ximage import XImageBindings     #@UnresolvedImport
from xpra.gtk_common.error import xsync
XImage = XImageBindings()

from xpra.log import Logger
log = Logger("x11", "shadow")
traylog = Logger("tray")

USE_XSHM = os.environ.get("XPRA_XSHM", "1")=="1"


class GTKX11RootWindowModel(GTKRootWindowModel):

    def __init__(self, root_window):
        GTKRootWindowModel.__init__(self, root_window)
        self.xshm = None

    def __repr__(self):
        return "GTKX11RootWindowModel(%#x)" % get_xwindow(self.window)

    def suspend(self):
        #we can cleanup the current xshm area and we'll create a new one later
        self.cleanup()

    def cleanup(self):
        if self.xshm:
            with xsync:
                self.xshm.cleanup()
            self.xshm = None


    def get_image(self, x, y, width, height, logger=None):
        try:
            start = time.time()
            with xsync:
                if USE_XSHM:
                    log("X11 shadow get_image, xshm=%s", self.xshm)
                    if self.xshm is None:
                        self.xshm = XImage.get_XShmWrapper(get_xwindow(self.window))
                        self.xshm.setup()
                    if self.xshm:
                        image = self.xshm.get_image(get_xwindow(self.window), x, y, width, height)
                        #discard to ensure we will call XShmGetImage next time around
                        self.xshm.discard()
                        return image
                #fallback to gtk capture:
                return GTKRootWindowModel.get_image(self, x, y, width, height, logger)
        except Exception as e:
            log.warn("Warning: failed to capture root window pixels:")
            log.warn(" %s", e)
            #cleanup and hope for the best!
            self.cleanup()
        finally:
            end = time.time()
            log("X11 shadow captured %s pixels at %i MPixels/s using %s", width*height, (width*height/(end-start))//1024//1024, ["GTK", "XSHM"][USE_XSHM])


#FIXME: warning: this class inherits from ServerBase twice..
#so many calls will happen twice there (__init__ and init)
class ShadowX11Server(GTKShadowServerBase, X11ServerBase):

    def __init__(self):
        GTKShadowServerBase.__init__(self)
        X11ServerBase.__init__(self, False)

    def init(self, opts):
        GTKShadowServerBase.init(self, opts)
        X11ServerBase.init(self, opts)

    def make_tray_widget(self):
        from xpra.platform.xposix.gui import get_native_system_tray_classes
        classes = get_native_system_tray_classes()
        try:
            from xpra.client.gtk_base.statusicon_tray import GTKStatusIconTray
            classes.append(GTKStatusIconTray)
        except:
            pass
        traylog("tray classes: %s", classes)
        if not classes:
            traylog.error("Error: no system tray implementation available")
            return None
        errs = []
        for c in classes:
            try:
                w = c(self, self.tray, "Xpra Shadow Server", None, None, self.tray_click_callback, mouseover_cb=None, exit_cb=self.tray_exit_callback)
                return w
            except Exception as e:
                errs.append((c, e))
        traylog.error("Error: all system tray implementations have failed")
        for c, e in errs:
            traylog.error(" %s: %s", c, e)
        return None


    def makeRootWindowModel(self):
        return GTKX11RootWindowModel(self.root)

    def last_client_exited(self):
        GTKShadowServerBase.last_client_exited(self)
        X11ServerBase.last_client_exited(self)

    def _process_mouse_common(self, proto, wid, pointer):
        pointer = self._adjust_pointer(pointer)
        X11ServerBase._process_mouse_common(self, proto, wid, pointer)


    def make_hello(self, source):
        capabilities = X11ServerBase.make_hello(self, source)
        capabilities.update(GTKShadowServerBase.make_hello(self, source))
        capabilities["server_type"] = "Python/gtk2/x11-shadow"
        return capabilities

    def get_info(self, proto):
        info = X11ServerBase.get_info(self, proto)
        info.setdefault("features", {})["shadow"] = True
        info.setdefault("server", {})["type"] = "Python/gtk2/x11-shadow"
        return info
