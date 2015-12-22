# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2015 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import time
import os
import hashlib
from collections import deque

from xpra.log import Logger
log = Logger("window", "encoding")
refreshlog = Logger("window", "refresh")
compresslog = Logger("window", "compress")
scalinglog = Logger("scaling")
iconlog = Logger("icon")
deltalog = Logger("delta")
avsynclog = Logger("av-sync")


AUTO_REFRESH_THRESHOLD = int(os.environ.get("XPRA_AUTO_REFRESH_THRESHOLD", 100))
AUTO_REFRESH_QUALITY = int(os.environ.get("XPRA_AUTO_REFRESH_QUALITY", 100))
AUTO_REFRESH_SPEED = int(os.environ.get("XPRA_AUTO_REFRESH_SPEED", 50))

MAX_PIXELS_PREFER_RGB = int(os.environ.get("XPRA_MAX_PIXELS_PREFER_RGB", 4096))

DELTA = os.environ.get("XPRA_DELTA", "1")=="1"
MIN_DELTA_SIZE = int(os.environ.get("XPRA_MIN_DELTA_SIZE", "1024"))
MAX_DELTA_SIZE = int(os.environ.get("XPRA_MAX_DELTA_SIZE", "32768"))
MAX_DELTA_HITS = int(os.environ.get("XPRA_MAX_DELTA_HITS", "20"))
MIN_WINDOW_REGION_SIZE = int(os.environ.get("XPRA_MIN_WINDOW_REGION_SIZE", "1024"))

HAS_ALPHA = os.environ.get("XPRA_ALPHA", "1")=="1"
FORCE_BATCH = os.environ.get("XPRA_FORCE_BATCH", "0")=="1"
STRICT_MODE = os.environ.get("XPRA_ENCODING_STRICT_MODE", "0")=="1"
MERGE_REGIONS = os.environ.get("XPRA_MERGE_REGIONS", "1")=="1"
INTEGRITY_HASH = os.environ.get("XPRA_INTEGRITY_HASH", "0")=="1"
MAX_SYNC_BUFFER_SIZE = int(os.environ.get("XPRA_MAX_SYNC_BUFFER_SIZE", "256"))*1024*1024        #256MB
AV_SYNC_RATE_CHANGE = int(os.environ.get("XPRA_AV_SYNC_RATE_CHANGE", "20"))
AV_SYNC_TIME_CHANGE = int(os.environ.get("XPRA_AV_SYNC_TIME_CHANGE", "500"))


from xpra.util import updict
from xpra.os_util import StringIOClass, memoryview_to_bytes
from xpra.server.window.window_stats import WindowPerformanceStatistics
from xpra.simple_stats import add_list_stats
from xpra.server.window.batch_delay_calculator import calculate_batch_delay, get_target_speed, get_target_quality
from xpra.server.cystats import time_weighted_average   #@UnresolvedImport
from xpra.server.window.region import rectangle, add_rectangle, remove_rectangle, merge_all   #@UnresolvedImport
from xpra.codecs.xor.cyxor import xor_str           #@UnresolvedImport
from xpra.server.picture_encode import webp_encode, rgb_encode, mmap_send
from xpra.codecs.loader import PREFERED_ENCODING_ORDER, get_codec
from xpra.codecs.codec_constants import LOSSY_PIXEL_FORMATS
from xpra.net import compression


