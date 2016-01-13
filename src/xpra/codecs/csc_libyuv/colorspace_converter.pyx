# This file is part of Xpra.
# Copyright (C) 2013 Arthur Huillet
# Copyright (C) 2012-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import time

from xpra.log import Logger
from xpra.codecs.codec_checks import do_testcsc
from xpra.codecs.codec_constants import get_subsampling_divs
log = Logger("csc", "libyuv")

from xpra.os_util import is_Ubuntu
from xpra.codecs.codec_constants import csc_spec
from xpra.codecs.image_wrapper import ImageWrapper


cdef extern from "stdlib.h":
    int posix_memalign(void **memptr, size_t alignment, size_t size)
    void free(void *ptr)

DEF MEMALIGN_ALIGNMENT = 16
cdef void *xmemalign(size_t size):
    cdef void *memptr = NULL
    if posix_memalign(&memptr, MEMALIGN_ALIGNMENT, size):
        return NULL
    return memptr

#inlined here because linking messes up with c++..
cdef extern from "Python.h":
    int PyObject_AsReadBuffer(object obj, const void **buffer, Py_ssize_t *buffer_len)
    int PyMemoryView_Check(object obj)
    object PyMemoryView_FromBuffer(Py_buffer *view)
    Py_buffer *PyMemoryView_GET_BUFFER(object mview)
    int PyBuffer_FillInfo(Py_buffer *view, object obj, void *buf, Py_ssize_t len, int readonly, int infoflags)
    int PyBUF_SIMPLE

cdef int object_as_buffer(object obj, const void ** buffer, Py_ssize_t * buffer_len):
    cdef Py_buffer *rpybuf
    if PyMemoryView_Check(obj):
        rpybuf = PyMemoryView_GET_BUFFER(obj)
        if rpybuf.buf==NULL:
            return -1
        buffer[0] = rpybuf.buf
        buffer_len[0] = rpybuf.len
        return 0
    return PyObject_AsReadBuffer(obj, buffer, buffer_len)

cdef object memory_as_readonly_pybuffer(void *ptr, Py_ssize_t buf_len):
    cdef Py_buffer pybuf
    cdef Py_ssize_t shape[1]
    shape[0] = buf_len
    cdef int ret = PyBuffer_FillInfo(&pybuf, None, ptr, buf_len, 0, PyBUF_SIMPLE)
    if ret!=0:
        return None
    pybuf.format = "B"
    pybuf.shape = shape
    return PyMemoryView_FromBuffer(&pybuf)


from libc.stdint cimport uint8_t

cdef extern from "libyuv/convert.h" namespace "libyuv":
    #int BGRAToI420(const uint8_t* src_frame, ...
    #this is actually BGRX for little endian systems:
    int ARGBToI420(const uint8_t* src_frame, int src_stride_frame,
               uint8_t* dst_y, int dst_stride_y,
               uint8_t* dst_u, int dst_stride_u,
               uint8_t* dst_v, int dst_stride_v,
               int width, int height) nogil

cdef extern from "libyuv/scale.h" namespace "libyuv":
    ctypedef int FilterMode
    FilterMode  kFilterNone
    FilterMode  kFilterBilinear
    FilterMode  kFilterBox
    void ScalePlane(const uint8_t* src, int src_stride,
                int src_width, int src_height,
                uint8_t* dst, int dst_stride,
                int dst_width, int dst_height,
                FilterMode filtering) nogil

FILTERMODE_STR = {
        kFilterNone     : "None",
        kFilterBilinear : "Bilinear",
        kFilterBox      : "Box"}

cdef FilterMode get_filtermode(int speed):
    if speed>66:
        return kFilterNone
    elif speed>33:
        return kFilterBilinear
    return kFilterBox


cdef inline int roundup(int n, int m):
    return (n + m - 1) & ~(m - 1)


def init_module():
    #nothing to do!
    log("csc_libyuv.init_module()")

def cleanup_module():
    log("csc_libyuv.cleanup_module()")

def get_type():
    return "libyuv"

def get_version():
    return 0

#hardcoded for now:
MAX_WIDTH = 32768
MAX_HEIGHT = 32768
IN_COLORSPACES = ["BGRX"]
OUT_COLORSPACES = ["YUV420P"]
def get_info():
    global IN_COLORSPACES, OUT_COLORSPACES, MAX_WIDTH, MAX_HEIGHT
    return {"version"           : get_version(),
            "input-formats"     : IN_COLORSPACES,
            "output-formats"    : OUT_COLORSPACES,
            "max-size"          : (MAX_WIDTH, MAX_HEIGHT),
            }

def get_input_colorspaces():
    return IN_COLORSPACES

def get_output_colorspaces(input_colorspace):
    assert input_colorspace in IN_COLORSPACES
    return OUT_COLORSPACES


