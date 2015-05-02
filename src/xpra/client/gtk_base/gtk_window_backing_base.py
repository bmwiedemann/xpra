# This file is part of Xpra.
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2012, 2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import sys

#pygtk3 vs pygtk2 (sigh)
from xpra.gtk_common.gobject_compat import import_glib, import_cairo, is_gtk3
glib = import_glib()
cairo   = import_cairo()

from xpra.client.window_backing_base import WindowBackingBase
from xpra.log import Logger
log = Logger("paint")

#transparency with GTK:
# - on MS Windows: not supported
# - on OSX: only with gtk3
DEFAULT_HAS_ALPHA = not sys.platform.startswith("win") and (not sys.platform.startswith("darwin") or is_gtk3())
GTK_ALPHA_SUPPORTED = os.environ.get("XPRA_ALPHA", DEFAULT_HAS_ALPHA) in (True, "1")


"""
Generic GTK superclass for Backing code (for both GTK2 and GTK3),
see CairoBacking, PixmapBacking and TrayBacking for actual implementations.
(some may override HAS_ALPHA, TrayBacking does)
"""
class GTKWindowBacking(WindowBackingBase):

    HAS_ALPHA = GTK_ALPHA_SUPPORTED

    def __init__(self, wid, window_alpha):
        WindowBackingBase.__init__(self, wid, window_alpha and self.HAS_ALPHA, glib.idle_add)


    def cairo_draw(self, context):
        self.cairo_draw_from_drawable(context, self._backing)

    def cairo_draw_from_drawable(self, context, drawable):
        if drawable is None:
            return
        try:
            context.set_source_pixmap(drawable, 0, 0)
            context.set_operator(cairo.OPERATOR_SOURCE)
            context.paint()
            return True
        except KeyboardInterrupt:
            raise
        except:
            log.error("cairo_draw_from_drawable(%s, %s)", context, drawable, exc_info=True)
            return False
