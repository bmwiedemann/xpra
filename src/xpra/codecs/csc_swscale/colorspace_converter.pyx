# This file is part of Xpra.
# Copyright (C) 2013 Arthur Huillet
# Copyright (C) 2012-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import time

from xpra.log import Logger
from xpra.codecs.codec_checks import do_testcsc
from xpra.codecs.csc_cython import colorspace_converter
from xpra.codecs.csc_cython.colorspace_converter import get_input_colorspaces, get_output_colorspaces
log = Logger("csc", "swscale")

from xpra.os_util import is_Ubuntu
from xpra.codecs.codec_constants import csc_spec
from xpra.codecs.image_wrapper import ImageWrapper
from xpra.codecs.libav_common.av_log cimport override_logger, restore_logger #@UnresolvedImport


cdef extern from "../../buffers/buffers.h":
    object memory_as_pybuffer(void* ptr, Py_ssize_t buf_len, int readonly)
    int    object_as_buffer(object obj, const void ** buffer, Py_ssize_t * buffer_len)
    int get_buffer_api_version()

cdef extern from "../../buffers/memalign.h":
    int pad(int size) nogil
    void *xmemalign(size_t size) nogil

cdef extern from "stdlib.h":
    void free(void *ptr)

cdef extern from "../../inline.h":
    pass

ctypedef unsigned char uint8_t
ctypedef long AVPixelFormat
cdef extern from "libavcodec/version.h":
    int LIBSWSCALE_VERSION_MAJOR
    int LIBSWSCALE_VERSION_MINOR
    int LIBSWSCALE_VERSION_MICRO

cdef extern from "libavutil/pixfmt.h":
    AVPixelFormat AV_PIX_FMT_YUV420P
    AVPixelFormat AV_PIX_FMT_YUV422P
    AVPixelFormat AV_PIX_FMT_YUV444P
    AVPixelFormat AV_PIX_FMT_RGB24
    AVPixelFormat AV_PIX_FMT_0RGB
    AVPixelFormat AV_PIX_FMT_BGR0
    AVPixelFormat AV_PIX_FMT_ARGB
    AVPixelFormat AV_PIX_FMT_BGRA
    AVPixelFormat AV_PIX_FMT_GBRP
    AVPixelFormat AV_PIX_FMT_RGB24
    AVPixelFormat AV_PIX_FMT_BGR24
    AVPixelFormat AV_PIX_FMT_NONE
    unsigned int SWS_ACCURATE_RND
    unsigned int SWS_BICUBIC
    unsigned int SWS_BICUBLIN
    unsigned int SWS_BILINEAR
    unsigned int SWS_FAST_BILINEAR
    unsigned int SWS_FULL_CHR_H_INT

ctypedef void SwsContext
cdef extern from "libswscale/swscale.h":
    ctypedef struct SwsFilter:
        pass
    unsigned int SWS_ACCURATE_RND
    unsigned int SWS_BICUBIC
    unsigned int SWS_BICUBLIN
    unsigned int SWS_BILINEAR
    unsigned int SWS_FAST_BILINEAR
    unsigned int SWS_FULL_CHR_H_INT

    SwsContext *sws_getContext(int srcW, int srcH, AVPixelFormat srcFormat,
                                int dstW, int dstH, AVPixelFormat dstFormat,
                                int flags, SwsFilter *srcFilter,
                                SwsFilter *dstFilter, const double *param)
    void sws_freeContext(SwsContext *context)

    int sws_scale(SwsContext *c, const uint8_t *const srcSlice[],
                  const int srcStride[], int srcSliceY, int srcSliceH,
                  uint8_t *const dst[], const int dstStride[]) nogil


