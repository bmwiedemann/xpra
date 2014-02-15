# This file is part of Xpra.
# Copyright (C) 2012 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2012-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.log import Logger
log = Logger("opengl", "window")

from xpra.client.gtk2.client_window import ClientWindow
from xpra.client.gl.gl_window_backing import GLPixmapBacking, log


class GLClientWindow(ClientWindow):

    gl_pixmap_backing_class = GLPixmapBacking

    def __init__(self, client, group_leader, wid, x, y, w, h, metadata, override_redirect, client_properties, auto_refresh_delay):
        log("GLClientWindow(..)")
        ClientWindow.__init__(self, client, group_leader, wid, x, y, w, h, metadata, override_redirect, client_properties, auto_refresh_delay)
        self._client_properties["encoding.uses_swscale"] = False
        self.set_reallocate_redraws(True)
        self.add(self._backing._backing)

    def __str__(self):
        return "GLClientWindow(%s : %s)" % (self._id, self._backing)

    def is_GL(self):
        return True

    def set_alpha(self):
        ClientWindow.set_alpha(self)
        rgb_formats = self._client_properties.get("encodings.rgb_formats", [])
        #gl_window_backing supports BGR(A) too:
        if "RGBA" in rgb_formats:
            rgb_formats.append("BGRA")
        if "RGB" in rgb_formats:
            rgb_formats.append("BGR")
            #TODO: we could handle BGRX as BGRA too...
            #rgb_formats.append("BGRX")

    def spinner(self, ok):
        if not self._backing.paint_screen or not self._backing._backing or not self.can_have_spinner():
            return
        w, h = self.get_size()
        if ok:
            self._backing.gl_expose_event(self._backing._backing, "spinner: fake event")
            self.queue_draw(0, 0, w, h)
        else:
            import gtk.gdk
            window = self._backing._backing.get_window()
            context = window.cairo_create()
            self.paint_spinner(context, gtk.gdk.Rectangle(0, 0, w, h))

    def do_expose_event(self, event):
        log("GL do_expose_event(%s)", event)

    def do_configure_event(self, event):
        log("GL do_configure_event(%s)", event)
        ClientWindow.do_configure_event(self, event)
        self._backing.paint_screen = True

    def destroy(self):
        self._backing.paint_screen = False
        ClientWindow.destroy(self)

    def new_backing(self, w, h):
        self._backing = self.make_new_backing(self.gl_pixmap_backing_class, w, h)