class WindowSource(object):
    """
    We create a Window Source for each window we send pixels for.

    The UI thread calls 'damage' for screen updates,
    we eventually call 'ServerSource.call_in_encode_thread' to queue the damage compression,
    the function can then submit the packet using the 'queue_damage_packet' callback.

    (also by 'send_window_icon' and clibpoard packets)
    """

    @staticmethod
    def staticinit(idle_add, timeout_add, source_remove):
        WindowSource._encoding_warnings = set()
        #scheduling stuff (gobject wrapped):
        WindowSource.idle_add = idle_add
        WindowSource.timeout_add = timeout_add
        WindowSource.source_remove = source_remove

    def __init__(self, queue_size, call_in_encode_thread, queue_packet, compressed_wrapper,
                    statistics,
                    wid, window, batch_config, auto_refresh_delay,
                    av_sync, av_sync_delay,
                    video_helper,
                    server_core_encodings, server_encodings,
                    encoding, encodings, core_encodings, encoding_options, icons_encoding_options,
                    rgb_formats,
                    default_encoding_options,
                    mmap, mmap_size):
        # mmap:
        self._mmap = mmap
        self._mmap_size = mmap_size

        self.init_vars()

        self.queue_size   = queue_size                  #callback to get the size of the damage queue
        self.call_in_encode_thread = call_in_encode_thread  #callback to add damage data which is ready to compress to the damage processing queue
        self.queue_packet = queue_packet                #callback to add a network packet to the outgoing queue
        self.compressed_wrapper = compressed_wrapper    #callback utility for making compressed wrappers
        self.wid = wid
        self.global_statistics = statistics             #shared/global statistics from ServerSource
        self.statistics = WindowPerformanceStatistics()
        self.av_sync = av_sync
        self.av_sync_delay = av_sync_delay
        self.av_sync_delay_target = av_sync_delay
        self.av_sync_timer = None
        self.encode_queue = []
        self.encode_queue_max_size = 10

        self.server_core_encodings = server_core_encodings
        self.server_encodings = server_encodings
        self.encoding = encoding                        #the current encoding
        self.encodings = encodings                      #all the encodings supported by the client
        self.core_encodings = core_encodings            #the core encodings supported by the client
        self.rgb_formats = rgb_formats                  #supported RGB formats (RGB, RGBA, ...) - used by mmap
        self.encoding_options = encoding_options        #extra options which may be specific to the encoder (ie: x264)
        self.icons_encoding_options = icons_encoding_options    #icon caps
        self.rgb_zlib = compression.use_zlib and encoding_options.boolget("rgb_zlib", True)     #server and client support zlib pixel compression (not to be confused with 'rgb24zlib'...)
        self.rgb_lz4 = compression.use_lz4 and encoding_options.boolget("rgb_lz4", False)       #server and client support lz4 pixel compression
        self.rgb_lzo = compression.use_lzo and encoding_options.boolget("rgb_lzo", False)       #server and client support lzo pixel compression
        self.supports_transparency = HAS_ALPHA and encoding_options.boolget("transparency")
        self.full_frames_only = self.is_tray or encoding_options.boolget("full_frames_only")
        self.supports_flush = encoding_options.get("flush")
        ropts = set(("png", "webp", "rgb24", "rgb32", "jpeg", "webp"))     #default encodings for auto-refresh
        ropts = ropts.intersection(set(self.server_core_encodings)) #ensure the server has support for it
        ropts = ropts.intersection(set(self.core_encodings))        #ensure the client has support for it
        self.client_refresh_encodings = encoding_options.strlistget("auto_refresh_encodings", list(ropts))
        self.supports_delta = []
        if not window.is_tray() and DELTA:
            self.supports_delta = [x for x in encoding_options.strlistget("supports_delta", []) if x in ("png", "rgb24", "rgb32")]
            if self.supports_delta:
                self.delta_buckets = min(25, encoding_options.intget("delta_buckets", 1))
                self.delta_pixel_data = [None for _ in range(self.delta_buckets)]
        self.batch_config = batch_config
        #auto-refresh:
        self.auto_refresh_delay = auto_refresh_delay
        self.last_auto_refresh_message = None
        self.video_helper = video_helper
        if window.is_shadow():
            self.max_delta_size = -1

        self.is_OR = window.is_OR()
        self.is_tray = window.is_tray()
        self.is_shadow = window.is_shadow()
        self.has_alpha = window.has_alpha()
        self.window_dimensions = 0, 0
        self.fullscreen = not self.is_tray and window.get_property("fullscreen")
        self.scaling_control = default_encoding_options.intget("scaling.control", 1)    #ServerSource sets defaults with the client's scaling.control value
        self.scaling = None
        self.maximized = False          #set by the client!
        self.iconic = False
        self.window_signal_handlers = []
        if "iconic" in window.get_dynamic_property_names():
            self.iconic = window.get_property("iconic")
            sid = window.connect("notify::iconic", self._iconic_changed)
            self.window_signal_handlers.append((window, sid))
        if "fullscreen" in window.get_dynamic_property_names():
            sid = window.connect("notify::fullscreen", self._fullscreen_changed)
            self.window_signal_handlers.append((window, sid))

        #for deciding between small regions and full screen updates:
        self.max_small_regions = 40
        self.max_bytes_percent = 60
        self.small_packet_cost = 1024
        if mmap and mmap_size>0:
            #with mmap, we can move lots of data around easily
            #so favour large screen updates over small packets
            self.max_small_regions = 10
            self.max_bytes_percent = 25
            self.small_packet_cost = 4096
        self.pixel_format = None                            #ie: BGRX

        #for sending and batching window icon updates:
        self.window_icon_data = None
        self.send_window_icon_due = False
        self.theme_default_icons = icons_encoding_options.strlistget("default.icons", [])
        self.window_icon_greedy = icons_encoding_options.boolget("greedy", False)
        self.window_icon_size = icons_encoding_options.intpair("size", (64, 64))
        self.window_icon_max_size = icons_encoding_options.intpair("max_size", self.window_icon_size)
        self.window_icon_max_size = max(self.window_icon_max_size[0], 16), max(self.window_icon_max_size[1], 16)
        self.window_icon_size = min(self.window_icon_size[0], self.window_icon_max_size[0]), min(self.window_icon_size[1], self.window_icon_max_size[1])
        self.window_icon_size = max(self.window_icon_size[0], 16), max(self.window_icon_size[1], 16)
        iconlog("client icon settings: size=%s, max_size=%s, theme_default_icons=%s", self.window_icon_size, self.window_icon_max_size, self.theme_default_icons)

        # general encoding tunables (mostly used by video encoders):
        self._encoding_quality = deque(maxlen=100)   #keep track of the target encoding_quality: (event time, info, encoding speed)
        self._encoding_speed = deque(maxlen=100)     #keep track of the target encoding_speed: (event time, info, encoding speed)
        # they may have fixed values:
        self._fixed_quality = default_encoding_options.get("quality", 0)
        self._fixed_min_quality = default_encoding_options.get("min-quality", 0)
        self._fixed_speed = default_encoding_options.get("speed", 0)
        self._fixed_min_speed = default_encoding_options.get("min-speed", 0)
        #will be overriden by update_quality() and update_speed() called from update_encoding_selection()
        #just here for clarity:
        self._current_quality = 50
        self._current_speed = 50
        self._want_alpha = False
        self._lossless_threshold_base = 85
        self._lossless_threshold_pixel_boost = 20
        self._rgb_auto_threshold = MAX_PIXELS_PREFER_RGB

        self.init_encoders()
        self.update_encoding_selection(encoding)
        log("initial encoding for %s: %s", self.wid, self.encoding)

    def __repr__(self):
        return "WindowSource(%s : %s)" % (self.wid, self.window_dimensions)


    def init_encoders(self):
        self._encoders["rgb24"] = self.rgb_encode
        self._encoders["rgb32"] = self.rgb_encode
        enc_pillow = get_codec("enc_pillow")
        if enc_pillow:
            for x in enc_pillow.get_encodings():
                if x in self.server_core_encodings:
                    self._encoders[x] = self.pillow_encode
        #prefer this one over PIL supplied version:
        if "webp" in self.server_core_encodings:
            self._encoders["webp"] = self.webp_encode
        if self._mmap and self._mmap_size>0:
            self._encoders["mmap"] = self.mmap_encode

    def init_vars(self):
        self.server_core_encodings = []
        self.server_encodings = []
        self.encoding = None
        self.encodings = []
        self.encoding_last_used = None
        self.auto_refresh_encodings = []
        self.core_encodings = []
        self.rgb_formats = []
        self.client_refresh_encodings = []
        self.encoding_options = {}
        self.rgb_zlib = False
        self.rgb_lz4 = False
        self.rgb_lzo = False
        self.supports_transparency = False
        self.full_frames_only = False
        self.supports_delta = []
        self.delta_buckets = 0
        self.delta_pixel_data = []
        self.suspended = False
        self.strict = STRICT_MODE
        #
        self.auto_refresh_delay = 0
        self.video_helper = None
        self.refresh_event_time = 0
        self.refresh_target_time = 0
        self.refresh_timer = None
        self.refresh_regions = []
        self.timeout_timer = None
        self.expire_timer = None
        self.soft_timer = None
        self.soft_expired = 0
        self.max_soft_expired = 5
        self.min_delta_size = MIN_DELTA_SIZE
        self.max_delta_size = MAX_DELTA_SIZE
        self.is_OR = False
        self.is_tray = False
        self.is_shadow = False
        self.has_alpha = False
        self.window_dimensions = 0, 0
        self.fullscreen = False
        self.scaling_control = 0
        self.scaling = None
        self.maximized = False
        #
        self.max_small_regions = 0
        self.max_bytes_percent = 0
        self.small_packet_cost = 0
        #
        self._encoding_quality = []
        self._encoding_speed = []
        #
        self._fixed_quality = 0
        self._fixed_min_quality = 0
        self._fixed_speed = 0
        self._fixed_min_speed = 0
        #
        self._damage_delayed = None
        self._damage_delayed_expired = False
        self._sequence = 1
        self._damage_cancelled = 0
        self._damage_packet_sequence = 1
        encoders = {}
        if self._mmap and self._mmap_size>0:
            #we must always be able to send mmap
            #so we can reclaim its space
            encoders["mmap"] = self.mmap_encode
        self._encoders = encoders

    def cleanup(self):
        self.cancel_damage()
        self.statistics.reset()
        log("encoding_totals for wid=%s with primary encoding=%s : %s", self.wid, self.encoding, self.statistics.encoding_totals)
        self.init_vars()
        self._damage_cancelled = float("inf")
        def window_signal_handlers_cleanup():
            log("window_signal_handlers_cleanup: will disconnect %s", [int(sid) for _,sid in self.window_signal_handlers])
            for window, sid in self.window_signal_handlers:
                window.disconnect(sid)
            self.window_signal_handlers = []
        self.idle_add(window_signal_handlers_cleanup)


    def get_info(self):
        #should get prefixed with "client[M].window[N]." by caller
        """
            Add window specific stats
        """
        info = {
                "dimensions"            : self.window_dimensions,
                "encoding"              : self.encoding,
                "suspended"             : self.suspended or False
                }
        def up(prefix, d):
            updict(info, prefix, d)

        up("av-sync",       {"current"  : self.av_sync_delay,
                             "target"   : self.av_sync_delay_target})
        #heuristics
        up("encoding.lossless_threshold", {
                "base"                  : self._lossless_threshold_base,
                "pixel_boost"           : self._lossless_threshold_pixel_boost})
        up("encoding", {
                "rgb_threshold"         : self._rgb_auto_threshold,
                "mmap"                  : bool(self._mmap) and (self._mmap_size>0),
                "last_used"             : self.encoding_last_used or "",
                "full-frames-only"      : self.full_frames_only,
                "supports-transparency" : self.supports_transparency,
                "delta"                 : self.supports_delta,
                "delta.buckets"         : self.delta_buckets,
                })
        if self.pixel_format:
            info["pixel-format"] = self.pixel_format
        now = time.time()
        for i,x in enumerate(self.delta_pixel_data):
            if x:
                w, h, pixel_format, coding, store, buflen, _, hits, last_used = x
                info["encoding.delta.bucket[%s]" % i] = w, h, pixel_format, coding, store, buflen, hits, int((now-last_used)*1000)
        up("encoding",  self.get_quality_speed_info())
        try:
            #ie: get_strict_encoding -> "strict_encoding"
            info["encoding.selection"] = self.get_best_encoding.__name__.replace("get_", "")
        except:
            pass
        up("property",  self.get_property_info())
        up("batch",     self.batch_config.get_info())
        up("encodings", {
                 ""                     : self.encodings,
                 "core"                 : self.core_encodings,
                 "auto-refresh"         : self.client_refresh_encodings,
                 "rgb_formats"          : self.rgb_formats,
                 })
        larm = self.last_auto_refresh_message
        if larm:
            up("encodings.auto-refresh.last-event", {
                "elapsed"               : int(1000*(time.time()-larm[0])),
                "message"               : larm[1],
                                                     })
        up("icons", self.icons_encoding_options)
        idata = self.window_icon_data
        if idata:
            pixel_data, stride, w, h = idata
            up("icon", {
                    "width"     : w,
                    "height"    : h,
                    "stride"    : stride,
                    "bytes"     : len(pixel_data)
                               })

        info.update(self.statistics.get_info())
        return info

    def get_quality_speed_info(self):
        info = {}
        def add_last_rec_info(prefix, recs):
            #must make a list to work on (again!)
            l = list(recs)
            if len(l)>0:
                _, descr, _ = l[-1]
                for k,v in descr.items():
                    info[prefix+"."+k] = v
        quality_list = self._encoding_quality
        if quality_list:
            qp = "quality"
            add_list_stats(info, qp, [x for _, _, x in list(quality_list)])
            add_last_rec_info(qp, quality_list)
        speed_list = self._encoding_speed
        if speed_list:
            sp = "speed"
            add_list_stats(info, sp, [x for _, _, x in list(speed_list)])
            add_last_rec_info(sp, speed_list)
        return info

    def get_property_info(self):
        return {
                "fullscreen"            : self.fullscreen or False,
                #speed / quality properties (not necessarily the same as the video encoder settings..):
                "min_speed"             : self._fixed_min_speed,
                "speed"                 : self._fixed_speed,
                "min_quality"           : self._fixed_min_quality,
                "quality"               : self._fixed_quality,
                }


    def go_idle(self):
        self.batch_config.locked = True
        self.batch_config.saved = self.batch_config.delay
        self.batch_config.delay = max(500, self.batch_config.delay)

    def no_idle(self):
        if self.iconic:
            return
        self.batch_config.locked = False
        self.batch_config.delay = self.batch_config.saved


    def suspend(self):
        self.cancel_damage()
        self.statistics.reset()
        self.suspended = True

    def resume(self, window):
        self.cancel_damage()
        self.statistics.reset()
        self.suspended = False
        self.refresh(window, {"quality" : 100})
        if not self.is_OR and not self.is_tray:
            self.send_window_icon(window)

    def refresh(self, window, options={}):
        w, h = window.get_dimensions()
        self.damage(window, 0, 0, w, h, options)


    fallback_window_icon_surface = False
    @staticmethod
    def get_fallback_window_icon_surface():
        if WindowSource.fallback_window_icon_surface is False:
            try:
                import cairo
                from xpra.platform.paths import get_icon_filename
                fn = get_icon_filename("xpra.png")
                iconlog("get_fallback_window_icon_surface() icon filename=%s", fn)
                if os.path.exists(fn):
                    s = cairo.ImageSurface.create_from_png(fn)
            except Exception as e:
                iconlog.warn("failed to get fallback icon: %s", e)
                s = None
            WindowSource.fallback_window_icon_surface = s
        return WindowSource.fallback_window_icon_surface

    def send_window_icon(self, window):
        if self.suspended:
            return
        #this runs in the UI thread
        surf = window.get_property("icon")
        iconlog("send_window_icon(%s) icon=%s", window, surf)
        if not surf:
            #FIXME: this is a bit dirty,
            #we figure out if the client is likely to have an icon for this wmclass already,
            #(assuming the window even has a 'class-instance'), and if not we send the default
            try:
                c_i = window.get_property("class-instance")
            except:
                c_i = None
            if c_i and len(c_i)==2:
                wm_class = c_i[0].encode("utf-8")
                if wm_class in self.theme_default_icons:
                    iconlog("%s in client theme icons already (not sending default icon)", self.theme_default_icons)
                    return
                #try to load the icon for this class-instance from the theme:
                surf = window.get_default_window_icon()
                iconlog("send_window_icon(%s) using default window icon=%s", window, surf)
        if not surf and self.window_icon_greedy:
            #client does not set a default icon, so we must provide one every time
            #to make sure that the window icon does get set to something
            #(our icon is at least better than the window manager's default)
            surf = WindowSource.get_fallback_window_icon_surface()
            iconlog("using fallback window icon")
        if surf:
            #for debugging, save to a file so we can see it:
            #surf.write_to_png("S-%s-%s.png" % (self.wid, int(time.time())))
            #extract the data from the cairo surface for processing in the work queue:
            import cairo
            assert surf.get_format() == cairo.FORMAT_ARGB32
            self.window_icon_data = (surf.get_data(), surf.get_stride(), surf.get_width(), surf.get_height())
            if not self.send_window_icon_due:
                self.send_window_icon_due = True
                #call compress_clibboard via the work queue
                #and delay sending it by a bit to allow basic icon batching:
                delay = max(50, int(self.batch_config.delay))
                iconlog("send_window_icon(%s) wid=%s, icon=%s, compression scheduled in %sms", window, self.wid, surf, delay)
                self.timeout_add(delay, self.call_in_encode_thread, self.compress_and_send_window_icon)

    def compress_and_send_window_icon(self):
        #this runs in the work queue
        self.send_window_icon_due = False
        idata = self.window_icon_data
        if not idata:
            return
        pixel_data, stride, w, h = idata
        PIL = get_codec("PIL")
        max_w, max_h = self.window_icon_max_size
        #use png if supported and:
        # * if we must downscale it (bigger than what the client is willing to deal with)
        # * if not using a 4-stride (FIXME: should handle this with PIL too)
        use_png = PIL and ("png" in self.encodings) and (w>max_w or h>max_h or stride!=4*w)
        iconlog("compress_and_send_window_icon: %sx%s, sending as png=%s", w, h, use_png)
        if use_png:
            img = PIL.Image.frombuffer("RGBA", (w,h), pixel_data, "raw", "BGRA", 0, 1)
            icon_w, icon_h = self.window_icon_size
            if w>icon_w or h>icon_h:
                #scale the icon down to the size the client wants
                if w>=h:
                    h = min(max_h, int(h*icon_w/w))
                    w = icon_w
                else:
                    w = min(max_w, int(w*icon_h/h))
                    h = icon_h
                iconlog("scaling window icon down to %sx%s", w, h)
                img = img.resize((w,h), PIL.Image.ANTIALIAS)
            output = StringIOClass()
            img.save(output, 'PNG')
            compressed_data = output.getvalue()
            output.close()
            wrapper = compression.Compressed("png", compressed_data)
        else:
            wrapper = self.compressed_wrapper("premult_argb32", str(pixel_data))
        assert wrapper.datatype in ("premult_argb32", "png")
        packet = ("window-icon", self.wid, w, h, wrapper.datatype, wrapper)
        iconlog("queuing window icon update: %s", packet)
        self.queue_packet(packet)


    def set_scaling(self, scaling):
        scalinglog("set_scaling(%s)", scaling)
        self.scaling = scaling
        self.reconfigure(True)

    def set_scaling_control(self, scaling_control):
        scalinglog("set_scaling_control(%s)", scaling_control)
        self.scaling_control = max(0, min(100, scaling_control))
        self.reconfigure(True)

    def _fullscreen_changed(self, window, *args):
        self.fullscreen = window.get_property("fullscreen")
        log("window fullscreen state changed: %s", self.fullscreen)
        self.reconfigure(True)

    def _iconic_changed(self, window, *args):
        self.iconic = window.get_property("iconic")
        if self.iconic:
            self.go_idle()
        else:
            self.no_idle()

    def set_client_properties(self, properties):
        #filter out stuff we don't care about
        #to see if there is anything to set at all,
        #and if not, don't bother doing the potentially expensive update_encoding_selection()
        for k in ("workspace", "screen"):
            if k in properties:
                del properties[k]
        if properties:
            self.do_set_client_properties(properties)

    def do_set_client_properties(self, properties):
        self.maximized = properties.boolget("maximized", False)
        self.client_refresh_encodings = properties.strlistget("encoding.auto_refresh_encodings", self.client_refresh_encodings)
        self.full_frames_only = self.is_tray or properties.boolget("encoding.full_frames_only", self.full_frames_only)
        self.supports_transparency = HAS_ALPHA and properties.boolget("encoding.transparency", self.supports_transparency)
        self.encodings = properties.strlistget("encodings", self.encodings)
        self.core_encodings = properties.strlistget("encodings.core", self.core_encodings)
        rgb_formats = properties.strlistget("encodings.rgb_formats", self.rgb_formats)
        if not self.supports_transparency:
            #remove rgb formats with alpha
            rgb_formats = [x for x in rgb_formats if x.find("A")<0]
        self.rgb_formats = rgb_formats
        self.update_encoding_selection(self.encoding)

    def set_auto_refresh_delay(self, d):
        self.auto_refresh_delay = d

    def set_av_sync_delay(self, new_delay):
        self.av_sync_delay_target = new_delay
        self.schedule_av_sync_update()

    def schedule_av_sync_update(self, delay=0):
        avsynclog("schedule_av_sync_update(%i) wid=%i, delay=%i, target=%i, timer=%s", delay, self.wid, self.av_sync_delay, self.av_sync_delay_target, self.av_sync_timer)
        if not self.av_sync:
            self.av_sync_delay = 0
            return
        if self.av_sync_delay==self.av_sync_delay_target:
            return  #already up to date
        if self.av_sync_timer:
            return  #already scheduled
        self.av_sync_timer = self.timeout_add(delay, self.update_av_sync_delay)

    def update_av_sync_delay(self):
        self.av_sync_timer = None
        delta = self.av_sync_delay_target-self.av_sync_delay
        if delta==0:
            return
        #limit the rate of change:
        rdelta = min(AV_SYNC_RATE_CHANGE, max(-AV_SYNC_RATE_CHANGE, delta))
        avsynclog("update_av_sync_delay() wid=%i, current=%s, target=%s, adding %s (capped to +-%s from %s)", self.wid, self.av_sync_delay, self.av_sync_delay_target, rdelta, AV_SYNC_RATE_CHANGE, delta)
        self.av_sync_delay += rdelta
        if self.av_sync_delay!=self.av_sync_delay_target:
            self.schedule_av_sync_update(AV_SYNC_TIME_CHANGE)


    def set_new_encoding(self, encoding, strict):
        """ Changes the encoder for the given 'window_ids',
            or for all windows if 'window_ids' is None.
        """
        if strict is not None:
            self.strict = strict or STRICT_MODE
        if self.encoding==encoding:
            return
        self.statistics.reset()
        self.delta_pixel_data = [None for _ in range(self.delta_buckets)]
        self.update_encoding_selection(encoding)


    def update_encoding_selection(self, encoding=None, exclude=[]):
        #now we have the real list of encodings we can use:
        #"rgb32" and "rgb24" encodings are both aliased to "rgb"
        common_encodings = [x for x in self._encoders.keys() if x in self.core_encodings and x not in exclude]
        #"rgb" is a pseudo encoding and needs special code:
        if "rgb24" in  common_encodings or "rgb32" in common_encodings:
            common_encodings.append("rgb")
        self.common_encodings = [x for x in PREFERED_ENCODING_ORDER if x in common_encodings]
        if not self.common_encodings:
            raise Exception("no common encodings found (server: %s vs client: %s)" % (", ".join(self._encoders.keys()), ", ".join(self.core_encodings)))
        #ensure the encoding chosen is supported by this source:
        if encoding in self.common_encodings:
            self.encoding = encoding
        else:
            self.encoding = self.common_encodings[0]
        self.auto_refresh_encodings = [x for x in self.client_refresh_encodings if x in self.common_encodings]
        log("update_encoding_selection(%s) encoding=%s, common encodings=%s, auto_refresh_encodings=%s", encoding, self.encoding, self.common_encodings, self.auto_refresh_encodings)
        assert self.encoding is not None
        self.update_quality()
        self.update_speed()
        self.update_encoding_options()

    def update_encoding_options(self, force_reload=False):
        self._want_alpha = self.is_tray or (self.has_alpha and self.supports_transparency)
        self._lossless_threshold_base = min(95, 75+self._current_speed/5)
        self._lossless_threshold_pixel_boost = 20
        #calculate the threshold for using rgb
        #if speed is high, assume we have bandwidth to spare
        smult = max(0.25, (self._current_speed-50)/5.0)
        qmult = max(0, self._current_quality/20.0)
        self._rgb_auto_threshold = int(MAX_PIXELS_PREFER_RGB * smult * qmult * (1 + int(self.is_OR or self.is_tray or self.is_shadow)*2))
        self.get_best_encoding = self.get_best_encoding_impl()
        log("update_encoding_options(%s) want_alpha=%s, lossless threshold: %s / %s, small_as_rgb=%s, get_best_encoding=%s",
                        force_reload, self._want_alpha, self._lossless_threshold_base, self._lossless_threshold_pixel_boost, self._rgb_auto_threshold, self.get_best_encoding)

    def get_best_encoding_impl(self):
        #choose which method to use for selecting an encoding
        #first the easy ones (when there is no choice):
        if self._mmap and self._mmap_size>0:
            return self.encoding_is_mmap
        elif self.encoding=="png/L":
            #(png/L would look awful if we mixed it with something else)
            return self.get_strict_encoding
        elif self.strict:
            #honour strict flag
            if self.encoding=="rgb":
                #choose between rgb32 and rgb24 already
                #as alpha support does not change without going through this method
                if self._want_alpha and "rgb32" in self.common_encodings:
                    return self.encoding_is_rgb32
                else:
                    assert "rgb24" in self.common_encodings
                    return self.encoding_is_rgb24
            return self.get_strict_encoding
        elif self._want_alpha:
            if self.encoding in ("rgb", "rgb32") and "rgb32" in self.common_encodings:
                return self.encoding_is_rgb32
            if self.encoding in ("png", "webp", "png/P"):
                #chosen encoding does alpha, stick to it:
                #(prevents alpha bleeding artifacts,
                # as different encoders may encode alpha differently)
                return self.get_strict_encoding
            #choose an alpha encoding and keep it?
            return self.get_transparent_encoding
        elif self.encoding=="rgb":
            #if we're here we don't need alpha, so try rgb24 first:
            if "rgb24" in self.common_encodings:
                return self.encoding_is_rgb24
            elif "rgb32" in self.common_encodings:
                return self.encoding_is_rgb32
        return self.get_best_encoding_impl_default()

    def get_best_encoding_impl_default(self):
        #stick to what is specified or use rgb for small regions:
        return self.get_current_or_rgb

    def encoding_is_mmap(self, *args):
        return "mmap"

    def encoding_is_rgb32(self, *args):
        return "rgb32"

    def encoding_is_rgb24(self, *args):
        return "rgb24"

    def get_strict_encoding(self, *args):
        return self.encoding

    def get_transparent_encoding(self, pixel_count, ww, wh, speed, quality, current_encoding):
        #small areas prefer rgb, also when high speed and high quality
        if "rgb32" in self.common_encodings and (pixel_count<self._rgb_auto_threshold or (quality>=90 and speed>=90)):
            return "rgb32"
        #choose webp for limited sizes:
        if "webp" in self.common_encodings:
            max_webp = 1024*1024*(200-quality)/100*speed/100
            if 16384<pixel_count<max_webp:
                return "webp"
        if "png" in self.common_encodings and quality>75:
            return "png"
        for x in ("rgb32", "png", "webp", "rgb32"):
            if x in self.common_encodings:
                return x
        return self.common_encodings[0]

    def get_current_or_rgb(self, pixel_count, ww, wh, *args):
        if pixel_count<self._rgb_auto_threshold:
            return "rgb24"
        return self.encoding


    def unmap(self):
        self.cancel_damage()
        self.statistics.reset()


    def cancel_damage(self):
        """
        Use this method to cancel all currently pending and ongoing
        damage requests for a window.
        Damage methods will check this value via 'is_cancelled(sequence)'.
        """
        log("cancel_damage() wid=%s, dropping delayed region %s, %s queued encodes, and all sequences up to %s", self.wid, self._damage_delayed, len(self.encode_queue), self._sequence)
        #for those in flight, being processed in separate threads, drop by sequence:
        self._damage_cancelled = self._sequence
        self.cancel_expire_timer()
        self.cancel_soft_timer()
        self.cancel_refresh_timer()
        self.cancel_timeout_timer()
        self.cancel_av_sync_timer()
        #if a region was delayed, we can just drop it now:
        self.refresh_regions = []
        eq = self.encode_queue
        if eq:
            self.encode_queue = []
            for item in eq:
                self.free_image_wrapper(item[6])
        self._damage_delayed = None
        self._damage_delayed_expired = False
        self.delta_pixel_data = [None for _ in range(self.delta_buckets)]
        #make sure we don't account for those as they will get dropped
        #(generally before encoding - only one may still get encoded):
        for sequence in self.statistics.encoding_pending.keys():
            if self._damage_cancelled>=sequence:
                try:
                    del self.statistics.encoding_pending[sequence]
                except KeyError:
                    #may have been processed whilst we checked
                    pass

    def cancel_expire_timer(self):
        if self.expire_timer:
            self.source_remove(self.expire_timer)
            self.expire_timer = None

    def cancel_soft_timer(self):
        if self.soft_timer:
            self.source_remove(self.soft_timer)
            self.soft_timer = None

    def cancel_refresh_timer(self):
        if self.refresh_timer:
            self.source_remove(self.refresh_timer)
            self.refresh_timer = None
            self.refresh_event_time = 0
            self.refresh_target_time = 0

    def cancel_timeout_timer(self):
        if self.timeout_timer:
            self.source_remove(self.timeout_timer)
            self.timeout_timer = None

    def cancel_av_sync_timer(self):
        avst = self.av_sync_timer
        if avst:
            self.source_remove(avst)
            self.av_sync_timer = None


    def is_cancelled(self, sequence=None):
        """ See cancel_damage(wid) """
        return self._damage_cancelled>=(sequence or float("inf"))


    def calculate_batch_delay(self, has_focus, other_is_fullscreen, other_is_maximized):
        if not self.batch_config.locked:
            calculate_batch_delay(self.wid, self.window_dimensions, has_focus, other_is_fullscreen, other_is_maximized, self.is_OR, self.soft_expired, self.batch_config, self.global_statistics, self.statistics)

    def update_speed(self):
        if self.suspended or self._mmap:
            return
        speed = self._fixed_speed
        if speed<=0:
            #make a copy to work on (and discard "info")
            speed_data = [(event_time, speed) for event_time, _, speed in list(self._encoding_speed)]
            info, target_speed = get_target_speed(self.wid, self.window_dimensions, self.batch_config, self.global_statistics, self.statistics, self._fixed_min_speed, speed_data)
            speed_data.append((time.time(), target_speed))
            speed = max(self._fixed_min_speed, time_weighted_average(speed_data, min_offset=1, rpow=1.1))
            speed = min(99, speed)
        else:
            info = {}
            speed = min(100, speed)
        self._current_speed = int(speed)
        log("update_speed() info=%s, speed=%s", info, self._current_speed)
        self._encoding_speed.append((time.time(), info, self._current_speed))

    def set_min_speed(self, min_speed):
        if self._fixed_min_speed!=min_speed:
            self._fixed_min_speed = min_speed
            self.reconfigure(True)

    def set_speed(self, speed):
        if self._fixed_speed != speed:
            self._fixed_speed = speed
            self.reconfigure(True)

    def get_speed(self, coding):
        return self._current_speed


    def update_quality(self):
        log("update_quality() suspended=%s, mmap=%s, encoding=%s", self.suspended, self._mmap, self.encoding)
        if self.suspended or self._mmap:
            return
        if self.encoding in ("rgb", "png", "png/P", "png/L"):
            #the user has selected an encoding which does not use quality
            #so skip the calculations!
            self._current_quality = 100
            return
        quality = self._fixed_quality
        if quality<=0:
            info, quality = get_target_quality(self.wid, self.window_dimensions, self.batch_config, self.global_statistics, self.statistics, self._fixed_min_quality)
            #make a copy to work on (and discard "info")
            ves_copy = [(event_time, speed) for event_time, _, speed in list(self._encoding_quality)]
            ves_copy.append((time.time(), quality))
            quality = max(self._fixed_min_quality, time_weighted_average(ves_copy, min_offset=0.1, rpow=1.2))
            quality = min(99, quality)
        else:
            info = {}
            quality = min(100, quality)
        self._current_quality = int(quality)
        log("update_quality() info=%s, quality=%s", info, self._current_quality)
        self._encoding_quality.append((time.time(), info, self._current_quality))

    def set_min_quality(self, min_quality):
        if self._fixed_min_quality!=min_quality:
            self._fixed_min_quality = min_quality
            self.update_quality()
            self.reconfigure(True)

    def set_quality(self, quality):
        if self._fixed_quality!=quality:
            self._fixed_quality = quality
            self._current_quality = quality
            self.reconfigure(True)

    def get_quality(self, encoding):
        #overriden in window video source
        return self._current_quality


    def reconfigure(self, force_reload=False):
        self.update_quality()
        self.update_speed()
        self.update_encoding_options(force_reload)


    def damage(self, window, x, y, w, h, options={}):
        """ decide what to do with the damage area:
            * send it now (if not congested)
            * add it to an existing delayed region
            * create a new delayed region if we find the client needs it
            Also takes care of updating the batch-delay in case of congestion.
            The options dict is currently used for carrying the
            "quality" and "override_options" values, and potentially others.
            When damage requests are delayed and bundled together,
            specify an option of "override_options"=True to
            force the current options to override the old ones,
            otherwise they are only merged.
        """
        if self.suspended:
            return
        if w==0 or h==0:
            #we may fire damage ourselves,
            #in which case the dimensions may be zero (if so configured by the client)
            return
        now = time.time()
        if "auto_refresh" not in options:
            log("damage%s", (window, x, y, w, h, options))
            self.statistics.last_damage_events.append((now, x,y,w,h))
        self.global_statistics.damage_events_count += 1
        self.statistics.damage_events_count += 1
        self.statistics.last_damage_event_time = now
        ww, wh = window.get_dimensions()
        if self.window_dimensions != (ww, wh):
            self.statistics.last_resized = now
            self.window_dimensions = ww, wh
            self.encode_queue_max_size = max(2, min(15, MAX_SYNC_BUFFER_SIZE/(ww*wh*4)))
        if self.full_frames_only:
            x, y, w, h = 0, 0, ww, wh

        delayed = self._damage_delayed
        if delayed:
            #use existing delayed region:
            if not self.full_frames_only:
                regions = delayed[2]
                region = rectangle(x, y, w, h)
                add_rectangle(regions, region)
            #merge/override options
            if options is not None:
                override = options.get("override_options", False)
                existing_options = delayed[4]
                for k in options.keys():
                    if override or k not in existing_options:
                        existing_options[k] = options[k]
            log("damage(%s, %s, %s, %s, %s) wid=%s, using existing delayed %s regions created %.1fms ago",
                x, y, w, h, options, self.wid, delayed[3], now-delayed[0])
            return
        elif self.batch_config.delay <= self.batch_config.min_delay and not self.batch_config.always:
            #work out if we have too many damage requests
            #or too many pixels in those requests
            #for the last time_unit, and if so we force batching on
            event_min_time = now-self.batch_config.time_unit
            all_pixels = [pixels for _,event_time,pixels in self.global_statistics.damage_last_events if event_time>event_min_time]
            eratio = float(len(all_pixels)) / self.batch_config.max_events
            pratio = float(sum(all_pixels)) / self.batch_config.max_pixels
            if eratio>1.0 or pratio>1.0:
                self.batch_config.delay = int(self.batch_config.min_delay * max(eratio, pratio))

        delay = options.get("delay", self.batch_config.delay)
        if now-self.statistics.last_resized<0.250:
            #recently resized, batch more
            delay = max(50, delay+25)
        qsize = self.queue_size()
        if qsize>4:
            #the queue is getting big, try to slow down progressively:
            delay = max(10, min(self.batch_config.min_delay, delay)) * (qsize/4.0)
        delay = max(delay, options.get("min_delay", 0))
        delay = min(delay, options.get("max_delay", self.batch_config.max_delay))
        delay = int(delay)
        packets_backlog = self.statistics.get_packets_backlog()
        pixels_encoding_backlog, enc_backlog_count = self.statistics.get_pixels_encoding_backlog()
        #only send without batching when things are going well:
        # - no packets backlog from the client
        # - the amount of pixels waiting to be encoded is less than one full frame refresh
        # - no more than 10 regions waiting to be encoded
        if not self.must_batch(delay) and (packets_backlog==0 and pixels_encoding_backlog<=ww*wh and enc_backlog_count<=10):
            #send without batching:
            log("damage(%s, %s, %s, %s, %s) wid=%s, sending now with sequence %s", x, y, w, h, options, self.wid, self._sequence)
            actual_encoding = options.get("encoding")
            if actual_encoding is None:
                q = options.get("quality") or self._current_quality
                s = options.get("speed") or self._current_speed
                actual_encoding = self.get_best_encoding(w*h, ww, wh, s, q, self.encoding)
            if self.must_encode_full_frame(window, actual_encoding):
                x, y = 0, 0
                w, h = ww, wh
            self.batch_config.last_delays.append((now, delay))
            self.batch_config.last_actual_delays.append((now, delay))
            def damage_now():
                if self.is_cancelled():
                    return
                window.acknowledge_changes()
                self.process_damage_region(now, window, x, y, w, h, actual_encoding, options)
            self.idle_add(damage_now)
            return

        #create a new delayed region:
        regions = [rectangle(x, y, w, h)]
        self._damage_delayed_expired = False
        actual_encoding = options.get("encoding", self.encoding)
        self._damage_delayed = now, window, regions, actual_encoding, options or {}
        log("damage(%s, %s, %s, %s, %s) wid=%s, scheduling batching expiry for sequence %s in %.1f ms", x, y, w, h, options, self.wid, self._sequence, delay)
        self.batch_config.last_delays.append((now, delay))
        self.expire_timer = self.timeout_add(delay, self.expire_delayed_region, delay)

    def must_batch(self, delay):
        if FORCE_BATCH or self.batch_config.always or delay>self.batch_config.min_delay:
            return True
        try:
            t, _ = self.batch_config.last_delays[-5]
            #do batch if we got more than 5 damage events in the last 10 milliseconds:
            return time.time()-t<0.010
        except:
            #probably not enough events to grab -10
            return False


    def expire_delayed_region(self, delay):
        """ mark the region as expired so damage_packet_acked can send it later,
            and try to send it now.
        """
        self.expire_timer = None
        self._damage_delayed_expired = True
        self.may_send_delayed()
        delayed = self._damage_delayed
        if delayed is None:
            #region has been sent
            return
        #the region has not been sent yet because we are waiting for damage ACKs from the client
        if self.soft_expired<self.max_soft_expired:
            #there aren't too many regions soft expired yet
            #so use the "soft timer":
            self.soft_expired += 1
            #we have already waited for "delay" to get here, wait more as we soft expire more regions:
            self.soft_timer = self.timeout_add(int(self.soft_expired*delay), self.delayed_region_soft_timeout)
        else:
            #NOTE: this should never happen...
            #the region should now get sent when we eventually receive the pending ACKs
            #but if somehow they go missing... clean it up from a timeout:
            delayed_region_time = delayed[0]
            self.timeout_timer = self.timeout_add(self.batch_config.timeout_delay, self.delayed_region_timeout, delayed_region_time)

    def delayed_region_soft_timeout(self):
        self.soft_timer = None
        self.do_send_delayed()
        return False

    def delayed_region_timeout(self, delayed_region_time):
        self.timeout_timer = None
        delayed = self._damage_delayed
        if delayed is None:
            #delayed region got sent
            return False
        region_time = delayed[0]
        if region_time!=delayed_region_time:
            #this is a different region
            return False
        #ouch: same region!
        window      = delayed[1]
        options     = delayed[4]
        elapsed = int(1000.0 * (time.time() - region_time))
        log.warn("Warning: delayed region timeout")
        log.warn(" region is %i seconds old, will retry - bad connection?", elapsed/1000)
        #re-try: cancel anything pending and do a full quality refresh
        self.cancel_damage()
        self.cancel_expire_timer()
        self.cancel_refresh_timer()
        self.cancel_soft_timer()
        self._damage_delayed = None
        self.full_quality_refresh(window, options)
        return False

    def may_send_delayed(self):
        """ send the delayed region for processing if the time is right """
        dd = self._damage_delayed
        if not dd:
            log("window %s delayed region already sent", self.wid)
            return
        damage_time = dd[0]
        packets_backlog = self.statistics.get_packets_backlog()
        now = time.time()
        actual_delay = int(1000.0 * (now-damage_time))
        if packets_backlog>0:
            if actual_delay>self.batch_config.timeout_delay:
                log.warn("send_delayed for wid %s, elapsed time %ims is above limit of %.1f", self.wid, actual_delay, self.batch_config.max_delay)
                return
            log("send_delayed for wid %s, delaying again because of backlog: %s packets, batch delay is %i, elapsed time is %ims",
                    self.wid, packets_backlog, self.batch_config.delay, actual_delay)
            #this method will fire again from damage_packet_acked
            return
        #if we're here, there is no packet backlog, but there may be damage acks pending.
        #if there are acks pending, may_send_delayed() should be called again from damage_packet_acked,
        #if not, we must either process the region now or set a timer to check again later
        def check_again(delay=actual_delay/10.0):
            #schedules a call to check again:
            delay = int(min(self.batch_config.max_delay, max(10, delay)))
            self.timeout_add(delay, self.may_send_delayed)
            return
        #locked means a fixed delay we try to honour,
        #this code ensures that we don't fire too early if called from damage_packet_acked
        if self.batch_config.locked:
            if self.batch_config.delay>actual_delay:
                #ensure we honour the fixed delay
                #(as we may get called from a damage ack before we expire)
                check_again(self.batch_config.delay-actual_delay)
            else:
                self.do_send_delayed()
            return
        pixels_encoding_backlog, enc_backlog_count = self.statistics.get_pixels_encoding_backlog()
        ww, wh = self.window_dimensions
        if pixels_encoding_backlog>=(ww*wh):
            log("send_delayed for wid %s, delaying again because too many pixels are waiting to be encoded: %s", self.wid, ww*wh)
            if self.statistics.get_acks_pending()==0:
                check_again()
            return
        elif enc_backlog_count>10:
            log("send_delayed for wid %s, delaying again because too many damage regions are waiting to be encoded: %s", self.wid, enc_backlog_count)
            if self.statistics.get_acks_pending()==0:
                check_again()
            return
        #no backlog, so ok to send, clear soft-expired counter:
        self.soft_expired = 0
        log("send_delayed for wid %s, batch delay is %i, elapsed time is %i ms", self.wid, self.batch_config.delay, actual_delay)
        self.do_send_delayed()

    def do_send_delayed(self):
        self.cancel_timeout_timer()
        self.cancel_soft_timer()
        delayed = self._damage_delayed
        if delayed:
            self._damage_delayed = None
            damage_time = delayed[0]
            now = time.time()
            actual_delay = int(1000.0 * (now-damage_time))
            self.batch_config.last_actual_delays.append((now, actual_delay))
            self.send_delayed_regions(*delayed)
        return False

    def send_delayed_regions(self, damage_time, window, regions, coding, options):
        """ Called by 'send_delayed' when we expire a delayed region,
            There may be many rectangles within this delayed region,
            so figure out if we want to send them all or if we
            just send one full window update instead.
        """
        # It's important to acknowledge changes *before* we extract them,
        # to avoid a race condition.
        if not window.is_managed():
            return
        window.acknowledge_changes()
        if not self.is_cancelled():
            self.do_send_delayed_regions(damage_time, window, regions, coding, options)

    def do_send_delayed_regions(self, damage_time, window, regions, coding, options, exclude_region=None, get_best_encoding=None):
        ww,wh = window.get_dimensions()
        speed = options.get("speed") or self._current_speed
        quality = options.get("quality") or self._current_quality
        get_best_encoding = get_best_encoding or self.get_best_encoding
        def get_encoding(pixel_count):
            return get_best_encoding(pixel_count, ww, wh, speed, quality, coding)

        def send_full_window_update():
            actual_encoding = get_encoding(ww*wh)
            log("send_delayed_regions: using full window update %sx%s with %s", ww, wh, actual_encoding)
            assert actual_encoding is not None
            self.process_damage_region(damage_time, window, 0, 0, ww, wh, actual_encoding, options)

        if exclude_region is None:
            if self.full_frames_only:
                send_full_window_update()
                return

            if len(regions)>self.max_small_regions:
                #too many regions!
                send_full_window_update()
                return
            if ww*wh<=MIN_WINDOW_REGION_SIZE:
                #size is too small to bother with regions:
                send_full_window_update()
                return

        regions = list(set(regions))
        if MERGE_REGIONS:
            bytes_threshold = ww*wh*self.max_bytes_percent/100
            pixel_count = sum(rect.width*rect.height for rect in regions)
            bytes_cost = pixel_count+self.small_packet_cost*len(regions)
            log("send_delayed_regions: bytes_cost=%s, bytes_threshold=%s, pixel_count=%s", bytes_cost, bytes_threshold, pixel_count)
            if bytes_cost>=bytes_threshold:
                #too many bytes to send lots of small regions..
                if exclude_region is None:
                    send_full_window_update()
                    return
                #make regions out of the rest of the window area:
                non_exclude = rectangle(0, 0, ww, wh).substract_rect(exclude_region)
                #and keep those that have damage areas in them:
                regions = [x for x in non_exclude if len([y for y in regions if x.intersects_rect(y)])>0]
                #TODO: should verify that is still better than what we had before..

            elif len(regions)>1:
                #try to merge all the regions to see if we save anything:
                merged = merge_all(regions)
                #remove the exclude region if needed:
                if exclude_region:
                    merged_rects = merged.substract_rect(exclude_region)
                else:
                    merged_rects = [merged]
                merged_pixel_count = sum(r.width*r.height for r in merged_rects)
                merged_bytes_cost = merged_pixel_count+self.small_packet_cost*len(merged_rects)
                log("send_delayed_regions: merged=%s, merged_bytes_cost=%s, bytes_cost=%s, merged_pixel_count=%s, pixel_count=%s",
                         merged_rects, merged_bytes_cost, bytes_cost, merged_pixel_count, pixel_count)
                if merged_bytes_cost<bytes_cost or merged_pixel_count<pixel_count:
                    #better, so replace with merged regions:
                    regions = merged_rects

            #check to see if the total amount of pixels makes us use a fullscreen update instead:
            if len(regions)>1 and exclude_region is None:
                pixel_count = sum(rect.width*rect.height for rect in regions)
                actual_encoding = get_encoding(pixel_count)
                log("send_delayed_regions: %s regions with %s pixels (encoding=%s, actual=%s)", len(regions), pixel_count, coding, actual_encoding)
                if pixel_count>=ww*wh or self.must_encode_full_frame(window, actual_encoding):
                    #use full screen dimensions:
                    self.process_damage_region(damage_time, window, 0, 0, ww, wh, actual_encoding, options)
                    return

        #we're processing a number of regions separately,
        #start by figuring out which encoding will get used, and shortcut out if this needs to be a full window update
        i_reg_enc = []
        for i,region in enumerate(regions):
            actual_encoding = get_encoding(region.width*region.height)
            if self.must_encode_full_frame(window, actual_encoding):
                self.process_damage_region(damage_time, window, 0, 0, ww, wh, actual_encoding, options)
                #we can stop here (full screen update will include the other regions)
                return
            i_reg_enc.append((i, region, actual_encoding))

        #reversed so that i=0 is last for flushing
        for i, region, actual_encoding in reversed(i_reg_enc):
            self.process_damage_region(damage_time, window, region.x, region.y, region.width, region.height, actual_encoding, options, flush=i)


    def must_encode_full_frame(self, window, encoding):
        #WindowVideoSource overrides this method
        return self.full_frames_only


    def free_image_wrapper(self, image):
        """ when not running in the UI thread,
            call this method to free an image wrapper safely
        """
        log("free_image_wrapper(%s) thread_safe=%s", image, image.is_thread_safe())
        if image.is_thread_safe():
            image.free()
        else:
            self.idle_add(image.free)


    def process_damage_region(self, damage_time, window, x, y, w, h, coding, options, flush=None):
        """
            Called by 'damage' or 'send_delayed_regions' to process a damage region.

            Actual damage region processing:
            we extract the rgb data from the pixmap and:
            * if doing av-sync, we place the data on the encode queue with a timer,
              when the timer fires, we queue the work for the damage thread
            * without av-sync, we just queue the work immediately
            The damage thread will call make_data_packet_cb which does the actual compression.
            This runs in the UI thread.
        """
        if w==0 or h==0:
            return
        if not window.is_managed():
            log("the window %s is not composited!?", window)
            return
        self._sequence += 1
        sequence = self._sequence
        if self.is_cancelled(sequence):
            log("get_window_pixmap: dropping damage request with sequence=%s", sequence)
            return

        assert coding is not None
        rgb_request_time = time.time()
        image = window.get_image(x, y, w, h, logger=log)
        if image is None:
            log("get_window_pixmap: no pixel data for window %s, wid=%s", window, self.wid)
            return
        if self.is_cancelled(sequence):
            image.free()
            return
        self.pixel_format = image.get_pixel_format()

        now = time.time()
        log("process_damage_regions: wid=%i, adding pixel data to encode queue (%ix%i - %s), elapsed time: %.1f ms, request time: %.1f ms",
                self.wid, w, h, coding, 1000*(now-damage_time), 1000*(now-rgb_request_time))
        item = (window, damage_time, w, h, now, self.wid, image, coding, sequence, options, flush)
        av_sync = options.get("av-sync", False)
        av_delay = self.av_sync_delay*int(av_sync)
        if not av_sync:
            self.call_in_encode_thread(self.make_data_packet_cb, *item)
        else:
            #schedule encode via queue, after freezing the pixels:
            frozen = image.freeze()
            self.encode_queue.append(item)
            l = len(self.encode_queue)
            if l>=self.encode_queue_max_size:
                av_delay = 0        #we must free some space!
            avsynclog("scheduling encode queue iteration in %ims, pixels frozen=%s, encode queue size=%i (max=%i)", av_delay, frozen, l, self.encode_queue_max_size)
            self.timeout_add(av_delay, self.call_in_encode_thread, self.encode_from_queue)

    def encode_from_queue(self):
        #note: we use a queue here to ensure we preserve the order
        #(so we encode frames in the same order they were grabbed)
        eq = self.encode_queue
        avsynclog("encode_from_queue: %s items", len(eq))
        if not eq:
            return      #nothing to encode, must have been picked off already
        self.update_av_sync_delay()
        #find the first item which is due
        #in seconds, same as time.time():
        av_delay = self.av_sync_delay/1000.0
        if len(self.encode_queue)>=self.encode_queue_max_size:
            av_delay = 0        #we must free some space!
        now = time.time()
        still_due = []
        pop = None
        try:
            for index,item in enumerate(eq):
                sequence = item[8]
                if self.is_cancelled(sequence):
                    self.free_image_wrapper(item[6])
                    continue
                ts = item[4]
                due = ts + av_delay
                if due<now and pop is None:
                    #found an item which is due
                    pop = index
                    avsynclog("encode_from_queue: processing item %s/%s (overdue by %ims)", index+1, len(self.encode_queue), int(1000*(now-due)))
                    self.make_data_packet_cb(*item)
                else:
                    #we only process only one item per call
                    #and just keep track of extra ones:
                    still_due.append(due)
        except Exception as e:
            avsynclog.error("error processing encode queue: %s", e, exc_info=True)
        if pop is not None:
            eq.pop(pop)
            return
        #README: encode_from_queue is scheduled to run every time we add an item
        #to the encode_queue, but since the av_delay can change it is possible
        #for us to not pop() any items from the list sometimes, and therefore we must ensure
        #we run this method again later when the items are actually due,
        #so we need to calculate when that is:
        if len(still_due)==0:
            avsynclog("encode_from_queue: nothing due")
            return
        first_due = int(max(0, min(still_due)-time.time())*1000)
        avsynclog("encode_from_queue: first due in %ims, due list=%s", first_due, still_due)
        self.timeout_add(first_due, self.call_in_encode_thread, self.encode_from_queue)


    def make_data_packet_cb(self, window, w, h, damage_time, process_damage_time, wid, image, coding, sequence, options, flush):
        """ This function is called from the damage data thread!
            Extra care must be taken to prevent access to X11 functions on window.
        """
        self.statistics.encoding_pending[sequence] = (damage_time, w, h)
        try:
            packet = self.make_data_packet(damage_time, process_damage_time, wid, image, coding, sequence, options, flush)
        finally:
            self.free_image_wrapper(image)
            del image
            try:
                del self.statistics.encoding_pending[sequence]
            except KeyError:
                #may have been cancelled whilst we processed it
                pass
        #NOTE: we MUST send it (even if the window is cancelled by now..)
        #because the code may rely on the client having received this frame
        if not packet:
            return
        #queue packet for sending:
        self.queue_damage_packet(packet, damage_time, process_damage_time)

        if not self.can_refresh(window):
            self.cancel_refresh_timer()
            return
        encoding = packet[6]
        #the actual encoding used may be different from the global one we specify
        x, y, w, h = packet[2:6]
        client_options = packet[10]     #info about this packet from the encoder
        actual_quality = client_options.get("quality", 0)
        if encoding.startswith("png") or encoding.startswith("rgb"):
            actual_quality = 100
        #jpeg uses colour subsampling by default, otherwise check the csc format value:
        lossy_csc = encoding=="jpeg" or client_options.get("csc") in LOSSY_PIXEL_FORMATS
        scaled = client_options.get("scaled_size") is not None
        region = rectangle(x, y, w, h)
        if options.get("auto_refresh", False) or (actual_quality>=AUTO_REFRESH_THRESHOLD and not lossy_csc and not scaled):
            #this screen update is lossless or high quality
            if not self.refresh_regions:
                #nothing due for refresh, still nothing to do
                msg = "nothing to do"
            else:
                #refresh already due: substract this region from the list of regions:
                self.remove_refresh_region(region)
                if len(self.refresh_regions)==0:
                    msg = "covered all regions that needed a refresh, cancelling refresh"
                    self.cancel_refresh_timer()
                else:
                    msg = "removed rectangle from regions, keeping refresh"
        else:
            #if we're here: the window is still valid and this was a lossy update,
            #of some form (lossy encoding with low enough quality, or using CSC subsampling, or using scaling)
            #so we probably need an auto-refresh (re-schedule it if one was due already)

            #try to add the rectangle to the refresh list:
            pixels_modified = self.add_refresh_region(window, region)
            #the target time is only set in this function and cleared when the refresh runs,
            #copy it before we modify anything (as the refresh clears it from another thread)
            target_time = self.refresh_target_time
            if pixels_modified==0:
                msg = "keeping existing timer (all pixels outside area)"
                if self.refresh_regions and not target_time:
                    #this should never happen:
                    refreshlog.warn("refresh regions are pending but no refresh timer is due!")
            else:
                #some pixels were modified, and we do need to refresh them:
                now = time.time()
                #figure out the proportion of pixels updated:
                pixels = region.width*region.height
                ww, wh = window.get_dimensions()
                pct = 100*pixels/(ww*wh)
                #try to take into account speed and quality:
                #delay more when quality is low
                #delay less when speed is high
                #(the resulting range is 100*100 to 200*200)
                qsmult = (200-self._current_quality) * (100+self._current_speed)
                #important: must check both, I think:
                if target_time==0 or not self.refresh_timer:
                    #this means we must schedule the refresh
                    self.refresh_event_time = time.time()
                    #delay in milliseconds: always at least the settings,
                    #more if we have more than 50% of the window pixels to update:
                    sched_delay = int(max(50, self.auto_refresh_delay * max(50, pct) / 50, self.batch_config.delay*4) * qsmult / (200*100))
                    self.refresh_target_time = now + sched_delay/1000.0
                    self.refresh_timer = self.timeout_add(int(sched_delay), self.refresh_timer_function, window, options)
                    msg = "scheduling refresh in %sms (pct=%s, batch=%s)" % (sched_delay, pct, self.batch_config.delay)
                else:
                    #add to the target time, but this will not move it forwards for small updates following big ones:
                    sched_delay = int(max(50, self.auto_refresh_delay * pct / 50, self.batch_config.delay*2) * qsmult / (200*100))
                    self.refresh_target_time = max(target_time, now + sched_delay/1000.0)
                    msg = "re-scheduling refresh (due in %ims, %ims added - sched_delay=%s, pct=%s, batch=%s)" % (1000*(self.refresh_target_time-now), 1000*(self.refresh_target_time-target_time), sched_delay, pct, self.batch_config.delay)
        self.last_auto_refresh_message = time.time(), msg
        refreshlog("auto refresh: %5s screen update (quality=%3i), %s (region=%s, refresh regions=%s)", encoding, actual_quality, msg, region, self.refresh_regions)

    def remove_refresh_region(self, region):
        #removes the given region from the refresh list
        #(also overriden in window video source)
        remove_rectangle(self.refresh_regions, region)

    def add_refresh_region(self, window, region):
        #adds the given region to the refresh list
        #returns the number of pixels in the region update
        #(overriden in window video source to exclude the video region)
        #Note: this does not run in the UI thread!
        add_rectangle(self.refresh_regions, region)
        return region.width*region.height

    def can_refresh(self, window):
        #safe to call from any thread (does not call X11):
        if not window.is_managed():
            #window is gone
            return False
        if self.auto_refresh_delay<=0 or self.is_cancelled() or len(self.auto_refresh_encodings)==0 or self._mmap:
            #can happen during cleanup
            return False
        return True

    def refresh_timer_function(self, window, damage_options):
        """ Must be called from the UI thread:
            this makes it easier to prevent races and we're allowed to use the window object.
            And for that reason, it may re-schedule itself safely here too.
            We figure out if now is the right time to do the refresh,
            and if not re-schedule.
        """
        #timer is running now, clear so we don't try to cancel it somewhere else:
        self.refresh_timer = None
        #re-do some checks that may have changed:
        if not self.can_refresh(window):
            self.refresh_event_time = 0
            return
        ret = self.refresh_event_time
        if ret==0:
            return
        delta = self.refresh_target_time - time.time()
        if delta<0.050:
            #this is about right (due already or due shortly)
            self.timer_full_refresh(window)
            return
        #re-schedule ourselves:
        self.refresh_timer = self.timeout_add(int(delta*1000), self.refresh_timer_function, window, damage_options)
        refreshlog("refresh_timer_function: rescheduling auto refresh timer with extra delay %ims", int(1000*delta))

    def timer_full_refresh(self, window):
        #copy event time and list of regions (which may get modified by another thread)
        ret = self.refresh_event_time
        self.refresh_event_time = 0
        regions = self.refresh_regions
        self.refresh_regions = []
        if self.can_refresh(window) and regions and ret>0:
            now = time.time()
            refreshlog("timer_full_refresh() after %ims, regions=%s", 1000.0*(time.time()-ret), regions)
            #choose an encoding:
            ww, wh = window.get_dimensions()
            encoding = self.auto_refresh_encodings[0]
            best_encoding = self.get_best_encoding(ww*wh, ww, wh, AUTO_REFRESH_SPEED, AUTO_REFRESH_QUALITY, encoding)
            refresh_encodings = self.auto_refresh_encodings
            if best_encoding in refresh_encodings:
                encoding = best_encoding
            options = self.get_refresh_options()
            refreshlog("timer_full_refresh() size=%s, encoding=%s, best=%s, auto_refresh_encodings=%s, refresh_encodings=%s, options=%s",
                            (ww, wh), encoding, best_encoding, self.auto_refresh_encodings, refresh_encodings, options)
            WindowSource.do_send_delayed_regions(self, now, window, regions, encoding, options, exclude_region=self.get_refresh_exclude())
        return False

    def get_refresh_exclude(self):
        #overriden in window video source to exclude the video subregion
        return None

    def full_quality_refresh(self, window, damage_options={}):
        #called on use request via xpra control,
        #or when we need to resend the window after a send timeout
        if not window.is_managed():
            #this window is no longer managed
            return
        if not self.auto_refresh_encodings or self.is_cancelled():
            #can happen during cleanup
            return
        refresh_regions = self.refresh_regions
        self.refresh_regions = []
        w, h = window.get_dimensions()
        log("full_quality_refresh() for %sx%s window with regions: %s", w, h, self.refresh_regions)
        new_options = damage_options.copy()
        encoding = self.auto_refresh_encodings[0]
        new_options.update(self.get_refresh_options())
        log("full_quality_refresh() using %s with options=%s", encoding, new_options)
        damage_time = time.time()
        self.send_delayed_regions(damage_time, window, refresh_regions, encoding, new_options)
        self.damage(window, 0, 0, w, h, options=new_options)

    def get_refresh_options(self):
        return {"optimize"      : False,
                "auto_refresh"  : True,     #not strictly an auto-refresh, just makes sure we won't trigger one
                "quality"       : AUTO_REFRESH_QUALITY,
                "speed"         : AUTO_REFRESH_SPEED}

    def queue_damage_packet(self, packet, damage_time, process_damage_time):
        """
            Adds the given packet to the packet_queue,
            (warning: this runs from the non-UI 'encode' thread)
            we also record a number of statistics:
            - damage packet queue size
            - number of pixels in damage packet queue
            - damage latency (via a callback once the packet is actually sent)
        """
        #packet = ["draw", wid, x, y, w, h, coding, data, self._damage_packet_sequence, rowstride, client_options]
        width = packet[4]
        height = packet[5]
        damage_packet_sequence = packet[8]
        actual_batch_delay = process_damage_time-damage_time
        def start_send(bytecount):
            now = time.time()
            self.statistics.damage_ack_pending[damage_packet_sequence] = [now, bytecount, 0, 0, width*height]
        def damage_packet_sent(bytecount):
            now = time.time()
            stats = self.statistics.damage_ack_pending.get(damage_packet_sequence)
            #if we timed it out, it may be gone already:
            if stats:
                start_send_time = stats[0]
                start_bytecount = stats[1]
                stats[2] = now
                stats[3] = bytecount
                damage_out_latency = now-process_damage_time
                self.statistics.damage_out_latency.append((now, width*height, actual_batch_delay, damage_out_latency))
                self.statistics.damage_send_speed.append((now, bytecount-start_bytecount, now-start_send_time))
        now = time.time()
        damage_in_latency = now-process_damage_time
        self.statistics.damage_in_latency.append((now, width*height, actual_batch_delay, damage_in_latency))
        self.queue_packet(packet, self.wid, width*height, start_send, damage_packet_sent)

    def damage_packet_acked(self, window, damage_packet_sequence, width, height, decode_time, message):
        """
            The client is acknowledging a damage packet,
            we record the 'client decode time' (provided by the client itself)
            and the "client latency".
            If we were waiting for pending ACKs to send an expired damage packet,
            check for it.
            (warning: this runs from the non-UI network parse thread,
            don't access the window from here!)
        """
        log("packet decoding sequence %s for window %s: %sx%s took %.1fms", damage_packet_sequence, self.wid, width, height, decode_time/1000.0)
        if decode_time>0:
            self.statistics.client_decode_time.append((time.time(), width*height, decode_time))
        elif decode_time<0:
            self.client_decode_error(window, decode_time, message)
        pending = self.statistics.damage_ack_pending.get(damage_packet_sequence)
        if pending is None:
            log("cannot find sent time for sequence %s", damage_packet_sequence)
            return
        del self.statistics.damage_ack_pending[damage_packet_sequence]
        if decode_time>0:
            start_send_at, start_bytes, end_send_at, end_bytes, pixels = pending
            bytecount = end_bytes-start_bytes
            #it is possible, though very unlikely,
            #that we get the ack before we've had a chance to call
            #damage_packet_sent, so we must validate the data:
            if bytecount>0 and end_send_at>0:
                self.global_statistics.record_latency(self.wid, decode_time, start_send_at, end_send_at, pixels, bytecount)
        if self._damage_delayed is not None and self._damage_delayed_expired:
            self.idle_add(self.may_send_delayed)
        if not self._damage_delayed:
            self.soft_expired = 0

    def client_decode_error(self, window, error, message):
        #don't print error code -1, which is just a generic code for error
        emsg = {-1 : ""}.get(error, error)
        if emsg:
            emsg = " %s" % emsg
        log.warn("Warning: client decoding error: %s%s", message, emsg)
        self.global_statistics.decode_errors += 1
        #something failed client-side, so we can't rely on the delta being available
        self.delta_pixel_data = [None for _ in range(self.delta_buckets)]
        if window:
            self.timeout_add(250, self.full_quality_refresh, window)


    def make_data_packet(self, damage_time, process_damage_time, wid, image, coding, sequence, options, flush):
        """
            Picture encoding - non-UI thread.
            Converts a damage item picked from the 'compression_work_queue'
            by the 'encode' thread and returns a packet
            ready for sending by the network layer.

            * 'mmap' will use 'mmap_encode'
            * 'jpeg' and 'png' are handled by 'pillow_encode'
            * 'webp' uses 'webp_encode'
            * 'h264', 'h265', 'vp8' and 'vp9' use 'video_encode'
            * 'rgb24' and 'rgb32' use 'rgb_encode'
        """
        if self.is_cancelled(sequence) or self.suspended:
            log("make_data_packet: dropping data packet for window %s with sequence=%s", wid, sequence)
            return  None
        x, y, w, h, _ = image.get_geometry()
        assert w>0 and h>0, "invalid dimensions: %sx%s" % (w, h)

        #more useful is the actual number of bytes (assuming 32bpp)
        #since we generally don't send the padding with it:
        isize = w*h
        psize = isize*4
        log("make_data_packet: image=%s, damage data: %s", image, (wid, x, y, w, h, coding))
        start = time.time()
        delta, store, bucket, hits = -1, -1, -1, 0
        pixel_format = image.get_pixel_format()
        #use delta pre-compression for this encoding if:
        #* client must support delta (at least one bucket)
        #* encoding must be one that supports delta (usually rgb24/rgb32 or png)
        #* size is worth xoring (too small is pointless, too big is too expensive)
        #* the pixel format is supported by the client
        # (if we have to rgb_reformat the buffer, it really complicates things)
        if self.delta_buckets>0 and (coding in self.supports_delta) and self.min_delta_size<isize<self.max_delta_size and \
            pixel_format in self.rgb_formats:
            #this may save space (and lower the cost of xoring):
            image.restride()
            #we need to copy the pixels because some encodings
            #may modify the pixel array in-place!
            dpixels = image.get_pixels()
            assert dpixels, "failed to get pixels from %s" % image
            dpixels = memoryview_to_bytes(dpixels)
            dlen = len(dpixels)
            store = sequence
            deltalog("delta available for %s and %i %s pixels on wid=%i", coding, isize, pixel_format, wid)
            for i, dr in enumerate(list(self.delta_pixel_data)):
                if dr is None:
                    continue
                lw, lh, lpixel_format, lcoding, lsequence, buflen, ldata, hits, _ = dr
                if lw==w and lh==h and lpixel_format==pixel_format and lcoding==coding and buflen==dlen:
                    bucket = i
                    if MAX_DELTA_HITS>0 and hits<MAX_DELTA_HITS:
                        deltalog("delta: using matching bucket %s: %sx%s (%s, %i bytes, sequence=%i, hit count=%s)", i, lw, lh, lpixel_format, dlen, lsequence, hits)
                        #xor with this matching delta bucket:
                        delta = lsequence
                        xored = xor_str(dpixels, ldata)
                        image.set_pixels(xored)
                        dr[-1] = time.time()            #update last used time
                        hits += 1
                        dr[-2] = hits               #update hit count
                    else:
                        deltalog("delta: too many hits for bucket %s: %s, clearing it", bucket, hits)
                        hits = 0
                        self.delta_pixel_data[i] = None
                        delta = -1
                    break

        #by default, don't set rowstride (the container format will take care of providing it):
        encoder = self._encoders.get(coding)
        if encoder is None:
            if self.is_cancelled(sequence):
                return None
            else:
                raise Exception("BUG: no encoder not found for %s" % coding)
        ret = encoder(coding, image, options)
        if ret is None:
            log("%s%s returned None", encoder, (coding, image, options))
            #something went wrong.. nothing we can do about it here!
            return  None

        coding, data, client_options, outw, outh, outstride, bpp = ret
        #check cancellation list again since the code above may take some time:
        #but always send mmap data so we can reclaim the space!
        if coding!="mmap" and (self.is_cancelled(sequence) or self.suspended):
            log("make_data_packet: dropping data packet for window %s with sequence=%s", wid, sequence)
            return  None
        #tell client about delta/store for this pixmap:
        if delta>=0:
            client_options["delta"] = delta
            client_options["bucket"] = bucket
        csize = len(data)
        if store>0:
            if delta>0 and csize>=psize*40//100:
                #compressed size is more than 40% of the original
                #maybe delta is not helping us, so clear it:
                self.delta_pixel_data[bucket] = None
                deltalog("delta: clearing bucket %i (compressed size=%s, original size=%s)", bucket, csize, psize)
                #TODO: could tell the clients they can clear it too
                #(add a new client capability and send it a zero store value)
            else:
                #find the bucket to use:
                if bucket<0:
                    lpd = self.delta_pixel_data
                    try:
                        bucket = lpd.index(None)
                        deltalog("delta: found empty bucket %i", bucket)
                    except ValueError:
                        #find a bucket which has not been used recently
                        t = 0
                        bucket = 0
                        for i,dr in enumerate(lpd):
                            if dr and (t==0 or dr[-1]<t):
                                t = dr[-1]
                                bucket = i
                        deltalog("delta: using oldest bucket %i", bucket)
                self.delta_pixel_data[bucket] = [w, h, pixel_format, coding, store, len(dpixels), dpixels, hits, time.time()]
                client_options["store"] = store
                client_options["bucket"] = bucket
                #record number of frames and pixels:
                totals = self.statistics.encoding_totals.setdefault("delta", [0, 0])
                totals[0] = totals[0] + 1
                totals[1] = totals[1] + w*h
                deltalog("delta: client options=%s (for region %s)", client_options, (x, y, w, h))
        if INTEGRITY_HASH and coding!="mmap":
            #could be a compressed wrapper or just raw bytes:
            try:
                v = data.data
            except:
                v = data
            md5 = hashlib.md5(v).hexdigest()
            client_options["z.md5"] = md5
            client_options["z.len"] = len(data)
            log("added len and hash of compressed data integrity %19s: %8i / %s", type(v), len(v), md5)
        #actual network packet:
        if self.supports_flush and flush is not None:
            client_options["flush"] = flush
        packet = ("draw", wid, x, y, outw, outh, coding, data, self._damage_packet_sequence, outstride, client_options)
        end = time.time()
        compresslog("compress: %5.1fms for %4ix%-4i pixels for wid=%-5i using %5s with ratio %5.1f%% (%5iKB to %5iKB), client_options=%s",
                 (end-start)*1000.0, w, h, wid, coding, 100.0*csize/psize, psize/1024, csize/1024, client_options)
        self.global_statistics.packet_count += 1
        self.statistics.packet_count += 1
        self._damage_packet_sequence += 1
        self.statistics.encoding_stats.append((coding, w*h, bpp, len(data), end-start))
        #record number of frames and pixels:
        totals = self.statistics.encoding_totals.setdefault(coding, [0, 0])
        totals[0] = totals[0] + 1
        totals[1] = totals[1] + w*h
        self.encoding_last_used = coding
        #log("make_data_packet: returning packet=%s", packet[:7]+[".."]+packet[8:])
        return packet


    def webp_encode(self, coding, image, options):
        q = options.get("quality") or self.get_quality(coding)
        s = options.get("speed") or self.get_speed(coding)
        return webp_encode(coding, image, self.rgb_formats, self.supports_transparency, q, s, options)

    def rgb_encode(self, coding, image, options):
        s = options.get("speed") or self._current_speed
        return rgb_encode(coding, image, self.rgb_formats, self.supports_transparency, s,
                          self.rgb_zlib, self.rgb_lz4, self.rgb_lzo)

    def pillow_encode(self, coding, image, options):
        #for more information on pixel formats supported by PIL / Pillow, see:
        #https://github.com/python-imaging/Pillow/blob/master/libImaging/Unpack.c
        assert coding in self.server_core_encodings
        q = options.get("quality") or self.get_quality(coding)
        s = options.get("speed") or self.get_speed(coding)
        enc_pillow = get_codec("enc_pillow")
        assert enc_pillow
        return enc_pillow.encode(coding, image, q, s, self.supports_transparency)

    def mmap_encode(self, coding, image, options):
        assert self._mmap and self._mmap_size>0
        v = mmap_send(self._mmap, self._mmap_size, image, self.rgb_formats, self.supports_transparency)
        if v is None:
            return None
        mmap_info, mmap_free_size, written = v
        self.global_statistics.mmap_bytes_sent += written
        self.global_statistics.mmap_free_size = mmap_free_size
        #the data we send is the index within the mmap area:
        return "mmap", mmap_info, {"rgb_format" : image.get_pixel_format()}, image.get_width(), image.get_height(), image.get_rowstride(), 32