cdef class CSCPixelFormat:
    cdef AVPixelFormat av_enum
    cdef char* av_enum_name
    cdef float width_mult[4]
    cdef float height_mult[4]
    cdef char *pix_fmt
    def __init__(self, AVPixelFormat av_enum, char *av_enum_name, width_mult, height_mult, char *pix_fmt):
        self.av_enum = av_enum
        self.av_enum_name = av_enum_name
        for i in range(4):
            self.width_mult[i] = 0.0
            self.height_mult[i] = 0.0
        for i in range(4):
            self.width_mult[i] = width_mult[i]
            self.height_mult[i] = height_mult[i]
        self.pix_fmt = pix_fmt

    def __repr__(self):
        return "CSCPixelFormat(%s)" % av_enum_name

#we could use a class to represent these options:
COLORSPACES = []
#keeping this array in scope ensures the strings don't go away!
FORMAT_OPTIONS = [
    ("RGB24",   AV_PIX_FMT_RGB24,      (3, 0, 0, 0),       (1, 0, 0, 0),       "RGB"  ),
    ("BGR24",   AV_PIX_FMT_BGR24,      (3, 0, 0, 0),       (1, 0, 0, 0),       "BGR"  ),
    ("0RGB",    AV_PIX_FMT_0RGB,       (4, 0, 0, 0),       (1, 0, 0, 0),       "XRGB"  ),
    ("BGR0",    AV_PIX_FMT_BGR0,       (4, 0, 0, 0),       (1, 0, 0, 0),       "BGRX"  ),
    ("ARGB",    AV_PIX_FMT_ARGB,       (4, 0, 0, 0),       (1, 0, 0, 0),       "XRGB"  ),
    ("BGRA",    AV_PIX_FMT_BGRA,       (4, 0, 0, 0),       (1, 0, 0, 0),       "BGRX"  ),
    ("YUV420P", AV_PIX_FMT_YUV420P,    (1, 0.5, 0.5, 0),   (1, 0.5, 0.5, 0),   "YUV420P"),
    ("YUV422P", AV_PIX_FMT_YUV422P,    (1, 0.5, 0.5, 0),   (1, 1, 1, 0),       "YUV422P"),
    ("YUV444P", AV_PIX_FMT_YUV444P,    (1, 1, 1, 0),       (1, 1, 1, 0),       "YUV444P"),
    ("GBRP",    AV_PIX_FMT_GBRP,       (1, 1, 1, 0),       (1, 1, 1, 0),       "GBRP"   )
     ]
FORMATS = {}
for av_enum_name, av_enum, width_mult, height_mult, pix_fmt in FORMAT_OPTIONS:
    log("av_enum(%s)=%s", av_enum_name, av_enum)
    FORMATS[pix_fmt] = CSCPixelFormat(av_enum, av_enum_name.encode("latin1"), width_mult, height_mult, pix_fmt.encode("latin1"))
    if pix_fmt not in COLORSPACES:
        COLORSPACES.append(pix_fmt)
log("swscale pixel formats: %s", FORMATS)
log("colorspaces: %s", COLORSPACES)
if LIBSWSCALE_VERSION_MAJOR>3 or LIBSWSCALE_VERSION_MAJOR==3 and LIBSWSCALE_VERSION_MINOR>=1:
    YUV422P_SKIPLIST = []
else:
    #avoid unaccelerated conversion, which also triggers a warning on Ubuntu / Debian:
    YUV422P_SKIPLIST = ["RGB", "BGR", "BGRX"]
    log.warn("Warning: swscale version %s is too old:", ".".join((str(x) for x in (LIBSWSCALE_VERSION_MAJOR, LIBSWSCALE_VERSION_MINOR, LIBSWSCALE_VERSION_MICRO))))
    log.warn(" disabling YUV422P to %s", ", ".join(YUV422P_SKIPLIST))


cdef inline int roundup(int n, int m):
    return (n + m - 1) & ~(m - 1)


cdef class SWSFlags:
    cdef int flags
    cdef object flags_strs
    def __init__(self, int flags, flags_strs):          #@DuplicatedSignature
        self.flags = flags
        self.flags_strs = flags_strs

    def get_flags(self):
        return self.flags

    def __repr__(self):
        try:
            return "|".join(self.flags_strs)
        except:
            return str(self.flags_strs)


