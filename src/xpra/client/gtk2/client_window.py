# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2013 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008, 2010 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import gobject
import gtk
from gtk import gdk

from xpra.client.gtk_base.gtk_client_window_base import GTKClientWindowBase, HAS_X11_BINDINGS
from xpra.client.client_window_base import DRAW_DEBUG

USE_CAIRO = os.environ.get("XPRA_USE_CAIRO_BACKING", "0")=="1"
if USE_CAIRO:
    from xpra.client.gtk_base.cairo_backing import CairoBacking
    BACKING_CLASS = CairoBacking
else:
    from xpra.client.gtk2.pixmap_backing import PixmapBacking
    BACKING_CLASS = PixmapBacking


"""
GTK2 version of the ClientWindow class
"""
class ClientWindow(GTKClientWindowBase):

    WINDOW_EVENT_MASK = gdk.STRUCTURE_MASK | gdk.KEY_PRESS_MASK | gdk.KEY_RELEASE_MASK \
            | gdk.POINTER_MOTION_MASK | gdk.BUTTON_PRESS_MASK | gdk.BUTTON_RELEASE_MASK \
            | gdk.PROPERTY_CHANGE_MASK

    OR_TYPE_HINTS = [gdk.WINDOW_TYPE_HINT_DIALOG,
                gdk.WINDOW_TYPE_HINT_MENU, gdk.WINDOW_TYPE_HINT_TOOLBAR,
                #gdk.WINDOW_TYPE_HINT_SPLASHSCREEN, gdk.WINDOW_TYPE_HINT_UTILITY,
                #gdk.WINDOW_TYPE_HINT_DOCK, gdk.WINDOW_TYPE_HINT_DESKTOP,
                gdk.WINDOW_TYPE_HINT_DROPDOWN_MENU, gdk.WINDOW_TYPE_HINT_POPUP_MENU,
                gdk.WINDOW_TYPE_HINT_TOOLTIP,
                #gdk.WINDOW_TYPE_HINT_NOTIFICATION,
                gdk.WINDOW_TYPE_HINT_COMBO,gdk.WINDOW_TYPE_HINT_DND]
    NAME_TO_HINT = {
                "NORMAL"        : gdk.WINDOW_TYPE_HINT_NORMAL,
                "DIALOG"        : gdk.WINDOW_TYPE_HINT_DIALOG,
                "MENU"          : gdk.WINDOW_TYPE_HINT_MENU,
                "TOOLBAR"       : gdk.WINDOW_TYPE_HINT_TOOLBAR,
                "SPLASH"        : gdk.WINDOW_TYPE_HINT_SPLASHSCREEN,
                "UTILITY"       : gdk.WINDOW_TYPE_HINT_UTILITY,
                "DOCK"          : gdk.WINDOW_TYPE_HINT_DOCK,
                "DESKTOP"       : gdk.WINDOW_TYPE_HINT_DESKTOP,
                "DROPDOWN_MENU" : gdk.WINDOW_TYPE_HINT_DROPDOWN_MENU,
                "POPUP_MENU"    : gdk.WINDOW_TYPE_HINT_POPUP_MENU,
                "TOOLTIP"       : gdk.WINDOW_TYPE_HINT_TOOLTIP,
                "NOTIFICATION"  : gdk.WINDOW_TYPE_HINT_NOTIFICATION,
                "COMBO"         : gdk.WINDOW_TYPE_HINT_COMBO,
                "DND"           : gdk.WINDOW_TYPE_HINT_DND
                }
    # Map scroll directions back to mouse buttons.  Mapping is taken from
    # gdk/x11/gdkevents-x11.c.
    SCROLL_MAP = {gdk.SCROLL_UP: 4,
                  gdk.SCROLL_DOWN: 5,
                  gdk.SCROLL_LEFT: 6,
                  gdk.SCROLL_RIGHT: 7,
                  }

    def init_window(self, metadata):
        if self._override_redirect:
            gtk.Window.__init__(self, gtk.WINDOW_POPUP)
        else:
            gtk.Window.__init__(self, gtk.WINDOW_TOPLEVEL)
        GTKClientWindowBase.init_window(self, metadata)

    def set_alpha(self):
        self.debug("set_alpha()")
        if self._has_alpha:
            screen = self.get_screen()
            rgba = screen.get_rgba_colormap()
            if rgba is None:
                self.error("cannot handle window transparency!")
            else:
                self.set_colormap(rgba)

    def set_modal(self, modal):
        #with gtk2 setting the window as modal would prevent
        #all other windows we manage from receiving input
        #including other unrelated applications
        #what we want is "window-modal"
        self.debug("set_modal(%s) swallowed", modal)

    def new_backing(self, w, h):
        self._backing = self.make_new_backing(BACKING_CLASS, w, h)

    def xget_u32_property(self, target, name):
        try:
            if not HAS_X11_BINDINGS:
                prop = target.property_get(name)
                if not prop or len(prop)!=3 or len(prop[2])!=1:
                    return  None
                self.debug("xget_u32_property(%s, %s)=%s", target, name, prop[2][0])
                return prop[2][0]
        except Exception, e:
            self.error("xget_u32_property error on %s / %s: %s", target, name, e)
        return GTKClientWindowBase.xget_u32_property(self, target, name)

    def is_mapped(self):
        return self.window is not None and self.window.is_visible()

    def gdk_window(self):
        if gtk.gtk_version>=(2,14):
            return self.get_window()
        else:
            return self.window

    def get_window_geometry(self):
        gdkwindow = self.gdk_window()
        x, y = gdkwindow.get_origin()
        oL, oT, oR, oB, = self._offset
        _, _, w, h, _ = gdkwindow.get_geometry()
        return (x+oL, y+oT, w-oL-oR, h-oT-oB)

    def apply_geometry_hints(self, hints):
        self.set_geometry_hints(None, **hints)

    def queue_draw(self, x, y, width, height):
        window = self.gdk_window()
        if window:
            window.invalidate_rect(gdk.Rectangle(x, y, width, height), False)
        else:
            self.warn("ignoring draw received for a window which is not realized yet!")

    def do_expose_event(self, event):
        if DRAW_DEBUG:
            #cannot use self
            self.debug("do_expose_event(%s) area=%s", event, event.area)
        if not (self.flags() & gtk.MAPPED) or self._backing is None:
            return
        w,h = self.window.get_size()
        if w>=32768 or h>=32768:
            self.error("cannot paint on window which is too large: %sx%s !", w, h)
            return
        context = self.window.cairo_create()
        context.rectangle(event.area)
        context.clip()
        if self._offset!=(0, 0, 0, 0):
            self.debug("do_expose_event paint offset: %s", self._offset)
            self.paint_offset(event, context)
            #clip to offset:
            oL, oT = self._offset[:2]
            w, h = self._size
            context.rectangle(oL, oT, w, h)
            context.clip()
            context.translate(*self._offset[:2])
        self._backing.cairo_draw(context)
        if not self._client.server_ok():
            self.paint_spinner(context, event.area)

    def paint_offset(self, event, context):
        self.warn("paint_offset(%s, %s) not implemented in %s", event, context, type(self))

gobject.type_register(ClientWindow)