def get_spec(in_colorspace, out_colorspace):
    assert in_colorspace in IN_COLORSPACES, "invalid input colorspace: %s (must be one of %s)" % (in_colorspace, IN_COLORSPACES)
    assert out_colorspace in OUT_COLORSPACES, "invalid output colorspace: %s (must be one of %s)" % (out_colorspace, OUT_COLORSPACES)
    return csc_spec(ColorspaceConverter, codec_type=get_type(), setup_cost=0, min_w=8, min_h=2, can_scale=True, max_w=MAX_WIDTH, max_h=MAX_HEIGHT)


class YUVImageWrapper(ImageWrapper):

    def free(self):                             #@DuplicatedSignature
        log("YUVImageWrapper.free() cython_buffer=%#x", <unsigned long> self.cython_buffer)
        ImageWrapper.free(self)
        if self.cython_buffer>0:
            free(<void *> (<unsigned long> self.cython_buffer))
            self.cython_buffer = 0


cdef class ColorspaceConverter:
    cdef int src_width
    cdef int src_height
    cdef int dst_width
    cdef int dst_height
    cdef int scaling

    cdef unsigned long frames
    cdef double time

    cdef object src_format
    cdef object dst_format
    cdef int out_stride[3]
    cdef int out_width[3]
    cdef int out_height[3]
    cdef unsigned long[3] out_offsets
    cdef unsigned long out_size[3]
    cdef unsigned long out_buffer_size
    #when scaling:
    cdef uint8_t *output_buffer
    cdef FilterMode filtermode
    cdef int scaled_stride[3]
    cdef int scaled_width[3]
    cdef int scaled_height[3]
    cdef unsigned long[3] scaled_offsets
    cdef unsigned long scaled_size[3]
    cdef unsigned long scaled_buffer_size

    cdef object __weakref__

    def init_context(self, int src_width, int src_height, src_format,
                           int dst_width, int dst_height, dst_format, int speed=100):    #@DuplicatedSignature
        log("libyuv.ColorspaceConverter.init_context%s", (src_width, src_height, src_format, dst_width, dst_height, dst_format, speed))
        assert src_format=="BGRX", "invalid source format: %s" % src_format
        assert dst_format=="YUV420P", "invalid destination format: %s" % dst_format
        self.scaling = int(src_width!=dst_width or src_height!=dst_height)
        if self.scaling:
            self.filtermode = get_filtermode(speed)
        self.src_format = "BGRX"
        self.dst_format = "YUV420P"
        self.src_width = src_width
        self.src_height = src_height
        self.dst_width = dst_width
        self.dst_height = dst_height
        #pre-calculate unscaled YUV plane heights:
        self.out_buffer_size = 0
        self.scaled_buffer_size = 0
        divs = get_subsampling_divs(self.dst_format)
        for i in range(3):
            xdiv, ydiv = divs[i]
            self.out_width[i]   = src_width // xdiv
            self.out_height[i]  = src_height // ydiv
            self.out_stride[i]  = roundup(self.out_width[i], 16)
            self.out_size[i]    = self.out_stride[i] * self.out_height[i]
            self.out_offsets[i] = self.out_buffer_size
            #add one extra line to height so we can access a full rowstride at a time,
            #no matter where we start to read on the last line
            self.out_buffer_size += self.out_size[i] + self.out_stride[i]
            if self.scaling:
                self.scaled_width[i]    = dst_width // xdiv
                self.scaled_height[i]   = dst_height // ydiv
                self.scaled_stride[i]   = roundup(self.scaled_width[i], 16)
                self.scaled_size[i]     = self.scaled_stride[i] * self.scaled_height[i]
                self.scaled_offsets[i]  = self.scaled_buffer_size
                self.scaled_buffer_size += self.scaled_size[i] + self.out_stride[i]
        if self.scaling:
            self.output_buffer = <uint8_t *> xmemalign(self.out_buffer_size)
        else:
            self.output_buffer = NULL
        log("buffer size=%i, scaling=%s, filtermode=%s", self.out_buffer_size, self.scaling, FILTERMODE_STR.get(self.filtermode))
        self.time = 0
        self.frames = 0

    def get_info(self):         #@DuplicatedSignature
        info = get_info()
        info.update({
                "frames"    : self.frames,
                "src_width" : self.src_width,
                "src_height": self.src_height,
                "dst_width" : self.dst_width,
                "dst_height": self.dst_height})
        if self.src_format:
            info["src_format"] = self.src_format
        if self.dst_format:
            info["dst_format"] = self.dst_format
        if self.frames>0 and self.time>0:
            pps = float(self.src_width) * float(self.src_height) * float(self.frames) / self.time
            info["total_time_ms"] = int(self.time*1000.0)
            info["pixels_per_second"] = int(pps)
        return info

    def __repr__(self):
        if not self.src_format or not self.dst_format:
            return "libyuv(uninitialized)"
        return "libyuv(%s %sx%s %s)" % (self.src_format, self.width, self.height, self.dst_format)

    def __dealloc__(self):                  #@DuplicatedSignature
        self.clean()

    def get_src_width(self):
        return self.src_width

    def get_src_height(self):
        return self.src_height

    def get_src_format(self):
        return self.src_format

    def get_dst_width(self):
        return self.dst_width

    def get_dst_height(self):
        return self.dst_height

    def get_dst_format(self):
        return self.dst_format

    def get_type(self):                     #@DuplicatedSignature
        return  "libyuv"


    def clean(self):                        #@DuplicatedSignature
        self.src_width = 0
        self.src_height = 0
        self.dst_width = 0
        self.dst_height = 0
        self.src_format = ""
        self.dst_format = ""
        self.frames = 0
        self.time = 0
        self.scaling = 0
        for i in range(3):
            self.out_stride[i] = 0
            self.out_size[i] = 0
        cdef uint8_t *output_buffer = self.output_buffer
        self.output_buffer = NULL
        if output_buffer:
            free(output_buffer)
        self.out_buffer_size = 0

    def is_closed(self):
        return self.out_buffer_size==0


    def convert_image(self, image):
        cdef Py_ssize_t pic_buf_len = 0
        cdef const uint8_t *input_image
        cdef uint8_t *output_buffer
        cdef uint8_t *out_planes[3]
        cdef uint8_t *scaled_buffer
        cdef uint8_t *scaled_planes[3]
        cdef int iplanes
        cdef int i, result
        cdef int width, height, stride
        cdef object planes, strides, out_image
        start = time.time()
        iplanes = image.get_planes()
        pixels = image.get_pixels()
        stride = image.get_rowstride()
        width = image.get_width()
        height = image.get_height()
        assert iplanes==ImageWrapper.PACKED, "invalid plane input format: %s" % iplanes
        assert pixels, "failed to get pixels from %s" % image
        assert width>=self.src_width, "invalid image width: %s (minimum is %s)" % (width, self.src_width)
        assert height>=self.src_height, "invalid image height: %s (minimum is %s)" % (height, self.src_height)
        #get pointer to input:
        assert object_as_buffer(pixels, <const void**> &input_image, &pic_buf_len)==0
        if self.scaling:
            #re-use the same temporary buffer every time:
            output_buffer = self.output_buffer
        else:
            #allocate output buffer:
            output_buffer = <unsigned char*> xmemalign(self.out_buffer_size)
        for i in range(3):
            out_planes[i] = output_buffer + self.out_offsets[i]
        with nogil:
            result = ARGBToI420(input_image, stride,
                           out_planes[0], self.out_stride[0],
                           out_planes[1], self.out_stride[1],
                           out_planes[2], self.out_stride[2],
                           width, height)
        assert result==0, "libyuv BGRAToI420 failed and returned %i" % result
        elapsed = time.time()-start
        log("libyuv.ARGBToI420 took %.1fms", 1000.0*elapsed)
        self.time += elapsed
        planes = []
        strides = []
        if self.scaling:
            start = time.time()
            scaled_buffer = <unsigned char*> xmemalign(self.scaled_buffer_size)
            with nogil:
                for i in range(3):
                    scaled_planes[i] = scaled_buffer + self.scaled_offsets[i]
                    ScalePlane(out_planes[i], self.out_stride[i],
                               self.out_width[i], self.out_height[i],
                               scaled_planes[i], self.scaled_stride[i],
                               self.scaled_width[i], self.scaled_height[i],
                               self.filtermode)
            elapsed = time.time()-start
            log("libyuv.ScalePlane took %.1fms", 1000.0*elapsed)
            for i in range(3):
                strides.append(self.scaled_stride[i])
                planes.append(memory_as_readonly_pybuffer(<void *> (<unsigned long> (scaled_planes[i])), self.scaled_size[i]))
            self.frames += 1
            out_image = YUVImageWrapper(0, 0, self.src_width, self.src_height, planes, self.dst_format, 24, strides, ImageWrapper._3_PLANES)
            out_image.cython_buffer = <unsigned long> scaled_buffer
        else:
            #use output buffer directly:
            for i in range(3):
                strides.append(self.out_stride[i])
                planes.append(memory_as_readonly_pybuffer(<void *> (<unsigned long> (out_planes[i])), self.out_size[i]))
            self.frames += 1
            out_image = YUVImageWrapper(0, 0, self.src_width, self.src_height, planes, self.dst_format, 24, strides, ImageWrapper._3_PLANES)
            out_image.cython_buffer = <unsigned long> output_buffer
        return out_image


def selftest(full=False):
    global MAX_WIDTH, MAX_HEIGHT
    from xpra.codecs.codec_checks import testcsc, get_csc_max_size
    from xpra.codecs.csc_libyuv import colorspace_converter
    maxw, maxh = MAX_WIDTH, MAX_HEIGHT
    in_csc = get_input_colorspaces()
    out_csc = get_output_colorspaces(in_csc[0])
    testcsc(colorspace_converter, full, in_csc, out_csc)
    if full:
        mw, mh = get_csc_max_size(colorspace_converter, in_csc, out_csc, limit_w=32768, limit_h=32768)
        MAX_WIDTH = min(maxw, mw)
        MAX_HEIGHT = min(maxh, mh)
