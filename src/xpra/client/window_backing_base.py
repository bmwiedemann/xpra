# This file is part of Xpra.
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2012-2015 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import hashlib
from xpra.log import Logger
log = Logger("paint")
deltalog = Logger("delta")

from threading import Lock
from xpra.net.mmap_pipe import mmap_read
from xpra.net import compression
from xpra.util import typedict, csv
from xpra.codecs.loader import get_codec
from xpra.codecs.video_helper import getVideoHelper
from xpra.os_util import BytesIOClass, bytestostr
from xpra.codecs.xor.cyxor import xor_str   #@UnresolvedImport
from xpra.codecs.argb.argb import unpremultiply_argb, unpremultiply_argb_in_place   #@UnresolvedImport

DELTA_BUCKETS = int(os.environ.get("XPRA_DELTA_BUCKETS", "5"))
INTEGRITY_HASH = os.environ.get("XPRA_INTEGRITY_HASH", "0")=="1"
WEBP_PILLOW = os.environ.get("XPRA_WEBP_PILLOW", "0")=="1"

#ie:
#CSC_OPTIONS = { "YUV420P" : {"RGBX" : [opencl.spec, swscale.spec], "BGRX" : ...} }
CSC_OPTIONS = None
def load_csc_options():
    global CSC_OPTIONS
    if CSC_OPTIONS is None:
        CSC_OPTIONS = {}
        vh = getVideoHelper()
        for csc_in in vh.get_csc_inputs():
            CSC_OPTIONS[csc_in] = vh.get_csc_specs(csc_in)
    return CSC_OPTIONS

#get the list of video encodings (and the module for each one):
VIDEO_DECODERS = None
def load_video_decoders():
    global VIDEO_DECODERS
    if VIDEO_DECODERS is None:
        VIDEO_DECODERS = {}
        vh = getVideoHelper()
        for encoding in vh.get_decodings():
            specs = vh.get_decoder_specs(encoding)
            for colorspace, decoders in specs.items():
                log("%-4s decoders for %7s: %s", encoding, colorspace, csv([d.get_type() for _,d in decoders]))
                assert len(decoders)>0
                #use the first one:
                _, decoder_module = decoders[0]
                VIDEO_DECODERS[encoding] = decoder_module
        log("video decoders: %s", dict((e,d.get_type()) for e,d in VIDEO_DECODERS.items()))
    return VIDEO_DECODERS


def fire_paint_callbacks(callbacks, success, message=""):
    for x in callbacks:
        try:
            x(success, message)
        except KeyboardInterrupt:
            raise
        except:
            log.error("error calling %s(%s)", x, success, exc_info=True)


