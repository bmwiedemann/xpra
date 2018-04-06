# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2012-2018 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

#ensures we only load GTK2:
from xpra.x11.gtk_x11.gdk_display_source import init_display_source #@UnresolvedImport
init_display_source()
from xpra.x11.x11_server_core import X11ServerCore

from xpra.os_util import monotonic_time
from xpra.util import envbool, envint, XPRA_APP_ID
from xpra.gtk_common.gtk_util import get_xwindow, is_gtk3
from xpra.server.shadow.gtk_shadow_server_base import GTKShadowServerBase
from xpra.server.shadow.gtk_root_window_model import GTKRootWindowModel
from xpra.x11.bindings.ximage import XImageBindings     #@UnresolvedImport
from xpra.gtk_common.error import xsync
XImage = XImageBindings()

from xpra.log import Logger
log = Logger("x11", "shadow")
traylog = Logger("tray")
cursorlog = Logger("cursor")
geomlog = Logger("geometry")

USE_XSHM = envbool("XPRA_XSHM", True)
POLL_CURSOR = envint("XPRA_POLL_CURSOR", 20)
USE_NVFBC = envbool("XPRA_NVFBC", True)
USE_NVFBC_CUDA = envbool("XPRA_NVFBC_CUDA", True)
if USE_NVFBC:
    try:
        from xpra.codecs.nvfbc.fbc_capture_linux import init_module, NvFBC_SysCapture, NvFBC_CUDACapture    #@UnresolvedImport
        init_module()
    except Exception:
        log("NvFBC Capture is not available", exc_info=True)
        USE_NVFBC = False


