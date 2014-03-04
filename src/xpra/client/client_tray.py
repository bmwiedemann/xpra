# This file is part of Xpra.
# Copyright (C) 2010-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.client.client_widget_base import ClientWidgetBase
from xpra.log import Logger
log = Logger("tray")

from xpra.client.gtk_base.gtk_window_backing_base import GTKWindowBacking


class ClientTray(ClientWidgetBase):
    """
        This acts like a widget and we use the TrayBacking
        to capture the tray pixels and forward them
        to the real tray widget class.
    """
    DEFAULT_LOCATION = [0, 0]
    DEFAULT_SIZE = [64, 64]
    DEFAULT_GEOMETRY = DEFAULT_LOCATION + DEFAULT_SIZE

    def __init__(self, client, wid, w, h, tray_widget, mmap_enabled, mmap_area):
        log("ClientTray%s", (client, wid, w, h, tray_widget, mmap_enabled, mmap_area))
        ClientWidgetBase.__init__(self, client, wid)
        self.tray_widget = tray_widget
        self._has_alpha = True
        self._geometry = None
        self.group_leader = None

        self.mmap_enabled = mmap_enabled
        self.mmap = mmap_area
        self._backing = None
        self.new_backing(w, h)
        self.idle_add(self.reconfigure)

    def is_OR(self):
        return True

    def is_tray(self):
        return True

    def get_window(self):
        return None

    def get_geometry(self):
        return self._geometry or ClientTray.DEFAULT_GEOMETRY

    def get_tray_geometry(self):
        return self.tray_widget.get_geometry()

    def get_tray_size(self):
        return self.tray_widget.get_size()

    def reconfigure(self, force_send_configure=False):
        geometry = self.tray_widget.get_geometry()
        if geometry is None:
            if self._geometry:
                geometry = self._geometry
            else:
                #make one up as best we can - maybe we have the size at least?
                size = self.tray_widget.get_size()
                log("%s.reconfigure() guessing location using size=%s", self, size)
                geometry = ClientTray.DEFAULT_LOCATION + list(size or ClientTray.DEFAULT_SIZE)
        x, y, w, h = geometry
        if w<=1 or h<=1:
            w, h = ClientTray.DEFAULT_SIZE
            geometry = x, y, w, h
        if force_send_configure or self._geometry is None or geometry!=self._geometry:
            self._geometry = geometry
            client_properties = {"encoding.transparency": True,
                                 "encodings.rgb_formats" : ["RGBA", "RGB", "RGBX"]}
            orientation = self.tray_widget.get_orientation()
            if orientation:
                client_properties["orientation"] = orientation
            screen = self.tray_widget.get_screen()
            if screen>=0:
                client_properties["screen"] = screen
            self._client.send("configure-window", self._id, x, y, w, h, client_properties)
        if self._size!=(w, h):
            self.new_backing(w, h)

    def move_resize(self, x, y, w, h):
        log("%s.move_resize(%s, %s, %s, %s)", self, x, y, w, h)
        w = max(1, w)
        h = max(1, h)
        self._geometry = x, y, w, h
        self.reconfigure(True)

    def new_backing(self, w, h):
        self._size = w, h
        self._backing = TrayBacking(self._id, w, h, self._has_alpha)
        if self.mmap_enabled:
            self._backing.enable_mmap(self.mmap)

    def update_metadata(self, metadata):
        log("%s.update_metadata(%s)", self, metadata)

    def update_icon(self, width, height, coding, data):
        #this is the window icon... not the tray icon!
        pass


    def draw_region(self, x, y, width, height, coding, img_data, rowstride, packet_sequence, options, callbacks):
        log("%s.draw_region%s", self, (x, y, width, height, coding, "%s bytes" % len(img_data), rowstride, packet_sequence, options, callbacks))

        def after_draw_update_tray(success):
            log("%s.after_draw_update_tray(%s)", self, success)
            if not success:
                log.warn("after_draw_update_tray(%s) options=%s", success, options)
                return
            tray_data = self._backing.data
            if tray_data is None:
                log.warn("TrayBacking does not have any pixel data!")
                return
            self.idle_add(self.set_tray_icon, tray_data)
            self.idle_add(self.reconfigure)
        callbacks.append(after_draw_update_tray)
        self._backing.draw_region(x, y, width, height, coding, img_data, rowstride, options, callbacks)

    def set_tray_icon(self, tray_data):
        enc, w, h, rowstride, pixels = tray_data
        log("%s.set_tray_icon(%s, %s, %s, %s, %s bytes)", self, enc, w, h, rowstride, len(pixels))
        has_alpha = enc=="rgb32"
        self.tray_widget.set_icon_from_data(pixels, has_alpha, w, h, rowstride)


    def destroy(self):
        if self.tray_widget:
            self.tray_widget.cleanup()
            self.tray_widget = None

    def __repr__(self):
        return "ClientTray(%s)" % self._id


class TrayBacking(GTKWindowBacking):
    """
        This backing only stores the rgb pixels so
        we can use them with the real widget.
    """

    def __init__(self, wid, w, h, has_alpha):
        self.data = None
        GTKWindowBacking.__init__(self, wid)

    def _do_paint_rgb24(self, img_data, x, y, width, height, rowstride, options, callbacks):
        log("TrayBacking._do_paint_rgb24%s", ("%s bytes" % len(img_data), x, y, width, height, rowstride, options, callbacks))
        self.data = ["rgb24", width, height, rowstride, img_data[:]]
        return True

    def _do_paint_rgb32(self, img_data, x, y, width, height, rowstride, options, callbacks):
        log("TrayBacking._do_paint_rgb32%s", ("%s bytes" % len(img_data), x, y, width, height, rowstride, options, callbacks))
        self.data = ["rgb32", width, height, rowstride, img_data[:]]
        return True
