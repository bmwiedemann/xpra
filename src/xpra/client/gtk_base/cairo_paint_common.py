# This file is part of Xpra.
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2012-2020 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import cairo
from gi.repository import Gdk

from xpra.os_util import monotonic_time, memoryview_to_bytes
from xpra.gtk_common.gtk_util import get_pixbuf_from_data
from xpra.codecs.argb.argb import unpremultiply_argb    #@UnresolvedImport
from xpra.log import Logger

log = Logger("paint", "mouse")


def setup_cairo_context(context, ww : int, wh : int, w : int, h : int, offset_x : int=0, offset_y : int=0):
    if w!=ww or h!=wh:
        context.scale(ww/w, wh/h)
    if offset_x!=0 or offset_y!=0:
        context.translate(offset_x, offset_y)
    context.set_operator(cairo.OPERATOR_SOURCE)

def cairo_paint_pointer_overlay(context, cursor_data, px : int, py : int, start_time):
    if not cursor_data:
        return
    elapsed = max(0, monotonic_time()-start_time)
    if elapsed>6:
        return
    cw = cursor_data[3]
    ch = cursor_data[4]
    xhot = cursor_data[5]
    yhot = cursor_data[6]
    pixels = cursor_data[8]
    x = px-xhot
    y = py-yhot
    alpha = max(0, (5.0-elapsed)/5.0)
    log("cairo_paint_pointer_overlay%s drawing pointer with cairo, alpha=%s",
        (context, x, y, start_time), alpha)
    context.translate(x, y)
    context.rectangle(0, 0, cw, ch)
    argb = unpremultiply_argb(pixels)
    img_data = memoryview_to_bytes(argb)
    pixbuf = get_pixbuf_from_data(img_data, True, cw, ch, cw*4)
    context.set_operator(cairo.OPERATOR_OVER)
    Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
    context.paint()