#keeping this array in scope ensures the strings don't go away!
FLAGS_OPTIONS = (
            (30, (SWS_BICUBIC, ),       ("BICUBIC", )),
            (40, (SWS_BICUBLIN, ),      ("BICUBLIN", )),
            (60, (SWS_BILINEAR, ),      ("BILINEAR", )),
            (80, (SWS_FAST_BILINEAR, ), ("FAST_BILINEAR", )),
        )
FLAGS = []
for speed, flags, flag_strs in FLAGS_OPTIONS:
    flag_value = 0
    for flag in flags:
        flag_value |= flag
    swsf = SWSFlags(flag_value, flag_strs)
    FLAGS.append((speed, swsf))
    log("speed=%s %s=%s", speed, swsf, flag_value)
log("swscale flags: %s", FLAGS)


cdef int get_swscale_flags(int speed, int scaling, int subsampling, dst_format):
    if not scaling and not subsampling:
        speed = 100
    cdef int flags = 0
    for s, swsflags in FLAGS:
        if s>=speed:
            flags = swsflags.get_flags()
            break
    #not found? use the highest one:
    if flags==0:
        _, swsflags = FLAGS[-1]
        flags = swsflags.get_flags()
    #look away now: we get an acceleration warning with XRGB
    #when we don't add SWS_ACCURATE_RND...
    #but we don't want the flag otherwise, unless we are scaling or downsampling:
    if ((scaling or subsampling) and speed<100) or dst_format=="XRGB":
        flags |= SWS_ACCURATE_RND
    if dst_format=="GBRP":
        flags |= SWS_FULL_CHR_H_INT
    return flags


def get_swscale_flags_strs(int flags):
    strs = []
    for flag_value, flag_name in {
                SWS_BICUBIC         : "BICUBIC",
                SWS_BICUBLIN        : "BICUBLIN",
                SWS_FAST_BILINEAR   : "FAST_BILINEAR",
                SWS_ACCURATE_RND    : "ACCURATE_RND"}.items():
        if (flag_value & flags)>0:
            strs.append(flag)
    return strs


def init_module():
    #nothing to do!
    log("csc_swscale.init_module()")
    override_logger()

def cleanup_module():
    log("csc_swscale.cleanup_module()")
    restore_logger()

def get_type():
    return "swscale"

def get_version():
    return (LIBSWSCALE_VERSION_MAJOR, LIBSWSCALE_VERSION_MINOR, LIBSWSCALE_VERSION_MICRO)

def get_info():
    global COLORSPACES, MAX_WIDTH, MAX_HEIGHT
    return {"version"   : get_version(),
            "buffer_api": get_buffer_api_version(),
            "formats"   : COLORSPACES,
            "max-size"  : (MAX_WIDTH, MAX_HEIGHT),
            }

def get_input_colorspaces():
    return COLORSPACES

def get_output_colorspaces(input_colorspace):
    #exclude input colorspace:
    exclude = [input_colorspace]
    if input_colorspace in ("YUV420P", "YUV422P"):
        #these would cause a warning:
        #"No accelerated colorspace conversion found from yuv420p to gbrp."
        exclude.append("GBRP")
    if input_colorspace=="YUV422P":
        exclude += YUV422P_SKIPLIST
    return [x for x in COLORSPACES if x not in exclude]


#a safe guess, which we probe later on:
MAX_WIDTH = 16384
MAX_HEIGHT = 16384
def get_spec(in_colorspace, out_colorspace):
    assert in_colorspace in COLORSPACES, "invalid input colorspace: %s (must be one of %s)" % (in_colorspace, COLORSPACES)
    assert out_colorspace in COLORSPACES, "invalid output colorspace: %s (must be one of %s)" % (out_colorspace, COLORSPACES)
    #setup cost is very low (usually less than 1ms!)
    #there are restrictions on dimensions (8x2 minimum!)
    #swscale can be used to scale (obviously)
    return csc_spec(ColorspaceConverter, codec_type=get_type(), setup_cost=20, min_w=8, min_h=2, can_scale=True, max_w=MAX_WIDTH, max_h=MAX_HEIGHT)


