# This file is part of Xpra.
# Copyright (C) 2008, 2009 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2012-2018 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

#cython: boundscheck=False, wraparound=False, cdivision=True
from __future__ import absolute_import

from xpra.os_util import bytestostr
from xpra.util import first_time
from xpra.buffers.membuf cimport getbuf, padbuf, MemBuf
from xpra.buffers.membuf cimport object_as_buffer, object_as_write_buffer

from libc.stdint cimport uint32_t, uint16_t, uint8_t

import struct
from xpra.log import Logger
log = Logger("encoding")

assert sizeof(int) == 4


cdef int as_buffer(object obj, const void ** buffer, Py_ssize_t * buffer_len) except -1:
    cdef size_t l
    if isinstance(obj, MemBuf):
        buffer[0] = <const void*> (<MemBuf> obj).get_mem()
        l = len(obj)
        buffer_len[0] = <Py_ssize_t> l
        return 0
    return object_as_buffer(obj, buffer, buffer_len)


cdef inline unsigned char clamp(int v):
    if v>255:
        return 255
    return <unsigned char> v


def bgr565_to_rgbx(buf):
    assert len(buf) % 2 == 0, "invalid buffer size: %s is not a multiple of 2" % len(buf)
    # buf is a Python buffer object
    cdef const uint16_t* cbuf = <const uint16_t*> 0
    cdef Py_ssize_t cbuf_len = 0
    assert as_buffer(buf, <const void**> &cbuf, &cbuf_len)==0, "cannot convert %s to a readable buffer" % type(buf)
    return bgr565data_to_rgbx(cbuf, cbuf_len)

cdef bgr565data_to_rgbx(const uint16_t* rgb565, const int rgb565_len):
    if rgb565_len <= 0:
        return None
    assert rgb565_len>0 and rgb565_len % 2 == 0, "invalid buffer size: %s is not a multiple of 2" % rgb565_len
    cdef MemBuf output_buf = padbuf(rgb565_len*2, 2)
    cdef uint32_t *rgbx = <uint32_t*> output_buf.get_mem()
    cdef uint16_t v
    cdef unsigned int i = 0
    cdef unsigned int l = rgb565_len//2
    for i in range(l):
        v = rgb565[i]
        rgbx[i] = 0xff000000 | (((v & 0xF800) >> 8) + ((v & 0x07E0) << 5) + ((v & 0x001F) << 19))
    return memoryview(output_buf)

def bgr565_to_rgb(buf):
    assert len(buf) % 2 == 0, "invalid buffer size: %s is not a multiple of 2" % len(buf)
    # buf is a Python buffer object
    cdef const uint16_t* cbuf = <const uint16_t*> 0
    cdef Py_ssize_t cbuf_len = 0
    assert as_buffer(buf, <const void**> &cbuf, &cbuf_len)==0, "cannot convert %s to a readable buffer" % type(buf)
    return bgr565data_to_rgb(cbuf, cbuf_len)

