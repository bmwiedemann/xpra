# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2013 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008, 2010 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.


from gi.repository import GObject               #@UnresolvedImport @UnusedImport
from gi.repository import Gtk                   #@UnresolvedImport @UnusedImport
from gi.repository import Gdk                   #@UnresolvedImport @UnusedImport

from xpra.client.gtk_base.cairo_backing import CairoBacking
from xpra.client.gtk_base.gtk_client_window_base import GTKClientWindowBase, HAS_X11_BINDINGS
from xpra.client.client_window_base import DRAW_DEBUG
from xpra.log import Logger
log = Logger()


"""
GTK3 version of the ClientWindow class
"""
class ClientWindow(GTKClientWindowBase):

    WINDOW_POPUP = Gtk.WindowType.POPUP
    WINDOW_TOPLEVEL = Gtk.WindowType.TOPLEVEL
    #where have those values gone?
    #gi/pygtk3 docs are terrible for this
    WINDOW_EVENT_MASK = 0
    OR_TYPE_HINTS = []
    NAME_TO_HINT = { }
    SCROLL_MAP = {}

    WINDOW_STATE_FULLSCREEN = Gdk.WindowState.FULLSCREEN
    WINDOW_STATE_MAXIMIZED = Gdk.WindowState.MAXIMIZED


    def init_window(self, metadata):
        #TODO: no idea how to do the window-type with gtk3
        #maybe not even be possible..
        Gtk.Window.__init__(self)
        GTKClientWindowBase.init_window(self, metadata)
        # tell KDE/oxygen not to intercept clicks
        # see: https://bugs.kde.org/show_bug.cgi?id=274485
        # does not work with gtk3? what the??
        #self.set_data(strtobytes("_kde_no_window_grab"), 1)

    def new_backing(self, w, h):
        self._backing = self.make_new_backing(CairoBacking, w, h)


    def xget_u32_property(self, target, name):
        try:
            if not HAS_X11_BINDINGS:
                name_atom = Gdk.Atom.intern(name, False)
                type_atom = Gdk.Atom.intern("CARDINAL", False)
                prop = Gdk.property_get(target, name_atom, type_atom, 0, 9999, False)
                if not prop or len(prop)!=3 or len(prop[2])!=1:
                    return  None
                log("xget_u32_property(%s, %s)=%s", target, name, prop[2][0])
                return prop[2][0]
        except Exception, e:
            log.error("xget_u32_property error on %s / %s: %s", target, name, e)
        return GTKClientWindowBase.xget_u32_property(self, target, name)

    def is_mapped(self):
        return self.get_mapped()

    def get_window_geometry(self):
        x, y = self.get_position()
        w, h = self.get_size()
        return (x, y, w, h)

    def apply_geometry_hints(self, hints):
        """ we convert the hints as a dict into a gdk.Geometry + gdk.WindowHints """
        wh = Gdk.WindowHints
        name_to_hint = {"maximum-size"  : wh.MAX_SIZE,
                        "max_width"     : wh.MAX_SIZE,
                        "max_height"    : wh.MAX_SIZE,
                        "minimum-size"  : wh.MIN_SIZE,
                        "min_width"     : wh.MIN_SIZE,
                        "min_height"    : wh.MIN_SIZE,
                        "base-size"     : wh.BASE_SIZE,
                        "base_width"    : wh.BASE_SIZE,
                        "base_height"   : wh.BASE_SIZE,
                        "increment"     : wh.RESIZE_INC,
                        "width_inc"     : wh.RESIZE_INC,
                        "height_inc"    : wh.RESIZE_INC,
                        "min_aspect_ratio"  : wh.ASPECT,
                        "max_aspect_ratio"  : wh.ASPECT,
                        }
        #these fields can be copied directly to the gdk.Geometry as ints:
        INT_FIELDS= ["min_width",    "min_height",
                        "max_width",    "max_height",
                        "base_width",   "base_height",
                        "width_inc",    "height_inc"]
        ASPECT_FIELDS = {
                        "min_aspect_ratio"  : "min_aspect",
                        "max_aspect_ratio"  : "max_aspect",
                         }
        geom = Gdk.Geometry()
        mask = 0
        for k,v in hints.items():
            if k in INT_FIELDS:
                setattr(geom, k, int(v))
                mask |= int(name_to_hint.get(k, 0))
            elif k in ASPECT_FIELDS:
                field = ASPECT_FIELDS.get(k)
                setattr(geom, field, float(v))
                mask |= int(name_to_hint.get(k, 0))
        hints = Gdk.WindowHints(mask)
        self.set_geometry_hints(None, geom, hints)


    def queue_draw(self, x, y, width, height):
        self.queue_draw_area(x, y, width, height)

    def do_draw(self, context):
        if DRAW_DEBUG:
            log.info("do_draw(%s)", context)
        if self.get_mapped() and self._backing:
            self._backing.cairo_draw(context)


GObject.type_register(ClientWindow)