class XImageCapture(object):
    def __init__(self, xwindow):
        self.xshm = None
        self.xwindow = xwindow
        assert USE_XSHM and XImage.has_XShm(), "no XShm support"

    def clean(self):
        self.close_xshm()

    def close_xshm(self):
        xshm = self.xshm
        if self.xshm:
            self.xshm = None
            with xsync:
                xshm.cleanup()

    def get_image(self, x, y, width, height):
        try:
            start = monotonic_time()
            with xsync:
                log("X11 shadow get_image, xshm=%s", self.xshm)
                if self.xshm is None:
                    self.xshm = XImage.get_XShmWrapper(self.xwindow)
                    self.xshm.setup()
                image = self.xshm.get_image(self.xwindow, x, y, width, height)
                #discard to ensure we will call XShmGetImage next time around
                self.xshm.discard()
                return image
        except Exception as e:
            if getattr(e, "msg", None)=="BadMatch":
                log("BadMatch - temporary error?", exc_info=True)
            else:
                log.warn("Warning: failed to capture pixels of window %#x:", self.xwindow)
                log.warn(" %s", e)
            #cleanup and hope for the best!
            self.close_xshm()
            return None
        finally:
            end = monotonic_time()
            log("X11 shadow captured %s pixels at %i MPixels/s using %s", width*height, (width*height/(end-start))//1024//1024, ["GTK", "XSHM"][USE_XSHM])

class GTKImageCapture(object):
    def __init__(self, root_window_model):
        self.root_window_model = root_window_model

    def clean(self):
        pass

    def get_image(self, x, y, width, height):
        return GTKRootWindowModel.get_image(self.root_window_model, x, y, width, height)


class GTKX11RootWindowModel(GTKRootWindowModel):

    def __init__(self, root_window):
        GTKRootWindowModel.__init__(self, root_window)
        screen = root_window.get_screen()
        screen.connect("size-changed", self._screen_size_changed)
        self.geometry = root_window.get_geometry()[2:4]
        self.capture = None

    def __repr__(self):
        return "GTKX11RootWindowModel(%#x - %s)" % (get_xwindow(self.window), self.geometry)

    def suspend(self):
        self.close_capture()

    def cleanup(self):
        self.close_capture()
        GTKRootWindowModel.cleanup(self)

    def close_capture(self):
        capture = self.capture
        if self.capture:
            self.capture = None
            capture.clean()

    def get_dimensions(self):
        #used by get_window_info only
        return self.geometry[2:4]

    def _screen_size_changed(self, screen):
        log("screen size changed: %s, closing current capture instance %s", screen, self.capture)
        self.close_capture()

    def get_image(self, x, y, width, height):
        image = None
        ox, oy, ww, wh = self.geometry
        if not self.capture:
            if USE_NVFBC:
                try:
                    if USE_NVFBC_CUDA:
                        self.capture = NvFBC_CUDACapture()
                    else:
                        self.capture = NvFBC_SysCapture()
                    self.capture.init_context(ww, wh)
                    image = self.capture.get_image(x, y, width, height)
                except Exception as e:
                    log("get_image() NvFBC test failed", exc_info=True)
                    log("not using %s: %s", self.capture, e)
                    self.capture = None
            if not self.capture and XImage.has_XShm() and USE_XSHM:
                self.capture = XImageCapture(get_xwindow(self.window))
            if not self.capture:
                self.capture = GTKImageCapture(self)
            log("shadow image capture=%s", self.capture)
        image = image or self.capture.get_image(ox+x, oy+y, width, height)
        if ox>0 or oy>0:
            #all we want to do here is adjust x and y...
            #FIXME: this is inefficient and may take a copy of the pixels:
            from xpra.codecs.image_wrapper import ImageWrapper
            ix, iy, iw, ih, depth = image.get_geometry()
            return ImageWrapper(ix-ox, iy-oy, iw, ih, image.get_pixels(), image.get_pixel_format(), depth, image.get_rowstride(), image.get_bytesperpixel(), image.get_planes(), thread_safe=True, palette=None)
        return image


#FIXME: warning: this class inherits from ServerBase twice..
#so many calls will happen twice there (__init__ and init)
class ShadowX11Server(GTKShadowServerBase, X11ServerCore):

    def __init__(self):
        GTKShadowServerBase.__init__(self)
        X11ServerCore.__init__(self)
        self.session_type = "shadow"
        self.cursor_poll_timer = None

    def init(self, opts):
        GTKShadowServerBase.init(self, opts)
        X11ServerCore.do_init(self, opts)

    def start_refresh(self):
        GTKShadowServerBase.start_refresh(self)
        self.start_poll_cursor()

    def stop_refresh(self):
        GTKShadowServerBase.stop_refresh(self)
        self.stop_poll_cursor()


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
                w = c(self, XPRA_APP_ID, self.tray, "Xpra Shadow Server", None, None, self.tray_click_callback, mouseover_cb=None, exit_cb=self.tray_exit_callback)
                return w
            except Exception as e:
                errs.append((c, e))
        traylog.error("Error: all system tray implementations have failed")
        for c, e in errs:
            traylog.error(" %s: %s", c, e)
        return None


    def makeRootWindowModels(self):
        log("makeRootWindowModels() root=%s", self.root)
        screen = self.root.get_screen()
        n = screen.get_n_monitors()
        models = []
        for i in range(n):
            geom = screen.get_monitor_geometry(i)
            x, y, width, height = geom.x, geom.y, geom.width, geom.height
            model = GTKX11RootWindowModel(self.root)
            if hasattr(screen, "get_monitor_plug_name"):
                plug_name = screen.get_monitor_plug_name(i)
                if plug_name or n>1:
                    model.title = plug_name or str(i)
            model.geometry = (x, y, width, height)
            models.append(model)
        log("makeRootWindowModels()=%s", models)
        return models

    def _adjust_pointer(self, proto, wid, pointer):
        pointer = X11ServerCore._adjust_pointer(self, proto, wid, pointer)
        window = self._id_to_window.get(wid)
        if window:
            ox, oy = window.geometry[:2]
            x, y = pointer
            return x+ox, y+oy
        return pointer


    def send_updated_screen_size(self):
        log("send_updated_screen_size")
        X11ServerCore.send_updated_screen_size(self)
        for wid, window in self._id_to_window.items():
            w, h = window.get_dimensions()
            geomlog("%i new window dimensions: %s", wid, (w, h))
            for ss in self._server_sources.values():
                #first, make sure the size-hints are updated:
                ss.window_metadata(wid, window, "size-hints")
                #tell client to resize now:
                ss.resize_window(wid, window, w, h)
                #refresh to ensure the client gets the new window contents:
                ss.damage(wid, window, 0, 0, w, h)


    def last_client_exited(self):
        GTKShadowServerBase.last_client_exited(self)
        X11ServerCore.last_client_exited(self)


    def start_poll_cursor(self):
        #the cursor poll timer:
        self.cursor_poll_timer = None
        if POLL_CURSOR>0:
            self.cursor_poll_timer = self.timeout_add(POLL_CURSOR, self.poll_cursor)

    def stop_poll_cursor(self):
        cpt = self.cursor_poll_timer
        if cpt:
            self.cursor_poll_timer = None
            self.source_remove(cpt)

    def poll_cursor(self):
        prev = self.last_cursor_data
        X11ServerCore.get_cursor_data(self)
        def cmpv(v):
            if v and len(v)>2:
                return v[2:]
            return None
        if cmpv(prev)!=cmpv(self.last_cursor_data):
            fields = ("x", "y", "width", "height", "xhot", "yhot", "serial", "pixels", "name")
            if len(prev or [])==len(self.last_cursor_data or []) and len(prev or [])==len(fields):
                diff = []
                for i in range(len(prev)):
                    if prev[i]!=self.last_cursor_data[i]:
                        diff.append(fields[i])
                cursorlog("poll_cursor() attributes changed: %s", diff)
            for ss in self._server_sources.values():
                ss.send_cursor()
        return True

    def get_cursor_data(self):
        return X11ServerCore.get_cursor_data(self)


    def make_hello(self, source):
        capabilities = X11ServerCore.make_hello(self, source)
        capabilities.update(GTKShadowServerBase.make_hello(self, source))
        capabilities["server_type"] = "Python/gtk2/x11-shadow"
        return capabilities

    def get_info(self, proto, *_args):
        info = X11ServerCore.get_info(self, proto)
        info.setdefault("features", {})["shadow"] = True
        info.setdefault("server", {})["type"] = "Python/gtk%i/x11-shadow" % (2+is_gtk3())
        return info
