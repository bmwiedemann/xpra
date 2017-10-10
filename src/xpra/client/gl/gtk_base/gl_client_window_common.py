# This file is part of Xpra.
# Copyright (C) 2012 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2012-2017 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.log import Logger
log = Logger("opengl", "window")

from collections import namedtuple

Rectangle = namedtuple("Rectangle", "x,y,width,height")
DrawEvent = namedtuple("DrawEvent", "area")


#common methods used by both GTK2 and GTK3 GLClientWindow implementations
class GLClientWindowCommon(object):

    def __repr__(self):
        return "GLClientWindow(%s : %s)" % (self._id, self._backing)

    def get_backing_class(self):
        raise NotImplementedError()

    def add_rgb_formats(self, rgb_formats):
        #gl_window_backing supports BGR(A) too:
        if "RGBA" in rgb_formats:
            rgb_formats.append("BGRA")
        if "RGB" in rgb_formats:
            rgb_formats.append("BGR")
        #TODO: we could handle BGRX as BGRA too...
        #rgb_formats.append("BGRX")

    def is_GL(self):
        return True

    def spinner(self, ok):
        b = self._backing
        log("spinner(%s) opengl window %s: backing=%s", ok, self._id, b)
        if not b:
            return
        b.paint_spinner = self.can_have_spinner() and not ok
        log("spinner(%s) backing=%s, paint_screen=%s, paint_spinner=%s", ok, b._backing, b.paint_screen, b.paint_spinner)
        if b._backing and b.paint_screen:
            w, h = self.get_size()
            self.queue_draw(0, 0, w, h)

    def queue_draw(self, x, y, w, h):
        b = self._backing
        if not b:
            return
        rect = (x, y, w, h)
        b.gl_expose_rect(rect)

    def do_expose_event(self, event):
        log("GL do_expose_event(%s)", event)

    def remove_backing(self):
        b = self._backing
        if b:
            self._backing = None
            b.paint_screen = False
            b.close()
            glarea = b._backing
            if glarea:
                try:
                    self.remove(glarea)
                except:
                    pass

    def toggle_debug(self, *_args):
        b = self._backing
        if not b:
            return
        if b.paint_box_line_width>0:
            b.paint_box_line_width = 0
        else:
            b.paint_box_line_width = b.default_paint_box_line_width

    def magic_key(self, *args):
        b = self._backing
        if self.border:
            self.border.toggle()
            if b:
                with b.gl_context():
                    b.gl_init()
                    b.present_fbo(0, 0, *b.size)
                self.queue_draw(0, 0, *self._size)
        log("gl magic_key%s border=%s, backing=%s", args, self.border, b)
