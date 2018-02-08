# This file is part of Xpra.
# Copyright (C) 2008, 2009 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2010-2017 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from __future__ import absolute_import

import gobject
import gtk
from gtk import gdk

from xpra.log import Logger
log = Logger("bindings", "gtk")


from libc.stdint cimport uintptr_t


###################################
# Headers, python magic
###################################
cdef extern from "gdk/gdk.h":
    ctypedef unsigned int gboolean
    ctypedef char gchar
    GdkAtom gdk_atom_intern(const gchar *atom_name, gboolean only_if_exists)

# Serious black magic happens here (I owe these guys beers):
cdef extern from "pygobject.h":
    void pygobject_init(int req_major, int req_minor, int req_micro)
pygobject_init(-1, -1, -1)

cdef extern from "pygtk/pygtk.h":
    void init_pygtk()
init_pygtk()
# Now all the macros in those header files will work.

###################################
# GObject
###################################

cdef extern from "glib-2.0/glib-object.h":
    ctypedef struct cGObject "GObject":
        pass

cdef extern from "pygtk-2.0/pygobject.h":
    cGObject * pygobject_get(object box)
    object pygobject_new(cGObject * contents)

    ctypedef void* gpointer
    ctypedef int GType
    ctypedef struct PyGBoxed:
        #PyObject_HEAD
        gpointer boxed
        GType gtype

cdef cGObject * unwrap(box, pyclass) except? NULL:
    # Extract a raw GObject* from a PyGObject wrapper.
    assert issubclass(pyclass, gobject.GObject)
    if not isinstance(box, pyclass):
        raise TypeError("object %r is not a %r" % (box, pyclass))
    return pygobject_get(box)

# def print_unwrapped(box):
#     "For debugging the above."
#     cdef cGObject * unwrapped
#     unwrapped = unwrap(box, gobject.GObject)
#     if unwrapped == NULL:
#         print("contents is NULL!")
#     else:
#         print("contents is %s" % (<long long>unwrapped))

cdef object wrap(cGObject * contents):
    # Put a raw GObject* into a PyGObject wrapper.
    return pygobject_new(contents)

cdef extern from "glib/gmem.h":
    #void g_free(gpointer mem)
    ctypedef unsigned long gsize
    gpointer g_malloc(gsize n_bytes)



######
# GDK primitives, and wrappers for Xlib
######

# gdk_region_get_rectangles (pygtk bug #517099)
cdef extern from "gtk-2.0/gdk/gdktypes.h":
    ctypedef struct cGdkDisplay "GdkDisplay":
        pass

    ctypedef struct cGdkWindow "GdkWindow":
        pass

    ctypedef struct _GdkAtom:
        pass
    ctypedef _GdkAtom* GdkAtom


cdef extern from "gtk-2.0/gtk/gtkselection.h":
    ctypedef int gint
    ctypedef unsigned char guchar
    ctypedef struct GtkSelectionData:
        GdkAtom       selection
        GdkAtom       target
        GdkAtom       type
        gint          format
        guchar        *data
        gint          length
        cGdkDisplay   *display


cpdef get_display_for(obj):
    if obj is None:
        raise TypeError("Cannot get a display: instance is None!")
    if isinstance(obj, gdk.Display):
        return obj
    elif isinstance(obj, (gdk.Drawable,
                          gtk.Widget,
                          gtk.Clipboard,
                          gtk.SelectionData,
                          )):
        return obj.get_display()
    else:
        raise TypeError("Don't know how to get a display from %r" % (obj,))


cdef cGdkDisplay * get_raw_display_for(obj) except? NULL:
    return <cGdkDisplay*> unwrap(get_display_for(obj), gdk.Display)


cdef void * pyg_boxed_get(v):
    cdef PyGBoxed * pygboxed = <PyGBoxed *> v
    return <void *> pygboxed.boxed

def sanitize_gtkselectiondata(obj):
    log("get_gtkselectiondata(%s) type=%s", obj, type(obj))
    cdef GtkSelectionData * selectiondata = <GtkSelectionData *> pyg_boxed_get(obj)
    if selectiondata==NULL:
        return False
    log("selectiondata: selection=%#x, target=%#x, type=%#x, format=%#x, length=%#x, data=%#x",
        <uintptr_t> selectiondata.selection, <uintptr_t> selectiondata.target, <uintptr_t> selectiondata.type, selectiondata.format, selectiondata.length, <uintptr_t> selectiondata.data)
    cdef GdkAtom gdkatom
    cdef gpointer data
    cdef char* c
    if (<uintptr_t> selectiondata.type)>=2**16:
        log.warn("Warning: sanitizing invalid gtk selection type atom %#x", <uintptr_t> selectiondata.type)
        data = g_malloc(16)
        assert data!=NULL
        c = <char *> data
        for i in range(16):
            c[i] = 0
        gdkatom = gdk_atom_intern(b"STRING", 0)
        selectiondata.length = 0
        selectiondata.format = 8
        selectiondata.type = gdkatom
        selectiondata.data = <guchar*> data
        return True
    return False


cdef extern from "gtk-2.0/gdk/gdkwindow.h":
    ctypedef struct cGdkGeometry "GdkGeometry":
        int min_width, min_height, max_width, max_height,
        int base_width, base_height, width_inc, height_inc
        double min_aspect, max_aspect
    void gdk_window_constrain_size(cGdkGeometry *geometry,
                                   unsigned int flags, int width, int height,
                                   int * new_width, int * new_height)

def calc_constrained_size(int width, int height, object hints):
    if hints is None:
        return width, height

    cdef cGdkGeometry geom
    cdef int new_width = 0, new_height = 0
    cdef int new_larger_width = 0, new_larger_height = 0
    cdef int flags = 0

    if "maximum-size" in hints:
        flags = flags | gdk.HINT_MAX_SIZE
        geom.max_width, geom.max_height = hints["maximum-size"]
    if "minimum-size" in hints:
        flags = flags | gdk.HINT_MIN_SIZE
        geom.min_width, geom.min_height = hints["minimum-size"]
    if "base-size" in hints:
        flags = flags | gdk.HINT_BASE_SIZE
        geom.base_width, geom.base_height = hints["base-size"]
    if "increment" in hints:
        flags = flags | gdk.HINT_RESIZE_INC
        geom.width_inc, geom.height_inc = hints["increment"]
    if "min_aspect" in hints:
        assert "max_aspect" in hints
        flags = flags | gdk.HINT_ASPECT
        geom.min_aspect = hints["min_aspect"]
        geom.max_aspect = hints["max_aspect"]
    gdk_window_constrain_size(&geom, flags, width, height, &new_width, &new_height)
    return new_width, new_height