MIN_SWSCALE_VERSION = (2, 1, 1)
if (LIBSWSCALE_VERSION_MAJOR, LIBSWSCALE_VERSION_MINOR, LIBSWSCALE_VERSION_MICRO)<MIN_SWSCALE_VERSION and is_Ubuntu():
    log.warn("buggy Ubuntu swscale version detected: %s", get_version())
    if os.environ.get("XPRA_FORCE_SWSCALE", "0")=="1":
        log.warn("XPRA_FORCE_SWSCALE enabled at your own risk!")
    else:
        log.warn("cowardly refusing to use it to avoid problems, set the environment variable:")
        log.warn("XPRA_FORCE_SWSCALE=1")
        log.warn("to use it anyway, at your own risk")
        COLORSPACES = []


cdef class CSCImage:
    """
        Allows us to call free_csc_image
        when this object is garbage collected
    """
    cdef uint8_t *buf[4]
    cdef int freed

    cdef set_plane(self, int plane, uint8_t *buf):
        assert plane in (0, 1, 2, 3)
        self.buf[plane] = buf

    def __repr__(self):
        return "CSCImage(%#x, freed=%s)" % (<unsigned long> self.buf, self.freed)

    def __dealloc__(self):
        #log("CSCImage.__dealloc__()")
        self.free()

    def free(self):
        #log("CSCImage.free() freed=%s", bool(self.freed))
        if self.freed==0:
            self.freed = 1
            if self.buf[0]==NULL:
                raise Exception("buffer is already freed!?")
            free(self.buf[0])
            for i in range(4):
                self.buf[i] = NULL


class CSCImageWrapper(ImageWrapper):

    def free(self):                             #@DuplicatedSignature
        log("CSCImageWrapper.free() csc_image=%s", self.csc_image)
        ImageWrapper.free(self)
        if self.csc_image:
            self.csc_image.free()
            self.csc_image = None


