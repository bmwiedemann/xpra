# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2013, 2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import copy
from threading import Lock
from xpra.log import Logger, debug_if_env
log = Logger()
debug = debug_if_env(log, "XPRA_VIDEOPIPELINE_DEBUG")

from xpra.codecs.loader import get_codec

instance = None


class VideoHelper(object):

    def __init__(self, vspecs={}, cscspecs={}, init=False):
        self._video_encoder_specs = vspecs
        self._csc_encoder_specs = cscspecs
        #bits needed to ensure we can initialize just once
        #even when called from multiple threads:
        self._initialized = init
        self._lock = Lock()

    def clone(self, deep=False):
        if not self._initialized:
            self.init()
        if deep:
            ves = copy.deepcopy(self._video_encoder_specs)
            ces = copy.deepcopy(self._csc_encoder_specs)
        else:
            ves = self._video_encoder_specs.copy()
            ces = self._csc_encoder_specs.copy()
        #make a deep copy:
        return VideoHelper(ves, ces, True)

    def get_info(self):
        d = {}
        for in_csc, specs in self._csc_encoder_specs.items():
            for out_csc, spec in specs:
                d.setdefault("csc."+in_csc+"_to_"+out_csc, []).append(spec.codec_type)
        for encoding, encoder_specs in self._video_encoder_specs.items():
            for in_csc, specs in encoder_specs.items():
                for spec in specs:
                    d.setdefault("encoding."+in_csc+"_to_"+encoding, []).append(spec.codec_type)
        return d

    def init(self):
        try:
            self._lock.acquire()
            #check again with lock held (in case of race):
            if self._initialized:
                return
            self.init_video_encoders_options()
            self.init_csc_options()
            self._initialized = True
        finally:
            self._lock.release()

    def get_encodings(self):
        return self._video_encoder_specs.keys()

    def get_encoder_specs(self, encoding):
        return self._video_encoder_specs.get(encoding, [])

    def get_csc_specs(self, src_format):
        return self._csc_encoder_specs.get(src_format, [])

    def init_video_options(self):
        self.init_video_encoders_options()
        self.init_csc_options()

    def init_video_encoders_options(self):
        try:
            self.init_video_encoder_option("enc_vpx")
        except:
            log.warn("init_video_encoders_options() cannot add vpx encoder", exc_info=True)
        try:
            self.init_video_encoder_option("enc_x264")
        except:
            log.warn("init_video_encoders_options() cannot add x264 encoder", exc_info=True)
        try:
            self.init_video_encoder_option("enc_nvenc")
        except:
            log.warn("init_video_encoders_options() cannot add nvenc encoder", exc_info=True)
        debug("init_video_encoders_options() video encoder specs: %s", self._video_encoder_specs)

    def init_video_encoder_option(self, encoder_name):
        encoder_module = get_codec(encoder_name)
        debug("init_video_encoder_option(%s) module=%s", encoder_name, encoder_module)
        if not encoder_module:
            return
        encoder_type = encoder_module.get_type()
        try:
            encoder_module.init_module()
        except Exception, e:
            log.warn("cannot use %s module %s: %s", encoder_type, encoder_module, e, exc_info=True)
            return
        colorspaces = encoder_module.get_colorspaces()
        debug("init_video_encoder_option(%s) %s input colorspaces=%s", encoder_module, encoder_type, colorspaces)
        encodings = encoder_module.get_encodings()
        debug("init_video_encoder_option(%s) %s encodings=%s", encoder_module, encoder_type, encodings)
        for encoding in encodings:
            for colorspace in colorspaces:
                spec = encoder_module.get_spec(encoding, colorspace)
                self.add_encoder_spec(encoding, colorspace, spec)

    def add_encoder_spec(self, encoding, colorspace, spec):
        self._video_encoder_specs.setdefault(encoding, {}).setdefault(colorspace, []).append(spec)


    def init_csc_options(self):
        try:
            self.init_csc_option("csc_swscale")
        except:
            log.warn("init_csc_options() cannot add swscale csc", exc_info=True)
        try:
            self.init_csc_option("csc_cython")
        except:
            log.warn("init_csc_options() cannot add cython csc", exc_info=True)
        try:
            self.init_csc_option("csc_opencl")
        except:
            log.warn("init_csc_options() cannot add opencl csc", exc_info=True)
        try:
            self.init_csc_option("csc_nvcuda")
        except:
            log.warn("init_csc_options() cannot add nvcuda csc", exc_info=True)
        debug("init_csc_options() csc specs: %s", self._csc_encoder_specs)
        for src_format, specs in sorted(self._csc_encoder_specs.items()):
            debug("%s - %s options:", src_format, len(specs))
            d = {}
            for dst_format, spec in sorted(specs):
                d.setdefault(dst_format, set()).add(spec.info())
            for dst_format, specs in sorted(d.items()):
                debug(" * %s via: %s", dst_format, sorted(list(specs)))

    def init_csc_option(self, csc_name):
        csc_module = get_codec(csc_name)
        debug("init_csc_option(%s) module=%s", csc_name, csc_module)
        if csc_module is None:
            return
        csc_type = csc_module.get_type()
        try:
            csc_module.init_module()
        except Exception, e:
            log.warn("cannot use %s module %s: %s", csc_type, csc_module, e, exc_info=True)
            return
        in_cscs = csc_module.get_input_colorspaces()
        for in_csc in in_cscs:
            out_cscs = csc_module.get_output_colorspaces(in_csc)
            debug("init_csc_option(..) %s.get_output_colorspaces(%s)=%s", csc_module.get_type(), in_csc, out_cscs)
            for out_csc in out_cscs:
                spec = csc_module.get_spec(in_csc, out_csc)
                self.add_csc_spec(in_csc, out_csc, spec)

    def add_csc_spec(self, in_csc, out_csc, spec):
        item = out_csc, spec
        self._csc_encoder_specs.setdefault(in_csc, []).append(item)


instance = VideoHelper()
def getVideoHelper():
    global instance
    return instance