"""
Generic superclass for all Backing code,
see CairoBacking and GTKWindowBacking for actual implementations
"""
class WindowBackingBase(object):
    def __init__(self, wid, window_alpha, idle_add):
        load_csc_options()
        load_video_decoders()
        self.wid = wid
        self.idle_add = idle_add
        self._alpha_enabled = window_alpha
        self._backing = None
        self._delta_pixel_data = [None for _ in range(DELTA_BUCKETS)]
        self._video_decoder = None
        self._csc_decoder = None
        self._decoder_lock = Lock()
        self._PIL_encodings = []
        PIL = get_codec("dec_pillow")
        if PIL:
            self._PIL_encodings = PIL.get_encodings()
        self.draw_needs_refresh = True
        self.mmap = None
        self.mmap_enabled = False

    def enable_mmap(self, mmap_area):
        self.mmap = mmap_area
        self.mmap_enabled = True

    def close(self):
        self._backing = None
        log("%s.close() video_decoder=%s", self, self._video_decoder)
        #try without blocking, if that fails then
        #the lock is held by the decoding thread,
        #and it will run the cleanup after releasing the lock
        #(it checks for self._backing None)
        self.close_decoder(False)

    def close_decoder(self, blocking=False):
        if self._decoder_lock is None or not self._decoder_lock.acquire(blocking):
            return False
        try:
            self.do_clean_video_decoder()
            self.do_clean_csc_decoder()
            return True
        finally:
            self._decoder_lock.release()

    def do_clean_video_decoder(self):
        if self._video_decoder:
            self._video_decoder.clean()
            self._video_decoder = None

    def do_clean_csc_decoder(self):
        if self._csc_decoder:
            self._csc_decoder.clean()
            self._csc_decoder = None


    def get_encoding_properties(self):
        return {
                 "encodings.rgb_formats"    : self.RGB_MODES,
                 "encoding.transparency"    : self._alpha_enabled,
                 "encoding.full_csc_modes"  : self._get_full_csc_modes(self.RGB_MODES),
                 }

    def _get_full_csc_modes(self, rgb_modes):
        #calculate the server CSC modes the server is allowed to use
        #based on the client CSC modes we can convert to in the backing class we use
        #and trim the transparency if we cannot handle it
        target_rgb_modes = list(rgb_modes)
        if not self._alpha_enabled:
            target_rgb_modes = [x for x in target_rgb_modes if x.find("A")<0]
        full_csc_modes = getVideoHelper().get_server_full_csc_modes_for_rgb(*target_rgb_modes)
        log("_get_full_csc_modes(%s)=%s (target_rgb_modes=%s)", rgb_modes, full_csc_modes, target_rgb_modes)
        return full_csc_modes


    def unpremultiply(self, img_data):
        if type(img_data)!=str:
            try:
                unpremultiply_argb_in_place(img_data)
                return img_data
            except:
                log.warn("failed to unpremultiply %s (len=%s)" % (type(img_data), len(img_data)))
        return unpremultiply_argb(img_data)


    def process_delta(self, raw_data, width, height, rowstride, options):
        """
            Can be called from any thread, decompresses and xors the rgb raw_data,
            then stores it for later xoring if needed.
        """
        img_data = raw_data
        if options:
            #check for one of the compressors:
            comp = [x for x in compression.ALL_COMPRESSORS if options.intget(x, 0)]
            if comp:
                assert len(comp)==1, "more than one compressor specified: %s" % str(comp)
                img_data = compression.decompress_by_name(raw_data, algo=comp[0])
        if len(img_data)!=rowstride * height:
            deltalog.error("invalid img data length: expected %s but got %s (%s: %s)", rowstride * height, len(img_data), type(img_data), str(img_data)[:256])
            raise Exception("expected %s bytes for %sx%s with rowstride=%s but received %s (%s compressed)" %
                                (rowstride * height, width, height, rowstride, len(img_data), len(raw_data)))
        delta = options.intget("delta", -1)
        bucket = options.intget("bucket", 0)
        rgb_format = options.strget("rgb_format")
        rgb_data = img_data
        if delta>=0:
            assert bucket>=0 and bucket<DELTA_BUCKETS, "invalid delta bucket number: %s" % bucket
            if self._delta_pixel_data[bucket] is None:
                raise Exception("delta region bucket %s references pixmap data we do not have!" % bucket)
            lwidth, lheight, lrgb_format, seq, ldata = self._delta_pixel_data[bucket]
            assert width==lwidth and height==lheight and delta==seq, \
                "delta bucket %s data does not match: expected %s but got %s" % (bucket, (width, height, delta), (lwidth, lheight, seq))
            assert lrgb_format==rgb_format, "delta region uses %s format, was expecting %s" % (rgb_format, lrgb_format)
            deltalog("delta: xoring with bucket %i", bucket)
            rgb_data = xor_str(img_data, ldata)
        #store new pixels for next delta:
        store = options.intget("store", -1)
        if store>=0:
            deltalog("delta: storing sequence %i in bucket %i", store, bucket)
            self._delta_pixel_data[bucket] =  width, height, rgb_format, store, rgb_data
        return rgb_data


    def paint_image(self, coding, img_data, x, y, width, height, options, callbacks):
        """ can be called from any thread """
        #log("paint_image(%s, %s bytes, %s, %s, %s, %s, %s, %s)", coding, len(img_data), x, y, width, height, options, callbacks)
        PIL = get_codec("PIL")
        assert PIL, "PIL not found"
        buf = BytesIOClass(img_data)
        img = PIL.Image.open(buf)
        assert img.mode in ("L", "P", "RGB", "RGBA"), "invalid image mode: %s" % img.mode
        transparency = options.get("transparency", -1)
        if img.mode=="P":
            if transparency>=0:
                #this deals with alpha without any extra work
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
        elif img.mode=="L":
            if transparency>=0:
                #why do we have to deal with alpha ourselves??
                def mask_value(a):
                    if a!=transparency:
                        return 255
                    return 0
                mask = PIL.Image.eval(img, mask_value)
                mask = mask.convert("L")
                def nomask_value(a):
                    if a!=transparency:
                        return a
                    return 0
                img = PIL.Image.eval(img, nomask_value)
                img = img.convert("RGBA")
                img.putalpha(mask)
            else:
                img = img.convert("RGB")

        #use tobytes() if present, fallback to tostring():
        data_fn = getattr(img, "tobytes", getattr(img, "tostring"))
        raw_data = data_fn("raw", img.mode)
        paint_options = typedict(options)
        if img.mode=="RGB":
            #PIL flattens the data to a continuous straightforward RGB format:
            rowstride = width*3
            paint_options["rgb_format"] = "RGB"
            img_data = self.process_delta(raw_data, width, height, rowstride, options)
            self.idle_add(self.do_paint_rgb24, img_data, x, y, width, height, rowstride, paint_options, callbacks)
        elif img.mode=="RGBA":
            rowstride = width*4
            paint_options["rgb_format"] = "RGBA"
            img_data = self.process_delta(raw_data, width, height, rowstride, options)
            self.idle_add(self.do_paint_rgb32, img_data, x, y, width, height, rowstride, paint_options, callbacks)
        return False

    def paint_webp(self, img_data, x, y, width, height, options, callbacks):
        dec_webp = get_codec("dec_webp")
        if not dec_webp or WEBP_PILLOW:
            #if webp is enabled, then Pillow should be able to take care of it:
            return self.paint_image("webp", img_data, x, y, width, height, options, callbacks)
        has_alpha = options.get("has_alpha", False)
        buffer_wrapper, width, height, stride, has_alpha, rgb_format = dec_webp.decompress(img_data, has_alpha, options.get("rgb_format"))
        #replace with the actual rgb format we get from the decoder:
        options["rgb_format"] = rgb_format
        def free_buffer(*args):
            buffer_wrapper.free()
        callbacks.append(free_buffer)
        data = buffer_wrapper.get_pixels()
        if len(rgb_format)==4:
            return self.paint_rgb32(data, x, y, width, height, stride, options, callbacks)
        else:
            return self.paint_rgb24(data, x, y, width, height, stride, options, callbacks)

    def paint_rgb24(self, raw_data, x, y, width, height, rowstride, options, callbacks):
        """ called from non-UI thread
            this method calls process_delta before calling do_paint_rgb24 from the UI thread via idle_add
        """
        rgb24_data = self.process_delta(raw_data, width, height, rowstride, options)
        self.idle_add(self.do_paint_rgb24, rgb24_data, x, y, width, height, rowstride, options, callbacks)
        return  False

    def do_paint_rgb24(self, img_data, x, y, width, height, rowstride, options, callbacks):
        """ must be called from UI thread
            this method is only here to ensure that we always fire the callbacks,
            the actual paint code is in _do_paint_rgb24
        """
        try:
            success = (self._backing is not None) and self._do_paint_rgb24(img_data, x, y, width, height, rowstride, options)
            fire_paint_callbacks(callbacks, success)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            if not self._backing:
                message = "paint error on closed backing ignored"
                log(message)
            else:
                log.error("do_paint_rgb24 error", exc_info=True)
                message = "do_paint_rgb24 error: %s" % e
            fire_paint_callbacks(callbacks, False, message)

    def _do_paint_rgb24(self, img_data, x, y, width, height, rowstride, options):
        raise Exception("override me!")


    def paint_rgb32(self, raw_data, x, y, width, height, rowstride, options, callbacks):
        """ called from non-UI thread
            this method calls process_delta before calling do_paint_rgb32 from the UI thread via idle_add
        """
        rgb32_data = self.process_delta(raw_data, width, height, rowstride, options)
        self.idle_add(self.do_paint_rgb32, rgb32_data, x, y, width, height, rowstride, options, callbacks)
        return  False

    def do_paint_rgb32(self, img_data, x, y, width, height, rowstride, options, callbacks):
        """ must be called from UI thread
            this method is only here to ensure that we always fire the callbacks,
            the actual paint code is in _do_paint_rgb32
        """
        try:
            success = (self._backing is not None) and self._do_paint_rgb32(img_data, x, y, width, height, rowstride, options)
            fire_paint_callbacks(callbacks, success)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log.error("do_paint_rgb32 error", exc_info=True)
            fire_paint_callbacks(callbacks, False, "do_paint_rgb32 error: %s" % e)

    def _do_paint_rgb32(self, img_data, x, y, width, height, rowstride, options):
        raise Exception("override me!")


    def make_csc(self, src_width, src_height, src_format,
                       dst_width, dst_height, dst_format_options, speed):
        global CSC_OPTIONS
        in_options = CSC_OPTIONS.get(src_format, {})
        assert len(in_options)>0, "no csc options for '%s' input in %s" % (src_format, CSC_OPTIONS)
        for dst_format in dst_format_options:
            specs = in_options.get(dst_format)
            log("make_csc%s specs=%s", (src_width, src_height, src_format, dst_width, dst_height, dst_format_options, speed), specs)
            if not specs:
                continue
            for spec in specs:
                if spec.min_w>src_width or spec.min_w>dst_width or \
                   spec.max_w<src_width or spec.max_w<dst_width:
                    log("csc module %s cannot cope with dimensions %sx%s to %sx%s", spec.codec_class, src_width, src_height, dst_width, dst_height)
                    continue
                if not spec.can_scale and (src_width!=dst_width or src_height!=dst_height):
                    log("csc module %s cannot scale")
                    continue
                try:
                    csc = spec.make_instance()
                    csc.init_context(src_width, src_height, src_format,
                               dst_width, dst_height, dst_format, speed)
                    return csc
                except:
                    log.error("failed to create csc instance of %s for %s to %s", spec.codec_class, src_format, dst_format, exc_info=True)
        raise Exception("no csc module found for %s(%sx%s) to %s(%sx%s) in %s" % (src_format, src_width, src_height, " or ".join(dst_format_options), dst_width, dst_height, CSC_OPTIONS))

    def paint_with_video_decoder(self, decoder_module, coding, img_data, x, y, width, height, options, callbacks):
        #log("paint_with_video_decoder%s", (decoder_module, coding, "%s bytes" % len(img_data), x, y, width, height, options, callbacks))
        assert decoder_module, "decoder module not found for %s" % coding
        with self._decoder_lock:
            if self._backing is None:
                message = "window %s is already gone!" % self.wid
                log(message)
                fire_paint_callbacks(callbacks, False, message)
                return  False
            enc_width, enc_height = options.intpair("scaled_size", (width, height))
            input_colorspace = options.strget("csc")
            if not input_colorspace:
                message = "csc mode is missing from the video options!"
                log.error(message)
                fire_paint_callbacks(callbacks, False, message)
                return  False
            #do we need a prep step for decoders that cannot handle the input_colorspace directly?
            decoder_colorspaces = decoder_module.get_input_colorspaces(coding)
            assert input_colorspace in decoder_colorspaces, "decoder does not support %s for %s" % (input_colorspace, coding)

            if self._video_decoder:
                if self._video_decoder.get_encoding()!=coding:
                    log("paint_with_video_decoder: encoding changed from %s to %s", self._video_decoder.get_encoding(), coding)
                    self.do_clean_video_decoder()
                elif self._video_decoder.get_width()!=enc_width or self._video_decoder.get_height()!=enc_height:
                    log("paint_with_video_decoder: window dimensions have changed from %s to %s", (self._video_decoder.get_width(), self._video_decoder.get_height()), (enc_width, enc_height))
                    self.do_clean_video_decoder()
                elif self._video_decoder.get_colorspace()!=input_colorspace:
                    #this should only happen on encoder restart, which means this should be the first frame:
                    l = log
                    if options.get("frame", 0)>1:
                        l = log.warn
                    l("paint_with_video_decoder: colorspace changed from %s to %s", self._video_decoder.get_colorspace(), input_colorspace)
                    self.do_clean_video_decoder()
                elif options.get("frame")==0:
                    log("paint_with_video_decoder: first frame of new stream")
                    self.do_clean_video_decoder()
            if self._video_decoder is None:
                log("paint_with_video_decoder: new %s(%s,%s,%s)", decoder_module.Decoder, width, height, input_colorspace)
                self._video_decoder = decoder_module.Decoder()
                self._video_decoder.init_context(coding, enc_width, enc_height, input_colorspace)
                log("paint_with_video_decoder: info=%s", self._video_decoder.get_info())

            img = self._video_decoder.decompress_image(img_data, options)
            if not img:
                fire_paint_callbacks(callbacks, False, "video decoder %s did not return an image", self._video_decoder)
                log.error("paint_with_video_decoder: wid=%s, %s decompression error on %s bytes of picture data for %sx%s pixels using %s, options=%s",
                      self.wid, coding, len(img_data), width, height, self._video_decoder, options)
                return False
            self.do_video_paint(img, x, y, enc_width, enc_height, width, height, options, callbacks)
        if self._backing is None:
            self.close_decoder(True)
        return  False

    def do_video_paint(self, img, x, y, enc_width, enc_height, width, height, options, callbacks):
        #try 24 bit first (paint_rgb24), then 32 bit (paint_rgb32):
        target_rgb_formats = self.RGB_MODES
        #as some video formats like vpx can forward transparency
        #also we could skip the csc step in some cases:
        pixel_format = img.get_pixel_format()
        if self._csc_decoder is not None:
            if self._csc_decoder.get_src_format()!=pixel_format:
                log("do_video_paint csc: switching src format from %s to %s", self._csc_decoder.get_src_format(), pixel_format)
                self.do_clean_csc_decoder()
            elif self._csc_decoder.get_dst_format() not in target_rgb_formats:
                log("do_video_paint csc: switching dst format from %s to %s", self._csc_decoder.get_dst_format(), target_rgb_formats)
                self.do_clean_csc_decoder()
            elif self._csc_decoder.get_src_width()!=enc_width or self._csc_decoder.get_src_height()!=enc_height:
                log("do_video_paint csc: switching src size from %sx%s to %sx%s",
                         enc_width, enc_height, self._csc_decoder.get_src_width(), self._csc_decoder.get_src_height())
                self.do_clean_csc_decoder()
            elif self._csc_decoder.get_dst_width()!=width or self._csc_decoder.get_dst_height()!=height:
                log("do_video_paint csc: switching src size from %sx%s to %sx%s",
                         width, height, self._csc_decoder.get_dst_width(), self._csc_decoder.get_dst_height())
                self.do_clean_csc_decoder()
        if self._csc_decoder is None:
            #use higher quality csc to compensate for lower quality source
            #(which generally means that we downscaled via YUV422P or lower)
            #or when upscaling the video:
            q = options.intget("quality", 50)
            csc_speed = int(min(100, 100-q, 100.0 * (enc_width*enc_height) / (width*height)))
            self._csc_decoder = self.make_csc(enc_width, enc_height, pixel_format,
                                           width, height, target_rgb_formats, csc_speed)
            log("do_video_paint new csc decoder: %s", self._csc_decoder)
        rgb_format = self._csc_decoder.get_dst_format()
        rgb = self._csc_decoder.convert_image(img)
        log("do_video_paint rgb using %s.convert_image(%s)=%s", self._csc_decoder, img, rgb)
        img.free()
        assert rgb.get_planes()==0, "invalid number of planes for %s: %s" % (rgb_format, rgb.get_planes())
        #make a new options dict and set the rgb format:
        paint_options = typedict(options)
        paint_options["rgb_format"] = rgb_format
        #this will also take care of firing callbacks (from the UI thread):
        def paint():
            data = rgb.get_pixels()
            rowstride = rgb.get_rowstride()
            if len(rgb_format)==3:
                self.do_paint_rgb24(data, x, y, width, height, rowstride, paint_options, callbacks)
            else:
                assert len(rgb_format)==4
                self.do_paint_rgb32(data, x, y, width, height, rowstride, paint_options, callbacks)
            rgb.free()
        self.idle_add(paint)

    def paint_mmap(self, img_data, x, y, width, height, rowstride, options, callbacks):
        """ must be called from UI thread """
        #we could run just paint_rgb24 from the UI thread,
        #but this would not make much of a difference
        #and would complicate the code (add a callback to free mmap area)
        """ see _mmap_send() in server.py for details """
        assert self.mmap_enabled
        data = mmap_read(self.mmap, img_data)
        rgb_format = options.strget("rgb_format", "RGB")
        #Note: BGR(A) is only handled by gl_window_backing
        if rgb_format in ("RGB", "BGR"):
            self.do_paint_rgb24(data, x, y, width, height, rowstride, options, callbacks)
        elif rgb_format in ("RGBA", "BGRA", "BGRX", "RGBX"):
            self.do_paint_rgb32(data, x, y, width, height, rowstride, options, callbacks)
        else:
            raise Exception("invalid rgb format: %s" % rgb_format)
        return  False


    def draw_region(self, x, y, width, height, coding, img_data, rowstride, options, callbacks):
        """ dispatches the paint to one of the paint_XXXX methods """
        log("draw_region(%s, %s, %s, %s, %s, %s bytes, %s, %s, %s)", x, y, width, height, coding, len(img_data), rowstride, options, callbacks)
        coding = bytestostr(coding)
        options["encoding"] = coding            #used for choosing the color of the paint box
        if INTEGRITY_HASH:
            l = options.get("z.len")
            if l:
                assert l==len(img_data), "compressed pixel data failed length integrity check: expected %i bytes but got %i" % (l, len(img_data))
            md5 = options.get("z.md5")
            if md5:
                h = hashlib.md5(img_data)
                hd = h.hexdigest()
                assert md5==hd, "pixel data failed compressed md5 integrity check: expected %s but got %s" % (md5, hd)
            deltalog("passed compressed data integrity checks: len=%s, md5=%s (type=%s)", l, md5, type(img_data))
        if coding == "mmap":
            self.idle_add(self.paint_mmap, img_data, x, y, width, height, rowstride, options, callbacks)
        elif coding == "rgb24" or coding == "rgb32":
            #avoid confusion over how many bytes-per-pixel we may have:
            rgb_format = options.get("rgb_format")
            if rgb_format:
                Bpp = len(rgb_format)
            elif coding=="rgb24":
                Bpp = 3
            else:
                Bpp = 4
            if rowstride==0:
                rowstride = width * Bpp
            if Bpp==3:
                self.paint_rgb24(img_data, x, y, width, height, rowstride, options, callbacks)
            else:
                self.paint_rgb32(img_data, x, y, width, height, rowstride, options, callbacks)
        elif coding in VIDEO_DECODERS:
            self.paint_with_video_decoder(VIDEO_DECODERS.get(coding), coding, img_data, x, y, width, height, options, callbacks)
        elif coding == "webp":
            self.paint_webp(img_data, x, y, width, height, options, callbacks)
        elif coding in self._PIL_encodings:
            self.paint_image(coding, img_data, x, y, width, height, options, callbacks)
        else:
            self.do_draw_region(x, y, width, height, coding, img_data, rowstride, options, callbacks)

    def do_draw_region(self, x, y, width, height, coding, img_data, rowstride, options, callbacks):
        raise Exception("invalid encoding: %s" % coding)