cdef class ColorspaceConverter:
    cdef int src_width
    cdef int src_height
    cdef AVPixelFormat src_format_enum
    cdef object src_format
    cdef int dst_width
    cdef int dst_height
    cdef AVPixelFormat dst_format_enum
    cdef object dst_format

    cdef unsigned long frames
    cdef double time
    cdef SwsContext *context
    cdef int flags                              #@DuplicatedSignature

    cdef int out_height[4]
    cdef int out_stride[4]
    cdef unsigned long out_size[4]
    cdef unsigned long buffer_size

    cdef object __weakref__

    def init_context(self, int src_width, int src_height, src_format,
                           int dst_width, int dst_height, dst_format, int speed=100):    #@DuplicatedSignature
        log("swscale.ColorspaceConverter.init_context%s", (src_width, src_height, src_format, dst_width, dst_height, dst_format, speed))
        cdef CSCPixelFormat src
        cdef CSCPixelFormat dst
        #src:
        src = FORMATS.get(src_format)
        log("source format=%s", src)
        assert src, "invalid source format: %s" % src_format
        self.src_format = src_format
        self.src_format_enum = src.av_enum
        #dst:
        dst = FORMATS.get(dst_format)
        log("destination format=%s", dst)
        assert dst, "invalid destination format: %s" % dst_format
        self.dst_format = dst_format
        self.dst_format_enum = dst.av_enum
        #pre-calculate plane heights:
        self.buffer_size = 0
        cdef int subsampling = False
        for i in range(4):
            self.out_height[i] = (int) (dst_height * dst.height_mult[i])
            self.out_stride[i] = roundup((int) (dst_width * dst.width_mult[i]), 16)
            if i!=3 and (dst.height_mult[i]!=1.0 or dst.width_mult[i]!=1.0):
                subsampling = True
            #add one extra line to height so we can read a full rowstride
            #no matter where we start to read on the last line.
            #MEMALIGN may be redundant here but it is very cheap
            self.out_size[i] = pad(self.out_stride[i] * (self.out_height[i]+1))
            self.buffer_size += self.out_size[i]
        log("buffer size=%s", self.buffer_size)

        self.src_width = src_width
        self.src_height = src_height
        self.dst_width = dst_width
        self.dst_height = dst_height

        cdef int scaling = (src_width!=dst_width) or (src_height!=dst_height)
        self.flags = get_swscale_flags(speed, scaling, subsampling, dst_format)
        #log("sws get_swscale_flags(%s, %s, %s)=%s", speed, scaling, subsampling, get_swscale_flags_strs(self.flags))
        self.time = 0
        self.frames = 0

        self.context = sws_getContext(self.src_width, self.src_height, self.src_format_enum,
                                      self.dst_width, self.dst_height, self.dst_format_enum,
                                      self.flags, NULL, NULL, NULL)
        log("sws context=%#x", <unsigned long> self.context)
        assert self.context!=NULL, "sws_getContext returned NULL"

    def get_info(self):         #@DuplicatedSignature
        info = get_info()
        info.update({
                "flags"     : get_swscale_flags_strs(self.flags),
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
            return "swscale(uninitialized)"
        return "swscale(%s %sx%s - %s %sx%s)" % (self.src_format, self.src_width, self.src_height,
                                                 self.dst_format, self.dst_width, self.dst_height)

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
        return  "swscale"


    def clean(self):                        #@DuplicatedSignature
        #overzealous clean is cheap!
        cdef int i
        if self.context!=NULL:
            log("swscale.ColorspaceConverter.clean() sws context=%#x", <unsigned long> self.context)
            sws_freeContext(self.context)
            self.context = NULL
        self.src_width = 0
        self.src_height = 0
        self.src_format_enum = AV_PIX_FMT_NONE
        self.src_format = ""
        self.dst_width = 0
        self.dst_height = 0
        self.dst_format_enum = AV_PIX_FMT_NONE
        self.dst_format = ""
        self.frames = 0
        self.time = 0
        self.flags = 0
        for i in range(4):
            self.out_height[i] = 0
            self.out_stride[i] = 0
            self.out_size[i] = 0
        self.buffer_size = 0

    def is_closed(self):
        return self.context!=NULL


    def convert_image(self, image):
        cdef Py_ssize_t pic_buf_len = 0
        assert self.context!=NULL
        cdef const uint8_t *input_image[4]
        cdef uint8_t *output_image[4]
        cdef int input_stride[4]
        cdef int iplanes,oplanes
        cdef int i                          #@DuplicatedSignature
        cdef int height
        cdef int stride
        cdef int result
        cdef Py_buffer *py_buffer
        start = time.time()
        iplanes = image.get_planes()
        pixels = image.get_pixels()
        strides = image.get_rowstride()
        assert iplanes in ImageWrapper.PLANE_OPTIONS, "invalid number of planes: %s" % iplanes
        if iplanes==ImageWrapper.PACKED:
            #magic: repack raw pixels/rowstride:
            planes = [pixels]
            strides = [strides]
            iplanes = 1
        else:
            planes = pixels
        #print("convert_image(%s) input=%s, strides=%s" % (image, len(input), strides))
        assert pixels, "failed to get pixels from %s" % image
        assert image.get_width()>=self.src_width, "invalid image width: %s (minimum is %s)" % (image.get_width(), self.src_width)
        assert image.get_height()>=self.src_height, "invalid image height: %s (minimum is %s)" % (image.get_height(), self.src_height)
        assert len(planes)==iplanes, "expected %s planes but found %s" % (iplanes, len(pixels))
        assert len(strides)==iplanes, "expected %s rowstrides but found %s" % (iplanes, len(strides))
        for i in range(4):
            if i<iplanes:
                input_stride[i] = strides[i]
                assert object_as_buffer(planes[i], <const void**> &input_image[i], &pic_buf_len)==0
            else:
                #some versions of swscale check all 4 planes
                #even when we only pass 1! see "check_image_pointers"
                #(so we just copy the last valid plane in the remaining slots - ugly!)
                input_stride[i] = input_stride[iplanes-1]
                input_image[i] = input_image[iplanes-1]
        with nogil:
            output_image[0] = <uint8_t*> xmemalign(self.buffer_size)
            for i in range(3):
                output_image[1+i] = output_image[i] + self.out_size[i]
            result = sws_scale(self.context, input_image, input_stride, 0, self.src_height, output_image, self.out_stride)
        assert result!=0, "sws_scale failed!"
        assert result==self.dst_height, "invalid output height: %s, expected %s" % (result, self.dst_height)
        #now parse the output:
        csci = CSCImage()           #keep a reference to memory for cleanup
        for i in range(4):
            csci.set_plane(i, NULL)
        if self.dst_format.endswith("P"):
            #planar mode, assume 3 planes:
            oplanes = ImageWrapper._3_PLANES
            out = []
            strides = []
            for i in range(3):
                if self.out_stride[i]>0 and output_image[i]!=NULL:
                    stride = self.out_stride[i]
                    plane = memory_as_pybuffer(<void *>output_image[i], self.out_height[i] * self.out_stride[i], True)
                else:
                    stride = 0
                    plane = None
                csci.set_plane(i, output_image[i])
                out.append(plane)
                strides.append(stride)
        else:
            #assume no planes, plain RGB packed pixels:
            oplanes = ImageWrapper.PACKED
            strides = self.out_stride[0]
            out = memory_as_pybuffer(<void *>output_image[0], self.out_height[0] * self.out_stride[0], True)
            csci.set_plane(0, output_image[0])
        elapsed = time.time()-start
        log("%s took %.1fms", self, 1000.0*elapsed)
        self.time += elapsed
        self.frames += 1
        out_image = CSCImageWrapper(0, 0, self.dst_width, self.dst_height, out, self.dst_format, 24, strides, oplanes)
        out_image.csc_image = csci
        return out_image


def selftest(full=False):
    global MAX_WIDTH, MAX_HEIGHT
    from xpra.codecs.codec_checks import testcsc, get_csc_max_size
    from xpra.codecs.csc_swscale import colorspace_converter
    override_logger()
    #test a limited set, not all combinations:
    if full:
        planar_tests = [x for x in get_input_colorspaces() if x.endswith("P")]
        packed_tests = [x for x in get_input_colorspaces() if ((x.find("BGR")>=0 or x.find("RGB")>=0) and not x not in planar_tests)]
    else:
        planar_tests = [x for x in ("YUV420P", "YUV422P", "YUV444P", "GBRP") if x in get_input_colorspaces()]
        packed_tests = ["BGRX"]   #only test BGRX
    maxw, maxh = 2**24, 2**24
    for planar in planar_tests:
        for packed in packed_tests:
            #test planar to packed:
            if packed not in get_output_colorspaces(planar):
                continue
            testcsc(colorspace_converter, full, [planar], [packed])
            if full:
                mw, mh = get_csc_max_size(colorspace_converter, [planar], [packed])
                maxw = min(maxw, mw)
                maxh = min(maxh, mh)
            #test BGRX to planar:
            if packed not in get_input_colorspaces():
                continue
            if planar not in get_output_colorspaces(packed):
                continue
            testcsc(colorspace_converter, full, [packed], [planar])
            if full:
                mw, mh = get_csc_max_size(colorspace_converter, [packed], [planar])
                maxw = min(maxw, mw)
                maxh = min(maxh, mh)
    if full and maxw<65536 and maxh<65536:
        MAX_WIDTH = maxw
        MAX_HEIGHT = maxh
        log("%s max dimensions: %ix%i", colorspace_converter, MAX_WIDTH, MAX_HEIGHT)
