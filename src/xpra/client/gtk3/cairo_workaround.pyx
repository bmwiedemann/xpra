# This file is part of Xpra.
# Copyright (C) 2014-2017 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

# What is this workaround you ask?
# Well, pycairo can't handle raw pixel data in RGB format,
# and that's despite the documentation saying otherwise:
# http://cairographics.org/pythoncairopil/
# Because of this glaring omission, we would have to roundtrip via PNG.
# This workaround populates an image surface with RGB pixels using Cython.
# (not the most efficient implementation, but still 10 times faster than the alternative)
#
#"cairo.ImageSurface.create_for_data" is not implemented in GTK3!
# http://cairographics.org/documentation/pycairo/3/reference/surfaces.html#cairo.ImageSurface.create_for_data
# "Not yet available in Python 3"
#
#It is available in the cffi cairo bindings, which can be used instead of pycairo
# but then we can't use it from the draw callbacks:
# https://mail.gnome.org/archives/python-hackers-list/2011-December/msg00004.html
# "PyGObject just lacks the glue code that allows it to pass the statically-wrapped
# cairo.Pattern to introspected methods"


#cython: boundscheck=False
from __future__ import absolute_import


import cairo
from xpra.buffers.membuf cimport object_as_buffer


cdef extern from "Python.h":
    ctypedef struct PyObject:
        pass
    void * PyCapsule_Import(const char *name, int no_block)
    object PyBuffer_FromMemory(void *ptr, Py_ssize_t size)
    int PyObject_AsReadBuffer(object obj, void ** buffer, Py_ssize_t * buffer_len) except -1

cdef extern from "cairo/cairo.h":
    ctypedef struct cairo_surface_t:
        pass

    #typedef enum _cairo_format {
    ctypedef enum cairo_format_t:
        CAIRO_FORMAT_INVALID
        CAIRO_FORMAT_ARGB32
        CAIRO_FORMAT_RGB24
        CAIRO_FORMAT_A8
        CAIRO_FORMAT_A1
        CAIRO_FORMAT_RGB16_565
        CAIRO_FORMAT_RGB30

    unsigned char * cairo_image_surface_get_data(cairo_surface_t *surface)
    cairo_format_t cairo_image_surface_get_format(cairo_surface_t *surface)

    int cairo_image_surface_get_width (cairo_surface_t *surface)
    int cairo_image_surface_get_height (cairo_surface_t *surface)
    int cairo_image_surface_get_stride (cairo_surface_t *surface)


cdef extern from "pycairo/pycairo.h":
    ctypedef struct Pycairo_CAPI_t:
        pass
    ctypedef struct PycairoSurface:
        #PyObject_HEAD
        cairo_surface_t *surface
        #PyObject *base; /* base object used to create surface, or NULL */
    ctypedef PycairoSurface PycairoImageSurface

CAIRO_FORMAT = {
        CAIRO_FORMAT_INVALID    : "Invalid",
        CAIRO_FORMAT_ARGB32     : "ARGB32",
        CAIRO_FORMAT_RGB24      : "RGB24",
        CAIRO_FORMAT_A8         : "A8",
        CAIRO_FORMAT_A1         : "A1",
        CAIRO_FORMAT_RGB16_565  : "RGB16_565",
        CAIRO_FORMAT_RGB30      : "RGB30",
        }

def set_image_surface_data(object image_surface, rgb_format, object pixel_data, int width, int height, int stride):
    #convert pixel_data to a C buffer:
    cdef const unsigned char * cbuf = <unsigned char *> 0
    cdef Py_ssize_t cbuf_len = 0
    assert object_as_buffer(pixel_data, <const void**> &cbuf, &cbuf_len)==0, "cannot convert %s to a readable buffer" % type(pixel_data)
    assert cbuf_len>=height*stride, "pixel buffer is too small for %sx%s with stride=%s: only %s bytes, expected %s" % (width, height, stride, cbuf_len, height*stride)
    #convert cairo.ImageSurface python object to a cairo_surface_t
    if not isinstance(image_surface, cairo.ImageSurface):
        raise TypeError("object %r is not a %r" % (image_surface, cairo.ImageSurface))
    cdef cairo_surface_t * surface = (<PycairoImageSurface *> image_surface).surface
    cdef unsigned char * data = cairo_image_surface_get_data(surface)
    #get surface attributes:
    cdef cairo_format_t format = cairo_image_surface_get_format(surface)
    cdef int istride    = cairo_image_surface_get_stride(surface)
    cdef int iwidth     = cairo_image_surface_get_width(surface)
    cdef int iheight    = cairo_image_surface_get_height(surface)
    assert iwidth>=width and iheight>=height, "invalid image surface: expected at least %sx%s but got %sx%s" % (width, height, iwidth, iheight)
    assert istride>=iwidth*4, "invalid image stride: expected at least %s but got %s" % (iwidth*4, istride)
    cdef int x,y
    cdef srci, dsti
    #just deal with the formats we care about:
    if format==CAIRO_FORMAT_RGB24 and rgb_format=="RGB":
        #cairo's RGB24 format is actually stored as BGR!
        for y in range(height):
            for x in range(width):
                srci = x*4 + y*istride
                dsti = x*3 + y*stride
                data[srci + 0] = cbuf[dsti + 2]     #B
                data[srci + 1] = cbuf[dsti + 1]     #G
                data[srci + 2] = cbuf[dsti + 0]     #R
                data[srci + 3] = 255                #X
        return
    #note: this one is currently unused because it doesn't work
    #and I don't know why
    #(we just disable 'rgb32' for gtk3... and fallback to png)
    elif format==CAIRO_FORMAT_ARGB32 and rgb_format=="RGBA":
        for x in range(width):
            for y in range(height):
                data[x*4 + 0 + y*istride] = cbuf[x*4 + 3 + y*stride]    #A
                data[x*4 + 1 + y*istride] = cbuf[x*4 + 0 + y*stride]    #R
                data[x*4 + 2 + y*istride] = cbuf[x*4 + 1 + y*stride]    #G
                data[x*4 + 3 + y*istride] = cbuf[x*4 + 2 + y*stride]    #B
        return
    print("ERROR: cairo workaround not implemented for %s / %s" % (rgb_format, CAIRO_FORMAT.get(format, "unknown")))

cdef Pycairo_CAPI_t * Pycairo_CAPI
Pycairo_CAPI = <Pycairo_CAPI_t*> PyCapsule_Import("cairo.CAPI", 0);
