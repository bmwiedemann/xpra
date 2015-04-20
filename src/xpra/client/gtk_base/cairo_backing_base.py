# This file is part of Xpra.
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2012-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.gtk_common.gobject_compat import import_gdk, import_gobject, import_pixbufloader, import_cairo
gdk             = import_gdk()
gobject         = import_gobject()
cairo           = import_cairo()
PixbufLoader    = import_pixbufloader()

from xpra.gtk_common.gtk_util import cairo_set_source_pixbuf, gdk_cairo_context
from xpra.client.gtk_base.gtk_window_backing_base import GTKWindowBacking
from xpra.codecs.loader import get_codec
from xpra.os_util import BytesIOClass, memoryview_to_bytes

from xpra.log import Logger
log = Logger("paint", "cairo")


FORMATS = {-1   : "INVALID"}
for x in (f for f in dir(cairo) if f.startswith("FORMAT_")):
    FORMATS[getattr(cairo, x)] = x.replace("FORMAT_", "")


"""
Superclass for gtk2 and gtk3 cairo implementations.
"""
class CairoBackingBase(GTKWindowBacking):


    def __init__(self, wid, w, h, has_alpha):
        GTKWindowBacking.__init__(self, wid, has_alpha)

    def init(self, w, h):
        old_backing = self._backing
        #should we honour self.depth here?
        self._backing = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(self._backing)
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.set_source_rgba(1, 1, 1, 1)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        if old_backing is not None:
            # Really we should respect bit-gravity here but... meh.
            old_w = old_backing.get_width()
            old_h = old_backing.get_height()
            cr.set_operator(cairo.OPERATOR_SOURCE)
            if w>old_w and h>old_h:
                #both width and height are bigger:
                cr.rectangle(old_w, 0, w-old_w, h)
                cr.fill()
                cr.new_path()
                cr.rectangle(0, old_h, old_w, h-old_h)
                cr.fill()
            elif w>old_w:
                #enlarged in width only
                cr.rectangle(old_w, 0, w-old_w, h)
                cr.fill()
            if h>old_h:
                #enlarged in height only
                cr.rectangle(0, old_h, w, h-old_h)
                cr.fill()
            #cr.set_operator(cairo.OPERATOR_CLEAR)
            cr.set_source_surface(old_backing, 0, 0)
            cr.paint()
            #old_backing.finish()

    def close(self):
        if self._backing:
            self._backing.finish()
        GTKWindowBacking.close(self)


    def cairo_paint_pixbuf(self, pixbuf, x, y):
        """ must be called from UI thread """
        log("cairo_paint_pixbuf(%s, %s, %s) backing=%s", pixbuf, x, y, self._backing)
        #now use it to paint:
        gc = gdk_cairo_context(cairo.Context(self._backing))
        cairo_set_source_pixbuf(gc, pixbuf, x, y)
        gc.paint()
        return True

    def cairo_paint_surface(self, img_surface, x, y):
        """ must be called from UI thread """
        log("cairo_paint_surface(%s, %s, %s)", img_surface, x, y)
        log("source image surface: %s", (img_surface.get_format(), img_surface.get_width(), img_surface.get_height(), img_surface.get_stride(), img_surface.get_content(), ))
        gc = gdk_cairo_context(cairo.Context(self._backing))
        gc.set_operator(cairo.OPERATOR_CLEAR)
        gc.rectangle(x, y, img_surface.get_width(), img_surface.get_height())
        gc.fill()
        gc.set_operator(cairo.OPERATOR_OVER)
        gc.set_source_surface(img_surface, x, y)
        gc.paint()
        return True


    def _do_paint_rgb24(self, img_data, x, y, width, height, rowstride, options):
        return self._do_paint_rgb(cairo.FORMAT_RGB24, False, img_data, x, y, width, height, rowstride, options)

    def _do_paint_rgb32(self, img_data, x, y, width, height, rowstride, options):
        return self._do_paint_rgb(cairo.FORMAT_ARGB32, True, img_data, x, y, width, height, rowstride, options)

    def _do_paint_rgb(self, *args):
        raise NotImplementedError()


    def nasty_rgb_via_png_paint(self, cairo_format, has_alpha, img_data, x, y, width, height, rowstride, rgb_format):
        log("nasty_rgb_via_png_paint%s", (cairo_format, has_alpha, len(img_data), x, y, width, height, rowstride, rgb_format))
        #PIL fallback
        PIL = get_codec("PIL")
        if has_alpha:
            oformat = "RGBA"
        else:
            oformat = "RGB"
        #use frombytes rather than frombuffer to be compatible with python3 new-style buffers
        #this is slower, but since this codepath is already dreadfully slow, we don't care
        bdata = memoryview_to_bytes(img_data)
        log("bdata=%s", type(bdata))
        img = PIL.Image.frombytes(oformat, (width,height), bdata, "raw", rgb_format, rowstride, 1)
        #This is insane, the code below should work, but it doesn't:
        # img_data = bytearray(img.tostring('raw', oformat, 0, 1))
        # pixbuf = pixbuf_new_from_data(img_data, COLORSPACE_RGB, True, 8, width, height, rowstride)
        # success = self.cairo_paint_pixbuf(pixbuf, x, y)
        #So we still rountrip via PNG:
        png = BytesIOClass()
        img.save(png, format="PNG")
        reader = BytesIOClass(png.getvalue())
        png.close()
        img = cairo.ImageSurface.create_from_png(reader)
        return self.cairo_paint_surface(img, x, y)


    def cairo_draw(self, context):
        log("cairo_draw(%s) backing=%s", context, self._backing)
        if self._backing is None:
            return False
        try:
            context.set_source_surface(self._backing, 0, 0)
            context.set_operator(cairo.OPERATOR_SOURCE)
            context.paint()
            return True
        except KeyboardInterrupt:
            raise
        except:
            log.error("cairo_draw(%s)", context, exc_info=True)
            return False
