# This file is part of Xpra.
# Copyright (C) 2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.log import Logger
log = Logger("encoder", "pillow")

from xpra.os_util import BytesIOClass
from xpra.os_util import memoryview_to_bytes
from xpra.net import compression

import PIL                      #@UnresolvedImport
from PIL import Image           #@UnresolvedImport
PIL_VERSION = PIL.PILLOW_VERSION
PIL_can_optimize = PIL_VERSION and PIL_VERSION>="2.2"


def get_version():
    return PIL.PILLOW_VERSION

def get_type():
    return "pillow"

def do_get_encodings():
    log("PIL.Image.SAVE=%s", Image.SAVE)
    encodings = []
    for encoding in ["png", "png/L", "png/P", "jpeg", "webp"]:
        #strip suffix (so "png/L" -> "png")
        stripped = encoding.split("/")[0].upper()
        if stripped in Image.SAVE:
            encodings.append(encoding)
    log("do_get_encodings()=%s", encodings)
    return encodings

def get_encodings():
    return ENCODINGS

ENCODINGS = do_get_encodings()

def get_info():
    return  {
            "version"       : get_version(),
            "encodings"     : get_encodings(),
            "optimize"      : PIL_can_optimize,
            }


def encode(coding, image, quality, speed, supports_transparency):
    pixel_format = image.get_pixel_format()
    w = image.get_width()
    h = image.get_height()
    rgb = {
           "XRGB"   : "RGB",
           "BGRX"   : "RGB",
           "RGBA"   : "RGBA",
           "BGRA"   : "RGBA",
           }.get(pixel_format, pixel_format)
    bpp = 32
    #remove transparency if it cannot be handled:
    try:
        pixels = image.get_pixels()
        assert pixels, "failed to get pixels from %s" % image
        #PIL cannot use the memoryview directly:
        pixels = memoryview_to_bytes(pixels)
        #it is safe to use frombuffer() here since the convert()
        #calls below will not convert and modify the data in place
        #and we save the compressed data then discard the image
        im = PIL.Image.frombuffer(rgb, (w, h), pixels, "raw", pixel_format, image.get_rowstride())
        if coding.startswith("png") and not supports_transparency and rgb=="RGBA":
            im = im.convert("RGB")
            rgb = "RGB"
            bpp = 24
    except Exception:
        log.error("PIL_encode%s converting %s pixels from %s to %s failed", (w, h, coding, "%s bytes" % image.get_size(), pixel_format, image.get_rowstride()), type(pixels), pixel_format, rgb, exc_info=True)
        raise
    buf = BytesIOClass()
    client_options = {}
    #only optimize with Pillow>=2.2 and when speed is zero
    if coding in ("jpeg", "webp"):
        q = int(min(99, max(1, quality)))
        kwargs = im.info
        kwargs["quality"] = q
        client_options["quality"] = q
        if coding=="jpeg" and PIL_can_optimize and speed<70:
            #(optimizing jpeg is pretty cheap and worth doing)
            kwargs["optimize"] = True
            client_options["optimize"] = True
        im.save(buf, coding.upper(), **kwargs)
    else:
        assert coding in ("png", "png/P", "png/L"), "unsupported png encoding: %s" % coding
        if coding in ("png/L", "png/P") and supports_transparency and rgb=="RGBA":
            #grab alpha channel (the last one):
            #we use the last channel because we know it is RGBA,
            #otherwise we should do: alpha_index= image.getbands().index('A')
            alpha = im.split()[-1]
            #convert to simple on or off mask:
            #set all pixel values below 128 to 255, and the rest to 0
            def mask_value(a):
                if a<=128:
                    return 255
                return 0
            mask = PIL.Image.eval(alpha, mask_value)
        else:
            #no transparency
            mask = None
        if coding=="png/L":
            im = im.convert("L", palette=PIL.Image.ADAPTIVE, colors=255)
            bpp = 8
        elif coding=="png/P":
            #I wanted to use the "better" adaptive method,
            #but this does NOT work (produces a black image instead):
            #im.convert("P", palette=Image.ADAPTIVE)
            im = im.convert("P", palette=PIL.Image.WEB, colors=255)
            bpp = 8
        if mask:
            # paste the alpha mask to the color of index 255
            im.paste(255, mask)
        kwargs = im.info
        if mask is not None:
            client_options["transparency"] = 255
            kwargs["transparency"] = 255
        if PIL_can_optimize and speed==0:
            #optimizing png is very rarely worth doing
            kwargs["optimize"] = True
            client_options["optimize"] = True
        #level can range from 0 to 9, but anything above 5 is way too slow for small gains:
        #76-100   -> 1
        #51-76    -> 2
        #etc
        level = max(1, min(5, (125-speed)//25))
        kwargs["compress_level"] = level
        client_options["compress_level"] = level
        #default is good enough, no need to override, other options:
        #DEFAULT_STRATEGY, FILTERED, HUFFMAN_ONLY, RLE, FIXED
        #kwargs["compress_type"] = PIL.Image.DEFAULT_STRATEGY
        im.save(buf, "PNG", **kwargs)
    log("sending %sx%s %s as %s, mode=%s, options=%s", w, h, pixel_format, coding, im.mode, kwargs)
    data = buf.getvalue()
    buf.close()
    return coding, compression.Compressed(coding, data), client_options, image.get_width(), image.get_height(), 0, bpp

def selftest(full=False):
    global ENCODINGS
    import binascii
    from xpra.codecs.codec_checks import make_test_image
    img = make_test_image("BGRA", 32, 32)
    if full:
        vrange = (0, 50, 100)
    else:
        vrange = (50, )
    for encoding in list(ENCODINGS):
        try:
            for q in vrange:
                for s in vrange:
                    for alpha in (True, False):
                        v = encode(encoding, img, q, s, alpha)
                        assert v, "encode output was empty!"
                        cdata = v[1].data
                        log("encode(%s)=%s", (encoding, img, q, s, alpha), binascii.hexlify(cdata))
        except Exception as e:
            log.warn("Pillow error saving %s with quality=%s, speed=%s, alpha=%s", encoding, q, s, alpha)
            log.warn(" %s", e, exc_info=True)
            ENCODINGS.remove(encoding)
