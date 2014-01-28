# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2012-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

LOSSY_PIXEL_FORMATS = ("YUV420P", "YUV422P")

PIXEL_SUBSAMPLING = {
         "YUV420P"   : ((1, 1), (2, 2), (2, 2)),
         "YUV422P"   : ((1, 1), (2, 1), (2, 1)),
         "YUV444P"   : ((1, 1), (1, 1), (1, 1)),
         "GBRP"      : ((1, 1), (1, 1), (1, 1)),
}
def get_subsampling_divs(pixel_format):
    # Return size dividers for the given pixel format
    #  (Y_w, Y_h), (U_w, U_h), (V_w, V_h)
    if pixel_format not in PIXEL_SUBSAMPLING:
        raise Exception("invalid pixel format: %s" % pixel_format)
    return PIXEL_SUBSAMPLING.get(pixel_format)


AVUTIL_ENUM_TO_COLORSPACE =  {
            0   : "YUV420P",
            2   : "RGB",
            3   : "BGR",
            4   : "YUV422P",
            5   : "YUV444P"}
COLORSPACE_TO_AVUTIL_ENUM = {}
for e, s in AVUTIL_ENUM_TO_COLORSPACE.items():
    COLORSPACE_TO_AVUTIL_ENUM[s] = e

def get_colorspace_from_avutil_enum(pixfmt):
    return AVUTIL_ENUM_TO_COLORSPACE.get(pixfmt)

def get_avutil_enum_from_colorspace(pixfmt):
    return COLORSPACE_TO_AVUTIL_ENUM.get(pixfmt)


RGB_FORMATS = ("XRGB",
               "BGRX",
               "ARGB",
               "BGRA",
               "RGB")


class TransientCodecException(Exception):
    pass


class codec_spec(object):

    def __init__(self, codec_class, codec_type="", encoding=None,
                    quality=100, speed=100,
                    setup_cost=50, cpu_cost=100, gpu_cost=0,
                    min_w=1, min_h=1, max_w=4*1024, max_h=4*1024,
                    can_scale=False,
                    score_boost=0,
                    width_mask=0xFFFF, height_mask=0xFFFF):
        self.codec_class = codec_class          #ie: xpra.codecs.enc_x264.encoder.Encoder
        self.codec_type = codec_type            #ie: "nvenc"
        self.quality = quality
        self.speed = speed
        self.setup_cost = setup_cost
        self.cpu_cost = cpu_cost
        self.gpu_cost = gpu_cost
        self.score_boost = score_boost
        self.min_w = min_w
        self.min_h = min_h
        self.max_w = max_w
        self.max_h = max_h
        self.width_mask = width_mask
        self.height_mask = height_mask
        self.can_scale = can_scale
        self.encoding = encoding                #ie: "h264"

    def to_dict(self):
        d = {}
        for k in ("codec_class", "codec_type", "quality", "speed",
                  "setup_cost", "cpu_cost", "gpu_cost", "score_boost",
                  "min_w", "min_h", "max_w", "max_h",
                  "width_mask", "height_mask",
                  "can_scale", "encoding"):
            d[k] = getattr(self, k)
        return d

    def get_runtime_factor(self):
        #a cost multiplier that some encoder may want to override
        #1.0 means no change:
        return 1.0

    def __str__(self):
        return "codec_spec(%s)" % self.__dict__

    def __repr__(self):
        return "codec_spec(%s)" % self.info()

    def info(self):
        try:
            return "%s.%s" % ((self.codec_type.__module__ or self.codec_class), self.codec_class.__name__)
        except:
            return "%s" % (self.codec_type or self.codec_class)