cdef bgr565data_to_rgb(const uint16_t* rgb565, const int rgb565_len):
    if rgb565_len <= 0:
        return None
    assert rgb565_len>0 and rgb565_len % 2 == 0, "invalid buffer size: %s is not a multiple of 2" % rgb565_len
    cdef MemBuf output_buf = padbuf(rgb565_len*3//2, 3)
    cdef uint8_t *rgb = <uint8_t*> output_buf.get_mem()
    cdef uint32_t v
    cdef unsigned int i = 0
    cdef unsigned int l = rgb565_len//2
    for i in range(l):
        v = rgb565[i]
        rgb[0] = (v & 0xF800) >> 8
        rgb[1] = (v & 0x07E0) >> 3
        rgb[2] = (v & 0x001F) << 3
        rgb += 3
    return memoryview(output_buf)


def r210_to_rgba(buf):
    assert len(buf) % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % len(buf)
    # buf is a Python buffer object
    cdef const unsigned int* cbuf = <const unsigned int *> 0
    cdef Py_ssize_t cbuf_len = 0
    assert as_buffer(buf, <const void**> &cbuf, &cbuf_len)==0, "cannot convert %s to a readable buffer" % type(buf)
    return r210data_to_rgba(cbuf, cbuf_len)

cdef r210data_to_rgba(const unsigned int* r210, const int r210_len):
    if r210_len <= 0:
        return None
    assert r210_len>0 and r210_len % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % r210_len
    cdef MemBuf output_buf = getbuf(r210_len)
    cdef unsigned char* rgba = <unsigned char*> output_buf.get_mem()
    #number of pixels:
    cdef int i = 0
    cdef unsigned int v
    while i < r210_len:
        v = r210[i//4]
        rgba[i+2] = (v&0x000003ff) >> 2
        rgba[i+1] = (v&0x000ffc00) >> 12
        rgba[i]   = (v&0x3ff00000) >> 22
        rgba[i+3] = ((v&(<unsigned int>0xc0000000)) >> 30)*85
        i = i + 4
    return memoryview(output_buf)


def r210_to_rgbx(buf):
    assert len(buf) % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % len(buf)
    # buf is a Python buffer object
    cdef const unsigned int* cbuf = <const unsigned int *> 0
    cdef Py_ssize_t cbuf_len = 0
    assert as_buffer(buf, <const void**> &cbuf, &cbuf_len)==0, "cannot convert %s to a readable buffer" % type(buf)
    return r210data_to_rgbx(cbuf, cbuf_len)

cdef r210data_to_rgbx(const unsigned int* r210, const int r210_len):
    if r210_len <= 0:
        return None
    assert r210_len>0 and r210_len % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % r210_len
    cdef MemBuf output_buf = getbuf(r210_len)
    cdef unsigned char* rgbx = <unsigned char*> output_buf.get_mem()
    #number of pixels:
    cdef int i = 0
    cdef unsigned int v
    while i < r210_len:
        v = r210[i//4]
        rgbx[i+2] = (v&0x000003ff) >> 2
        rgbx[i+1] = (v&0x000ffc00) >> 12
        rgbx[i]   = (v&0x3ff00000) >> 22
        rgbx[i+3] = 0xFF
        i = i + 4
    return memoryview(output_buf)


def r210_to_rgb(buf):
    assert len(buf) % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % len(buf)
    # buf is a Python buffer object
    cdef const unsigned int* cbuf = <const unsigned int *> 0
    cdef Py_ssize_t cbuf_len = 0
    assert as_buffer(buf, <const void**> &cbuf, &cbuf_len)==0, "cannot convert %s to a readable buffer" % type(buf)
    return r210data_to_rgb(cbuf, cbuf_len)

#white:  3fffffff
#red:    3ff00000
#green:     ffc00
#blue:        3ff
#black:         0
cdef r210data_to_rgb(const unsigned int* r210, const int r210_len):
    if r210_len <= 0:
        return None
    assert r210_len>0 and r210_len % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % r210_len
    cdef MemBuf output_buf = padbuf(r210_len//4*3, 3)
    cdef unsigned char* rgb = <unsigned char*> output_buf.get_mem()
    #number of pixels:
    cdef int s = 0
    cdef int d = 0
    cdef unsigned int v
    while s < r210_len//4:
        v = r210[s]
        rgb[d+2] = (v&0x000003ff) >> 2
        rgb[d+1] = (v&0x000ffc00) >> 12
        rgb[d]   = (v&0x3ff00000) >> 22
        s += 1
        d += 3
    return memoryview(output_buf)


def argb_to_rgba(buf):
    assert len(buf) % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % len(buf)
    # buf is a Python buffer object
    cdef const unsigned char * cbuf = <unsigned char *> 0
    cdef Py_ssize_t cbuf_len = 0
    assert as_buffer(buf, <const void**> &cbuf, &cbuf_len)==0, "cannot convert %s to a readable buffer" % type(buf)
    return argbdata_to_rgba(cbuf, cbuf_len)

cdef argbdata_to_rgba(const unsigned char* argb, const int argb_len):
    if argb_len <= 0:
        return None
    assert argb_len>0 and argb_len % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % argb_len
    cdef MemBuf output_buf = getbuf(argb_len)
    cdef unsigned char* rgba = <unsigned char*> output_buf.get_mem()
    #number of pixels:
    cdef int i = 0
    while i < argb_len:
        rgba[i]    = argb[i+1]              #R
        rgba[i+1]  = argb[i+2]              #G
        rgba[i+2]  = argb[i+3]              #B
        rgba[i+3]  = argb[i]                #A
        i = i + 4
    return memoryview(output_buf)

def argb_to_rgb(buf):
    assert len(buf) % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % len(buf)
    # buf is a Python buffer object
    cdef unsigned char * cbuf = <unsigned char *> 0     #@DuplicateSignature
    cdef Py_ssize_t cbuf_len = 0                        #@DuplicateSignature
    assert as_buffer(buf, <const void**> &cbuf, &cbuf_len)==0, "cannot convert %s to a readable buffer" % type(buf)
    return argbdata_to_rgb(cbuf, cbuf_len)

cdef argbdata_to_rgb(const unsigned char *argb, const int argb_len):
    if argb_len <= 0:
        return None
    assert argb_len>0 and argb_len % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % argb_len
    #number of pixels:
    cdef unsigned int mi = argb_len//4                #@DuplicateSignature
    #3 bytes per pixel:
    cdef MemBuf output_buf = padbuf(mi*3, 3)
    cdef unsigned char* rgb = <unsigned char*> output_buf.get_mem()
    cdef int i = 0, di = 0                          #@DuplicateSignature
    while i < argb_len:
        rgb[di]   = argb[i+1]               #R
        rgb[di+1] = argb[i+2]               #G
        rgb[di+2] = argb[i+3]               #B
        di += 3
        i += 4
    return memoryview(output_buf)


def bgra_to_rgb(buf):
    assert len(buf) % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % len(buf)
    # buf is a Python buffer object
    cdef unsigned char * bgra_buf = NULL    #@DuplicateSignature
    cdef Py_ssize_t bgra_buf_len = 0        #@DuplicateSignature
    assert as_buffer(buf, <const void**> &bgra_buf, &bgra_buf_len)==0, "cannot convert %s to a readable buffer" % type(buf)
    return bgradata_to_rgb(bgra_buf, bgra_buf_len)

cdef bgradata_to_rgb(const unsigned char* bgra, const int bgra_len):
    if bgra_len <= 0:
        return None
    assert bgra_len>0 and bgra_len % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % bgra_len
    #number of pixels:
    cdef int mi = bgra_len//4                #@DuplicateSignature
    #3 bytes per pixel:
    cdef MemBuf output_buf = padbuf(mi*3, 3)
    cdef unsigned char* rgb = <unsigned char*> output_buf.get_mem()
    cdef int di = 0, si = 0                  #@DuplicateSignature
    while si < bgra_len:
        rgb[di]   = bgra[si+2]              #R
        rgb[di+1] = bgra[si+1]              #G
        rgb[di+2] = bgra[si]                #B
        di += 3
        si += 4
    return memoryview(output_buf)

def bgra_to_rgba(buf):
    assert len(buf) % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % len(buf)
    # buf is a Python buffer object
    cdef unsigned char * bgra_buf2 = NULL
    cdef Py_ssize_t bgra_buf_len2 = 0
    assert as_buffer(buf, <const void**> &bgra_buf2, &bgra_buf_len2)==0, "cannot convert %s to a readable buffer" % type(buf)
    return bgradata_to_rgba(bgra_buf2, bgra_buf_len2)

cdef bgradata_to_rgba(const unsigned char* bgra, const int bgra_len):
    if bgra_len <= 0:
        return None
    assert bgra_len>0 and bgra_len % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % bgra_len
    #same number of bytes:
    cdef MemBuf output_buf = getbuf(bgra_len)
    cdef unsigned char* rgba = <unsigned char*> output_buf.get_mem()
    cdef int i = 0                      #@DuplicateSignature
    while i < bgra_len:
        rgba[i]   = bgra[i+2]           #R
        rgba[i+1] = bgra[i+1]           #G
        rgba[i+2] = bgra[i]             #B
        rgba[i+3] = bgra[i+3]           #A
        i += 4
    return memoryview(output_buf)

def rgba_to_bgra(buf):
    #same: just a swap
    return bgra_to_rgba(buf)


def premultiply_argb_in_place(buf):
    # b is a Python buffer object
    cdef unsigned int * cbuf = <unsigned int *> 0
    cdef Py_ssize_t cbuf_len = 0                #@DuplicateSignature
    assert len(buf) % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % len(buf)
    assert object_as_write_buffer(buf, <void **>&cbuf, &cbuf_len)==0
    do_premultiply_argb_in_place(cbuf, cbuf_len)

cdef do_premultiply_argb_in_place(unsigned int *buf, Py_ssize_t argb_len):
    # cbuf contains non-premultiplied ARGB32 data in native-endian.
    # We convert to premultiplied ARGB32 data, in-place.
    cdef unsigned char a, r, g, b
    cdef unsigned int argb
    assert argb_len>0 and argb_len % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % argb_len
    cdef int i
    for 0 <= i < argb_len / 4:
        argb = buf[i]
        a = (argb >> 24) & 0xff
        r = (argb >> 16) & 0xff
        r = r * a // 255
        g = (argb >> 8) & 0xff
        g = g * a // 255
        b = (argb >> 0) & 0xff
        b = b * a // 255
        buf[i] = (a << 24) | (r << 16) | (g << 8) | (b << 0)

def unpremultiply_argb_in_place(buf):
    # b is a Python buffer object
    cdef unsigned int * cbuf = <unsigned int *> 0   #@DuplicateSignature
    cdef Py_ssize_t cbuf_len = 0                    #@DuplicateSignature
    assert len(buf) % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % len(buf)
    assert object_as_write_buffer(buf, <void **>&cbuf, &cbuf_len)==0, "cannot convert %s to a writable buffer" % type(buf)
    do_unpremultiply_argb_in_place(cbuf, cbuf_len)

cdef do_unpremultiply_argb_in_place(unsigned int * buf, Py_ssize_t buf_len):
    # cbuf contains non-premultiplied ARGB32 data in native-endian.
    # We convert to premultiplied ARGB32 data, in-place.
    cdef unsigned char a, r, g, b                   #@DuplicateSignature
    cdef unsigned int argb                          #@DuplicateSignature
    assert buf_len>0 and buf_len % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % buf_len
    cdef int i                                      #@DuplicateSignature
    for 0 <= i < buf_len // 4:
        argb = buf[i]
        a = (argb >> 24) & 0xff
        if a==0:
            buf[i] = 0
            continue
        r = clamp(((argb >> 16) & 0xff) * 255 // a)
        g = clamp(((argb >> 8) & 0xff) * 255 // a)
        b = clamp(((argb >> 0) & 0xff) * 255 // a)
        buf[i] = (a << 24) | (r << 16) | (g << 8) | (b << 0)

def unpremultiply_argb(buf):
    # b is a Python buffer object
    cdef unsigned int * argb = <unsigned int *> 0   #@DuplicateSignature
    cdef Py_ssize_t argb_len = 0                    #@DuplicateSignature
    assert len(buf) % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % len(buf)
    assert as_buffer(buf, <const void **>&argb, &argb_len)==0
    return do_unpremultiply_argb(argb, argb_len)


#precalculate indexes in native endianness:
tmp = struct.pack("=L", 0 + 1*(2**8) + 2*(2**16) + 3*(2**24))
#little endian will give 0, 1, 2, 3
#big endian should give 3, 2, 1, 0 (untested)
cdef unsigned char B = tmp.index(b'\0')
cdef unsigned char G = tmp.index(b'\1')
cdef unsigned char R = tmp.index(b'\2')
cdef unsigned char A = tmp.index(b'\3')

cdef do_unpremultiply_argb(unsigned int * argb_in, Py_ssize_t argb_len):
    # cbuf contains non-premultiplied ARGB32 data in native-endian.
    # We convert to premultiplied ARGB32 data
    cdef unsigned char a, r, g, b                #@DuplicateSignature
    cdef unsigned int argb                      #@DuplicateSignature
    assert argb_len>0 and argb_len % 4 == 0, "invalid buffer size: %s is not a multiple of 4" % argb_len
    cdef MemBuf output_buf = getbuf(argb_len)
    cdef unsigned char* argb_out = <unsigned char*> output_buf.get_mem()
    cdef int i                                  #@DuplicateSignature
    for 0 <= i < argb_len // 4:
        argb = argb_in[i]
        a = (argb >> 24) & 0xff
        r = (argb >> 16) & 0xff
        g = (argb >> 8) & 0xff
        b = (argb >> 0) & 0xff
        if a!=0:
            r = clamp(r * 255 // a)
            g = clamp(g * 255 // a)
            b = clamp(b * 255 // a)
        else:
            r = 0
            g = 0
            b = 0
        #we could use struct pack to avoid endianness issues
        #but this is python 2.5 onwards only and is probably slower:
        #struct.pack_into("=BBBB", argb_out, i*4, b, g, r, a)
        argb_out[i*4+B] = b
        argb_out[i*4+G] = g
        argb_out[i*4+R] = r
        argb_out[i*4+A] = a
    return memoryview(output_buf)


def argb_swap(image, rgb_formats, supports_transparency):
    """ use the argb codec to do the RGB byte swapping """
    pixel_format = bytestostr(image.get_pixel_format())
    #try to fallback to argb module
    #if we have one of the target pixel formats:
    pixels = image.get_pixels()
    assert pixels, "failed to get pixels from %s" % image
    rs = image.get_rowstride()
    if pixel_format=="r210":
        #r210 never contains any transparency at present
        #if supports_transparency and "RGBA" in rgb_formats:
        #    log("argb_swap: r210_to_rgba for %s on %s", pixel_format, type(pixels))
        #    image.set_pixels(r210_to_rgba(pixels))
        #    image.set_pixel_format("RGBA")
        #    return True
        if "RGB" in rgb_formats:
            log("argb_swap: r210_to_rgb for %s on %s", pixel_format, type(pixels))
            image.set_pixels(r210_to_rgb(pixels))
            image.set_pixel_format("RGB")
            image.set_rowstride(rs*3//4)
            return True
        if "RGBX" in rgb_formats:
            log("argb_swap: r210_to_rgbx for %s on %s", pixel_format, type(pixels))
            image.set_pixels(r210_to_rgbx(pixels))
            image.set_pixel_format("RGBX")
            return True
    elif pixel_format=="BGR565":
        if "RGB" in rgb_formats:
            log("argb_swap: bgr565_to_rgb for %s on %s", pixel_format, type(pixels))
            image.set_pixels(bgr565_to_rgb(pixels))
            image.set_pixel_format("RGB")
            image.set_rowstride(rs*3//2)
            return True
        if "RGBX" in rgb_formats:
            log("argb_swap: bgr565_to_rgbx for %s on %s", pixel_format, type(pixels))
            image.set_pixels(bgr565_to_rgbx(pixels))
            image.set_pixel_format("RGBX")
            image.set_rowstride(rs*2)
            return True
    elif pixel_format in ("BGRX", "BGRA"):
        if supports_transparency and "RGBA" in rgb_formats:
            log("argb_swap: bgra_to_rgba for %s on %s", pixel_format, type(pixels))
            image.set_pixels(bgra_to_rgba(pixels))
            image.set_pixel_format("RGBA")
            return True
        if "RGB" in rgb_formats:
            log("argb_swap: bgra_to_rgb for %s on %s", pixel_format, type(pixels))
            image.set_pixels(bgra_to_rgb(pixels))
            image.set_pixel_format("RGB")
            image.set_rowstride(rs*3//4)
            return True
    elif pixel_format in ("XRGB", "ARGB"):
        if supports_transparency and "RGBA" in rgb_formats:
            log("argb_swap: argb_to_rgba for %s on %s", pixel_format, type(pixels))
            image.set_pixels(argb_to_rgba(pixels))
            image.set_pixel_format("RGBA")
            return True
        if "RGB" in rgb_formats:
            log("argb_swap: argb_to_rgb for %s on %s", pixel_format, type(pixels))
            image.set_pixels(argb_to_rgb(pixels))
            image.set_pixel_format("RGB")
            image.set_rowstride(rs*3//4)
            return True
    warning_key = "format-not-handled-%s" % bytestostr(pixel_format)
    if first_time(warning_key):
        log.warn("Warning: no matching argb function,")
        log.warn(" cannot convert %s to one of: %s", pixel_format, rgb_formats)
    return False
