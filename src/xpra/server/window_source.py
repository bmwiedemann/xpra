# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2013 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import time
import os

from xpra.log import Logger, debug_if_env
log = Logger()
elog = debug_if_env(log, "XPRA_ENCODING_DEBUG")

AUTO_REFRESH_ENCODING = os.environ.get("XPRA_AUTO_REFRESH_ENCODING", "")
AUTO_REFRESH_THRESHOLD = int(os.environ.get("XPRA_AUTO_REFRESH_THRESHOLD", 90))
AUTO_REFRESH_QUALITY = int(os.environ.get("XPRA_AUTO_REFRESH_QUALITY", 95))
AUTO_REFRESH_SPEED = int(os.environ.get("XPRA_AUTO_REFRESH_SPEED", 0))

MAX_PIXELS_PREFER_RGB = 4096
AUTO_SWITCH_TO_RGB = False

DELTA = os.environ.get("XPRA_DELTA", "1")=="1"
MAX_DELTA_SIZE = int(os.environ.get("XPRA_MAX_DELTA_SIZE", "10000"))
PIL_CAN_OPTIMIZE = os.environ.get("XPRA_PIL_OPTIMIZE", "1")=="1"
HAS_ALPHA = os.environ.get("XPRA_ALPHA", "1")=="1"

XPRA_DAMAGE_DEBUG = os.environ.get("XPRA_DAMAGE_DEBUG", "0")!="0"
if XPRA_DAMAGE_DEBUG:
    debug = log.info
    rgblog = log.info
else:
    def noop(*args, **kwargs):
        pass
    debug = noop
    rgblog = noop


from xpra.deque import maxdeque
from xpra.net.protocol import compressed_wrapper, Compressed, use_lz4
from xpra.server.window_stats import WindowPerformanceStatistics
from xpra.simple_stats import add_list_stats
from xpra.server.batch_delay_calculator import calculate_batch_delay, get_target_speed, get_target_quality
from xpra.server.stats.maths import time_weighted_average
from xpra.server.region import new_region, add_rectangle, get_rectangles
try:
    from xpra.codecs.xor import xor_str        #@UnresolvedImport
except Exception, e:
    log("cannot load xor module: %s", e)
    xor_str = None
try:
    from xpra.codecs.argb.argb import bgra_to_rgb, bgra_to_rgba, argb_to_rgb, argb_to_rgba   #@UnresolvedImport
except Exception, e:
    log("cannot load argb module: %s", e)
    bgra_to_rgb, bgra_to_rgba, argb_to_rgb, argb_to_rgba = (None,)*4
from xpra.os_util import StringIOClass
from xpra.codecs.loader import get_codec, has_codec, NEW_ENCODING_NAMES_TO_OLD
from xpra.codecs.codec_constants import LOSSY_PIXEL_FORMATS


class WindowSource(object):
    """
    We create a Window Source for each window we send pixels for.

    The UI thread calls 'damage' and we eventually
    call ServerSource.queue_damage to queue the damage compression,

    """

    _encoding_warnings = set()

    def __init__(self, idle_add, timeout_add, source_remove,
                    queue_damage, queue_packet, statistics,
                    wid, window, batch_config, auto_refresh_delay,
                    server_core_encodings, server_encodings,
                    encoding, encodings, core_encodings, encoding_options, rgb_formats,
                    default_encoding_options,
                    mmap, mmap_size):
        #scheduling stuff (gobject wrapped):
        self.idle_add = idle_add
        self.timeout_add = timeout_add
        self.source_remove = source_remove

        self.queue_damage = queue_damage                #callback to add damage data which is ready to compress to the damage processing queue
        self.queue_packet = queue_packet                #callback to add a network packet to the outgoing queue
        self.wid = wid
        self.global_statistics = statistics             #shared/global statistics from ServerSource
        self.statistics = WindowPerformanceStatistics()

        self.server_core_encodings = server_core_encodings
        self.server_encodings = server_encodings
        self.encoding = encoding                        #the current encoding
        self.encodings = encodings                      #all the encodings supported by the client
        self.encoding_last_used = None
        refresh_encodings = [x for x in self.encodings if x in ("png", "rgb", "jpeg")]
        client_refresh_encodings = encoding_options.strlistget("auto_refresh_encodings", refresh_encodings)
        self.auto_refresh_encodings = [x for x in client_refresh_encodings if x in self.encodings and x in self.server_core_encodings]
        self.core_encodings = core_encodings            #the core encodings supported by the client
        self.rgb_formats = rgb_formats                  #supported RGB formats (RGB, RGBA, ...) - used by mmap
        self.encoding_options = encoding_options        #extra options which may be specific to the encoder (ie: x264)
        self.default_encoding_options = default_encoding_options    #default encoding options, like "quality", "min-quality", etc
                                                        #may change at runtime (ie: see ServerSource.set_quality)
        self.encoding_client_options = encoding_options.boolget("client_options")
                                                        #does the client support encoding options?
        self.supports_rgb24zlib = encoding_options.boolget("rgb24zlib")
                                                        #supports rgb (both rgb24 and rgb32..) compression outside network layer (unwrapped)
        self.rgb_zlib = encoding_options.boolget("rgb_zlib", True)  #client supports zlib pixel compression (not to be confused with 'rgb24zlib'...)
        self.rgb_lz4 = encoding_options.boolget("rgb_lz4", False)   #client supports lz4 pixel compression
        self.generic_encodings = encoding_options.boolget("generic")
        self.supports_transparency = HAS_ALPHA and encoding_options.boolget("transparency")
        self.full_frames_only = encoding_options.boolget("full_frames_only")
        self.supports_delta = []
        if xor_str is not None and not window.is_tray():
            self.supports_delta = [x for x in encoding_options.strlistget("supports_delta", []) if x in ("png", "rgb24", "rgb32")]
        self.last_pixmap_data = None
        self.batch_config = batch_config
        self.suspended = False
        #auto-refresh:
        self.auto_refresh_delay = auto_refresh_delay
        self.refresh_timer = None
        self.timeout_timer = None
        self.expire_timer = None

        self.is_OR = window.get_property("override-redirect")
        self.window_dimensions = 0, 0
        self.fullscreen = window.get_property("fullscreen")
        self.scaling = window.get_property("scaling")
        self.maximized = False          #set by the client!
        window.connect("notify::scaling", self._scaling_changed)
        window.connect("notify::fullscreen", self._fullscreen_changed)

        # mmap:
        self._mmap = mmap
        self._mmap_size = mmap_size

        # general encoding tunables (mostly used by video encoders):
        self._encoding_quality = maxdeque(100)   #keep track of the target encoding_quality: (event time, info, encoding speed)
        self._encoding_speed = maxdeque(100)     #keep track of the target encoding_speed: (event time, info, encoding speed)

        # for managing/cancelling damage requests:
        self._damage_delayed = None                     #may store a delayed region when batching in progress
        self._damage_delayed_expired = False            #when this is True, the region should have expired
                                                        #but it is now waiting for the backlog to clear
        self._sequence = 1                              #increase with every region we process or delay
        self._last_sequence_queued = 0                  #the latest sequence we queued for sending (after encoding it)
        self._damage_cancelled = 0                      #stores the highest _sequence cancelled
        self._damage_packet_sequence = 1                #increase with every damage packet created

        self._encoders = {
                          "rgb24"   : self.rgb_encode,
                          "rgb32"   : self.rgb_encode,
                          }
        for x in ("png", "png/P", "png/L", "jpeg"):
            if x in self.server_core_encodings:
                self._encoders[x] = self.PIL_encode
        if "webp" in self.server_core_encodings:
            self._encoders["webp"] = self.webp_encode
        if self._mmap and self._mmap_size>0:
            self._encoders["mmap"] = self.mmap_encode

    def cleanup(self):
        self.cancel_damage()
        self._damage_cancelled = float("inf")
        self.statistics.reset()
        debug("encoding_totals for wid=%s with primary encoding=%s : %s", self.wid, self.encoding, self.statistics.encoding_totals)

    def suspend(self):
        self.cancel_damage()
        self.statistics.reset()
        self.suspended = True

    def resume(self, window):
        self.cancel_damage()
        self.statistics.reset()
        self.suspended = False
        w, h = window.get_dimensions()
        self.damage(window, 0, 0, w, h, {"quality" : 100})


    def set_new_encoding(self, encoding):
        """ Changes the encoder for the given 'window_ids',
            or for all windows if 'window_ids' is None.
        """
        if self.encoding==encoding:
            return
        self.statistics.reset()
        self.last_pixmap_data = None
        self.encoding = encoding

    def _scaling_changed(self, window, *args):
        self.scaling = window.get_property("scaling")
        debug("window recommended scaling changed: %s", self.scaling)
        self.reconfigure(False)

    def set_scaling(self, scaling):
        self.scaling = scaling
        self.reconfigure(True)

    def _fullscreen_changed(self, window, *args):
        self.fullscreen = window.get_property("fullscreen")
        debug("window fullscreen state changed: %s", self.fullscreen)
        self.reconfigure(False)

    def set_client_properties(self, properties):
        debug("set_client_properties(%s)", properties)
        self.maximized = properties.get("maximized", False)
        self.full_frames_only = properties.get("encoding.full_frames_only", self.full_frames_only)
        self.supports_transparency = HAS_ALPHA and properties.get("encoding.transparency", self.supports_transparency)
        self.encodings = properties.get("encodings", self.encodings)
        self.core_encodings = properties.get("encodings.core", self.core_encodings)
        #unless the client tells us it does support alpha, assume it does not:
        self.rgb_formats = properties.get("encodings.rgb_formats", [x for x in self.rgb_formats if x.find("A")<0])
        debug("set_client_properties: window rgb_formats=%s", self.rgb_formats)


    def unmap(self):
        self.cancel_damage()
        self.statistics.reset()


    def cancel_damage(self):
        """
        Use this method to cancel all currently pending and ongoing
        damage requests for a window.
        Damage methods will check this value via 'is_cancelled(sequence)'.
        """
        debug("cancel_damage() wid=%s, dropping delayed region %s and all sequences up to %s", self.wid, self._damage_delayed, self._sequence)
        #for those in flight, being processed in separate threads, drop by sequence:
        self._damage_cancelled = self._sequence
        self.cancel_expire_timer()
        self.cancel_refresh_timer()
        self.cancel_timeout_timer()
        #if a region was delayed, we can just drop it now:
        self._damage_delayed = None
        self._damage_delayed_expired = False
        self.last_pixmap_data = None
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

    def cancel_refresh_timer(self):
        if self.refresh_timer:
            self.source_remove(self.refresh_timer)
            self.refresh_timer = None

    def cancel_timeout_timer(self):
        if self.timeout_timer:
            self.source_remove(self.timeout_timer)
            self.timeout_timer = None


    def is_cancelled(self, sequence):
        """ See cancel_damage(wid) """
        return self._damage_cancelled>=sequence

    def add_stats(self, info, suffix=""):
        """
            Add window specific stats
        """
        prefix = "window[%s]." % self.wid
        #no suffix for metadata (as it is the same for all clients):
        info[prefix+"dimensions"] = self.window_dimensions
        info[prefix+"encoding"+suffix] = self.encoding
        info[prefix+"encoding.mmap"+suffix] = bool(self._mmap) and (self._mmap_size>0)
        if self.encoding_last_used:
            info[prefix+"encoding.last_used"+suffix] = self.encoding_last_used
        info[prefix+"suspended"+suffix] = self.suspended or False
        info[prefix+"property.scaling"+suffix] = self.scaling or (1, 1)
        info[prefix+"property.fullscreen"+suffix] = self.fullscreen or False
        self.statistics.add_stats(info, prefix, suffix)

        #batch delay stats:
        self.batch_config.add_stats(info, "", suffix)

        #speed / quality:
        def add_last_rec_info(prefix, recs):
            #must make a list to work on (again!)
            l = list(recs)
            if len(l)>0:
                _, descr, _ = l[-1]
                for k,v in descr.items():
                    info[prefix+"."+k] = v
        quality_list = [x for _, _, x in list(self._encoding_quality)]
        if len(quality_list)>0:
            qp = prefix+"encoding.quality"+suffix
            add_list_stats(info, qp, quality_list, show_percentile=[9])
            add_last_rec_info(qp, self._encoding_quality)
        speed_list = [x for _, _, x in list(self._encoding_speed)]
        if len(speed_list)>0:
            sp = prefix+"encoding.speed"+suffix
            add_list_stats(info, sp, speed_list, show_percentile=[9])
            add_last_rec_info(sp, self._encoding_speed)
        self.batch_config.add_stats(info, prefix, suffix)

    def calculate_batch_delay(self, has_focus):
        calculate_batch_delay(self.wid, self.window_dimensions, has_focus, self.is_OR, self.batch_config, self.global_statistics, self.statistics)

    def update_speed(self):
        if self.suspended:
            return
        speed = self.default_encoding_options.get("speed", -1)
        if speed<0:
            min_speed = self.get_min_speed()
            info, target_speed = get_target_speed(self.wid, self.window_dimensions, self.batch_config, self.global_statistics, self.statistics, min_speed)
            #make a copy to work on (and discard "info")
            ves_copy = [(event_time, speed) for event_time, _, speed in list(self._encoding_speed)]
            ves_copy.append((time.time(), target_speed))
            speed = max(min_speed, time_weighted_average(ves_copy, min_offset=0.1, rpow=1.2))
            speed = min(99, speed)
        else:
            info = {}
            speed = min(100, speed)
        elog("update_speed() info=%s, speed=%s", info, speed)
        self._encoding_speed.append((time.time(), info, speed))

    def get_min_speed(self):
        return self.default_encoding_options.get("min-speed", -1)

    def get_current_speed(self):
        ms = self.get_min_speed()
        s = min(100, self.default_encoding_options.get("speed", -1))
        if s>=0:
            return max(ms, s)
        if len(self._encoding_speed)==0:
            return max(ms, 80)
        return max(ms, self._encoding_speed[-1][-1])

    def update_quality(self):
        if self.suspended:
            return
        quality = self.default_encoding_options.get("quality", -1)
        if quality<0:
            min_quality = self.default_encoding_options.get("min-quality", -1)
            info, target_quality = get_target_quality(self.wid, self.window_dimensions, self.batch_config, self.global_statistics, self.statistics, min_quality)
            #make a copy to work on (and discard "info")
            ves_copy = [(event_time, speed) for event_time, _, speed in list(self._encoding_quality)]
            ves_copy.append((time.time(), target_quality))
            quality = max(min_quality, time_weighted_average(ves_copy, min_offset=0.1, rpow=1.2))
            quality = min(99, quality)
        else:
            info = {}
            quality = min(100, quality)
        elog("update_quality() info=%s, quality=%s", info, quality)
        self._encoding_quality.append((time.time(), info, quality))

    def get_min_quality(self):
        return self.default_encoding_options.get("min-quality", -1)

    def get_current_quality(self):
        mq = self.get_min_quality()
        q = min(100, self.default_encoding_options.get("quality", -1))
        if q>=0:
            return max(mq, q)
        if len(self._encoding_quality)==0:
            return max(mq, 90)
        return max(mq, self._encoding_quality[-1][-1])

    def reconfigure(self, force_reload=False):
        self.update_quality()
        self.update_speed()


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
        self.global_statistics.damage_events_count += 1
        self.statistics.damage_events_count += 1
        self.statistics.last_damage_event_time = now
        ww, wh = window.get_dimensions()
        self.window_dimensions = ww, wh
        if self.full_frames_only:
            x, y, w, h = 0, 0, ww, wh

        if self._damage_delayed:
            #use existing delayed region:
            if not self.full_frames_only:
                region = self._damage_delayed[2]
                add_rectangle(region, x, y, w, h)
            #merge/override options
            if options is not None:
                override = options.get("override_options", False)
                existing_options = self._damage_delayed[4]
                for k,v in options.items():
                    if override or k not in existing_options:
                        existing_options[k] = v
            debug("damage(%s, %s, %s, %s, %s) wid=%s, using existing delayed %s region created %.1fms ago",
                x, y, w, h, options, self.wid, self._damage_delayed[3], now-self._damage_delayed[0])
            return
        elif self.batch_config.delay < self.batch_config.min_delay:
            #work out if we have too many damage requests
            #or too many pixels in those requests
            #for the last time_unit, and if so we force batching on
            event_min_time = now-self.batch_config.time_unit
            all_pixels = [pixels for _,event_time,pixels in self.global_statistics.damage_last_events if event_time>event_min_time]
            eratio = float(len(all_pixels)) / self.batch_config.max_events
            pratio = float(sum(all_pixels)) / self.batch_config.max_pixels
            if eratio>1.0 or pratio>1.0:
                self.batch_config.delay = self.batch_config.min_delay * max(eratio, pratio)

        delay = options.get("delay", self.batch_config.delay)
        delay = max(delay, options.get("min_delay", 0))
        delay = min(delay, options.get("max_delay", self.batch_config.max_delay))
        packets_backlog = self.statistics.get_packets_backlog()
        pixels_encoding_backlog, enc_backlog_count = self.statistics.get_pixels_encoding_backlog()
        #only send without batching when things are going well:
        # - no packets packlog from the client
        # - the amount of pixels waiting to be encoded is less than one full frame refresh
        # - no more than 10 regions waiting to be encoded
        if (packets_backlog==0 and pixels_encoding_backlog<=ww*wh and enc_backlog_count<=10) and \
            not self.batch_config.always and delay<self.batch_config.min_delay:
            #send without batching:
            debug("damage(%s, %s, %s, %s, %s) wid=%s, sending now with sequence %s", x, y, w, h, options, self.wid, self._sequence)
            actual_encoding = options.get("encoding")
            if actual_encoding is None:
                actual_encoding = self.get_best_encoding(False, window, w*h, ww, wh, self.encoding)
            if self.must_encode_full_frame(window, actual_encoding):
                x, y = 0, 0
                w, h = ww, wh
            self.batch_config.last_delays.append((now, delay))
            self.batch_config.last_actual_delays.append((now, delay))
            self.idle_add(self.process_damage_region, now, window, x, y, w, h, actual_encoding, options)
            return

        #create a new delayed region:
        region = new_region()
        add_rectangle(region, x, y, w, h)
        self._damage_delayed_expired = False
        actual_encoding = options.get("encoding", self.encoding)
        self._damage_delayed = now, window, region, actual_encoding, options or {}
        debug("damage(%s, %s, %s, %s, %s) wid=%s, scheduling batching expiry for sequence %s in %.1f ms", x, y, w, h, options, self.wid, self._sequence, delay)
        self.batch_config.last_delays.append((now, delay))
        self.expire_timer = self.timeout_add(int(delay), self.expire_delayed_region)

    def expire_delayed_region(self):
        """ mark the region as expired so damage_packet_acked can send it later,
            and try to send it now.
        """
        self.expire_timer = None
        self._damage_delayed_expired = True
        self.may_send_delayed()
        if self._damage_delayed:
            #NOTE: this should never happen
            #the region has not been sent and it should now get sent
            #when we eventually receive the pending ACKs
            #but if somehow they go missing... try with a timer:
            delayed_region_time = self._damage_delayed[0]
            self.timeout_timer = self.timeout_add(self.batch_config.max_delay, self.delayed_region_timeout, delayed_region_time)

    def delayed_region_timeout(self, delayed_region_time):
        if self._damage_delayed:
            region_time = self._damage_delayed[0]
            if region_time==delayed_region_time:
                #same region!
                log.warn("delayed_region_timeout: sending now - something is wrong!")
                self.do_send_delayed_region()
        return False

    def may_send_delayed(self):
        """ send the delayed region for processing if there is no client backlog """
        if not self._damage_delayed:
            debug("window %s delayed region already sent", self.wid)
            return False
        damage_time = self._damage_delayed[0]
        packets_backlog = self.statistics.get_packets_backlog()
        now = time.time()
        actual_delay = 1000.0*(time.time()-damage_time)
        if packets_backlog>0:
            if actual_delay<self.batch_config.max_delay:
                debug("send_delayed for wid %s, delaying again because of backlog: %s packets, batch delay is %s, elapsed time is %.1f ms",
                        self.wid, packets_backlog, self.batch_config.delay, actual_delay)
                #this method will get fired again damage_packet_acked
                return False
            else:
                log.warn("send_delayed for wid %s, elapsed time %.1f is above limit of %.1f - sending now", self.wid, actual_delay, self.batch_config.max_delay)
        else:
            #if we're here, there is no packet backlog, and therefore
            #may_send_delayed() may not be called again by an ACK packet,
            #so we must either process the region now or set a timer to
            #check again later:
            def check_again():
                delay = int(max(10, actual_delay/10.0))
                self.timeout_add(delay, self.may_send_delayed)
                return False
            pixels_encoding_backlog, enc_backlog_count = self.statistics.get_pixels_encoding_backlog()
            ww, wh = self.window_dimensions
            if pixels_encoding_backlog>=(ww*wh):
                debug("send_delayed for wid %s, delaying again because too many pixels are waiting to be encoded: %s", self.wid, ww*wh)
                return check_again()
            elif enc_backlog_count>10:
                debug("send_delayed for wid %s, delaying again because too many damage regions are waiting to be encoded: %s", self.wid, enc_backlog_count)
                return check_again()
            #no backlog, so ok to send:
            debug("send_delayed for wid %s, batch delay is %.1f, elapsed time is %.1f ms", self.wid, self.batch_config.delay, actual_delay)
        self.batch_config.last_actual_delays.append((now, actual_delay))
        self.do_send_delayed_region()
        return False

    def do_send_delayed_region(self):
        self.cancel_timeout_timer()
        delayed = self._damage_delayed
        self._damage_delayed = None
        self.send_delayed_regions(*delayed)
        return False

    def send_delayed_regions(self, damage_time, window, damage, coding, options):
        """ Called by 'send_delayed' when we expire a delayed region,
            There may be many rectangles within this delayed region,
            so figure out if we want to send them all or if we
            just send one full window update instead.
        """
        regions = []
        ww,wh = window.get_dimensions()
        def send_full_window_update():
            actual_encoding = self.get_best_encoding(True, window, ww*wh, ww, wh, coding)
            debug("send_delayed_regions: using full window update %sx%s with %s", ww, wh, actual_encoding)
            self.process_damage_region(damage_time, window, 0, 0, ww, wh, actual_encoding, options)

        if window.is_tray() or self.full_frames_only:
            send_full_window_update()
            return

        try:
            count_threshold = 60
            pixels_threshold = ww*wh*9/10
            packet_cost = 1024
            if self._mmap and self._mmap_size>0:
                #with mmap, we can move lots of data around easily
                #so favour large screen updates over many small packets
                pixels_threshold = ww*wh/2
                packet_cost = 4096
            pixel_count = 0
            for rect in get_rectangles(damage):
                pixel_count += rect.width*rect.height
                #favor full window  updates over many regions:
                if len(regions)>count_threshold or pixel_count+packet_cost*len(regions)>=pixels_threshold:
                    send_full_window_update()
                    return
                regions.append((rect.x, rect.y, rect.width, rect.height))
            debug("send_delayed_regions: to regions: %s items, %s pixels", len(regions), pixel_count)
        except Exception, e:
            log.error("send_delayed_regions: error processing region %s: %s", damage, e, exc_info=True)
            return

        actual_encoding = self.get_best_encoding(True, window, pixel_count, ww, wh, coding)
        if self.must_encode_full_frame(window, actual_encoding):
            #use full screen dimensions:
            self.process_damage_region(damage_time, window, 0, 0, ww, wh, actual_encoding, options)
            return

        #we're processing a number of regions with a non video encoding:
        for region in regions:
            x, y, w, h = region
            self.process_damage_region(damage_time, window, x, y, w, h, actual_encoding, options)


    def must_encode_full_frame(self, window, encoding):
        if self.full_frames_only:
            return True
        if window.is_tray():
            return True
        #video encoders will override this
        return False

    def get_best_encoding(self, batching, window, pixel_count, ww, wh, current_encoding):
        e = self.do_get_best_encoding(batching, window.has_alpha(), window.is_tray(), window.is_OR(), pixel_count, ww, wh, current_encoding)
        if e is None:
            e = self.get_core_encoding(window.has_alpha(), current_encoding)
        log("get_best_encoding%s=%s", (batching, window, pixel_count, ww, wh, current_encoding), e)
        return e

    def do_get_best_encoding(self, batching, has_alpha, is_tray, is_OR, pixel_count, ww, wh, current_encoding):
        """
            decide which encoding to use: transparent windows and trays need special treatment
            (this is also overriden in WindowVideoSource)
        """
        if has_alpha and self.supports_transparency:
            return self.get_transparent_encoding(current_encoding)
        if is_tray:
            #tray needs a lossless encoder
            coding = self.find_common_lossless_encoder(has_alpha, current_encoding, ww*wh)
            debug("do_get_best_encoding(..) using %s encoder for %s tray pixels", coding, pixel_count)
            return coding
        if AUTO_SWITCH_TO_RGB and not batching and pixel_count<MAX_PIXELS_PREFER_RGB and current_encoding in ("png", "webp"):
            if has_alpha and self.supports_transparency:
                return self.pick_encoding(["rgb32"])
            else:
                return self.pick_encoding(["rgb24"])
        return None

    def get_transparent_encoding(self, current_encoding):
        if current_encoding in ("png", "png/P", "png/L", "rgb32", "webp"):
            return current_encoding
        if current_encoding=="rgb":
            encs = ("rgb32", "png", "webp")
        else:
            encs = ("png", "rgb32", "webp")
        for x in encs:
            if x in self.server_core_encodings and x in self.core_encodings:
                debug("do_get_best_encoding(..) using %s for alpha channel support", x)
                return x
        debug("no alpha channel encodings supported: no %s in %s", encs, [x for x in self.server_core_encodings if x in self.core_encodings])
        return None

    def get_core_encoding(self, has_alpha, current_encoding):
        if current_encoding=="rgb":
            encs = [current_encoding]
            if has_alpha and self.supports_transparency:
                encs.insert(0, "rgb32")
                encs.insert(1, "rgb24")
            else:
                encs.insert(0, "rgb24")
                encs.insert(1, "rgb32")
            log("get_core_encodings(%s, %s) encs=%s, server_core_encodings=%s, core_encodings=%s", has_alpha, current_encoding, encs, self.server_core_encodings, self.core_encodings)
            for e in encs:
                if e in self.server_core_encodings and e in self.core_encodings:
                    return e
        return current_encoding

    def find_common_lossless_encoder(self, has_alpha, fallback, pixel_count):
        if has_alpha and self.supports_transparency:
            rgb_fmt = "rgb32"
        else:
            rgb_fmt = "rgb24"
        if pixel_count<=MAX_PIXELS_PREFER_RGB:
            encs = rgb_fmt, "png", "rgb24"
        else:
            encs = "png", rgb_fmt, "rgb24"
        return self.pick_encoding(encs, fallback)

    def pick_encoding(self, encodings, fallback=None):
        for e in encodings:
            if e in self.server_core_encodings and e in self.core_encodings:
                return e
        return fallback

    def process_damage_region(self, damage_time, window, x, y, w, h, coding, options):
        """
            Called by 'damage' or 'send_delayed_regions' to process a damage region.

            Actual damage region processing:
            we extract the rgb data from the pixmap and place it on the damage queue.
            This runs in the UI thread.
        """
        if w==0 or h==0:
            return
        if not window.is_managed():
            debug("the window %s is not composited!?", window)
            return
        # It's important to acknowledge changes *before* we extract them,
        # to avoid a race condition.
        window.acknowledge_changes()

        sequence = self._sequence + 1
        if self.is_cancelled(sequence):
            debug("get_window_pixmap: dropping damage request with sequence=%s", sequence)
            return
        rgb_request_time = time.time()
        image = window.get_image(x, y, w, h, logger=rgblog)
        if image is None:
            debug("get_window_pixmap: no pixel data for window %s, wid=%s", window, self.wid)
            return
        if self.is_cancelled(sequence):
            image.free()
            return
        process_damage_time = time.time()
        data = (damage_time, process_damage_time, self.wid, image, coding, sequence, options)
        self._sequence += 1
        debug("process_damage_regions: wid=%s, adding pixel data %s to queue, elapsed time: %.1f ms, request rgb time: %.1f ms",
                self.wid, data[:6], 1000.0*(time.time()-damage_time), 1000.0*(time.time()-rgb_request_time))
        def make_data_packet_cb(*args):
            #NOTE: this function is called from the damage data thread!
            try:
                packet = self.make_data_packet(*data)
            finally:
                self.idle_add(image.free)
                try:
                    del self.statistics.encoding_pending[sequence]
                except KeyError:
                    #may have been cancelled whilst we processed it
                    pass
            #NOTE: we have to send it (even if the window is cancelled by now..)
            #because the code may rely on the client having received this frame
            if packet:
                self.queue_damage_packet(packet, damage_time, process_damage_time)
                if self.encoding.startswith("png") or self.encoding.startswith("rgb"):
                    #primary encoding is lossless, no need for auto-refresh
                    return
                #auto-refresh:
                if window.is_managed() and self.auto_refresh_delay>0 and not self.is_cancelled(sequence):
                    client_options = packet[10]     #info about this packet from the encoder
                    self.idle_add(self.schedule_auto_refresh, window, w, h, coding, options, client_options)
        self.statistics.encoding_pending[sequence] = (damage_time, w, h)
        self.queue_damage(make_data_packet_cb)

    def schedule_auto_refresh(self, window, w, h, coding, damage_options, client_options):
        """ Must be called from the UI thread: this makes it easier
            to prevent races, and we can call window.get_dimensions() safely
        """
        #NOTE: there is a small potential race here:
        #if the damage packet queue is congested, new damage requests could come in,
        #in between the time we schedule the new refresh timer and the time it fires,
        #and if not batching,
        #we would then do a full_quality_refresh when we should not...
        actual_quality = client_options.get("quality")
        lossy_csc = client_options.get("csc") in LOSSY_PIXEL_FORMATS
        if actual_quality is None and not lossy_csc:
            debug("schedule_auto_refresh: was a lossless %s packet, ignoring", coding)
            #lossless already: small region sent lossless or encoding is lossless
            #don't change anything: if we have a timer, keep it
            return
        if not window.is_managed():
            return
        if len(self.auto_refresh_encodings)==0:
            return
        ww, wh = window.get_dimensions()
        if client_options.get("scaled_size") is None and not lossy_csc:
            if actual_quality>=AUTO_REFRESH_THRESHOLD:
                if w*h>=ww*wh:
                    debug("schedule_auto_refresh: high quality (%s%%) full frame (%s pixels), cancelling refresh timer %s", actual_quality, w*h, self.refresh_timer)
                    #got enough pixels at high quality, cancel timer:
                    self.cancel_refresh_timer()
                else:
                    debug("schedule_auto_refresh: high quality (%s%%) small area, ignoring", actual_quality)
                return
        def full_quality_refresh():
            debug("full_quality_refresh() for %sx%s window", w, h)
            if self._damage_delayed:
                #there is already a new damage region pending
                return  False
            if not window.is_managed():
                #this window is no longer managed
                return  False
            self.refresh_timer = None
            new_options = damage_options.copy()
            encoding = self.auto_refresh_encodings[0]
            new_options["encoding"] = encoding
            new_options["optimize"] = False
            new_options["quality"] = AUTO_REFRESH_QUALITY
            new_options["speed"] = AUTO_REFRESH_SPEED
            debug("full_quality_refresh() with options=%s", new_options)
            self.damage(window, 0, 0, ww, wh, options=new_options)
            return False
            #self.process_damage_region(time.time(), window, 0, 0, ww, wh, coding, new_options)
        self.cancel_refresh_timer()
        if self._damage_delayed:
            debug("auto refresh: delayed region already exists")
            #there is already a new damage region pending, let it re-schedule when it gets sent
            return
        delay = int(max(50, self.auto_refresh_delay, self.batch_config.delay*4))
        debug("schedule_auto_refresh: low quality (%s%%) with %s pixels, (re)scheduling auto refresh timer with delay %s", actual_quality, w*h, delay)
        self.refresh_timer = self.timeout_add(delay, full_quality_refresh)

    def queue_damage_packet(self, packet, damage_time, process_damage_time):
        """
            Adds the given packet to the damage_packet_queue,
            (warning: this runs from the non-UI thread 'data_to_packet')
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
                stats[2] = now
                stats[3] = bytecount
                damage_out_latency = now-process_damage_time
                self.statistics.damage_out_latency.append((now, width*height, actual_batch_delay, damage_out_latency))
        now = time.time()
        damage_in_latency = now-process_damage_time
        self.statistics.damage_in_latency.append((now, width*height, actual_batch_delay, damage_in_latency))
        self.queue_packet(packet, self.wid, width*height, start_send, damage_packet_sent)

    def damage_packet_acked(self, damage_packet_sequence, width, height, decode_time):
        """
            The client is acknowledging a damage packet,
            we record the 'client decode time' (provided by the client itself)
            and the "client latency".
        """
        debug("packet decoding sequence %s for window %s %sx%s took %.1fms", damage_packet_sequence, self.wid, width, height, decode_time/1000.0)
        if decode_time>0:
            self.statistics.client_decode_time.append((time.time(), width*height, decode_time))
        pending = self.statistics.damage_ack_pending.get(damage_packet_sequence)
        if pending is None:
            debug("cannot find sent time for sequence %s", damage_packet_sequence)
            return
        del self.statistics.damage_ack_pending[damage_packet_sequence]
        if decode_time:
            start_send_at, start_bytes, end_send_at, end_bytes, pixels = pending
            bytecount = end_bytes-start_bytes
            self.global_statistics.record_latency(self.wid, decode_time, start_send_at, end_send_at, pixels, bytecount)
        else:
            #something failed client-side, so we can't rely on the delta being available
            self.last_pixmap_data = None
        if self._damage_delayed is not None and self._damage_delayed_expired:
            self.idle_add(self.may_send_delayed)

    def make_data_packet(self, damage_time, process_damage_time, wid, image, coding, sequence, options):
        """
            Picture encoding - non-UI thread.
            Converts a damage item picked from the 'damage_data_queue'
            by the 'data_to_packet' thread and returns a packet
            ready for sending by the network layer.

            * 'mmap' will use 'mmap_send' + 'mmap_encode' - always if available, otherwise:
            * 'jpeg' and 'png' are handled by 'PIL_encode'.
            * 'webp' uses 'webp_encode'
            * 'h264' and 'vp8' use 'video_encode'
            * 'rgb24' and 'rgb32' use 'rgb_encode'
        """
        if self.is_cancelled(sequence) or self.suspended:
            debug("make_data_packet: dropping data packet for window %s with sequence=%s", wid, sequence)
            return  None
        x, y, w, h, _ = image.get_geometry()

        isize = image.get_size()
        assert w>0 and h>0, "invalid dimensions: %sx%s" % (w, h)
        debug("make_data_packet: image=%s, damage data: %s", image, (wid, x, y, w, h, coding))
        start = time.time()
        if self._mmap and self._mmap_size>0 and isize>256:
            data = self.mmap_send(image)
            if data:
                #hackish: pass data to mmap_encode using "options":
                coding = "mmap"         #changed encoding!
                options["mmap_data"] = data

        #if client supports delta pre-compression for this encoding, use it if we can:
        delta = -1
        store = -1
        if DELTA and coding in self.supports_delta and image.get_size()<MAX_DELTA_SIZE:
            #we need to copy the pixels because some delta encodings
            #will modify the pixel array in-place!
            dpixels = image.get_pixels()[:]
            store = sequence
            if self.last_pixmap_data is not None:
                lw, lh, lcoding, lsequence, ldata = self.last_pixmap_data
                if lw==w and lh==h and lcoding==coding and len(ldata)==len(dpixels):
                    #xor with the last frame:
                    delta = lsequence
                    data = xor_str(dpixels, ldata)
                    image.set_pixels(data)

        #by default, don't set rowstride (the container format will take care of providing it):
        encoder = self._encoders.get(coding)
        assert encoder is not None, "encoder not found for %s" % coding
        encoder_type, data, client_options, outw, outh, outstride, bpp = encoder(coding, image, options)
        #check cancellation list again since the code above may take some time:
        #but always send mmap data so we can reclaim the space!
        if coding!="mmap" and (self.is_cancelled(sequence)  or self.suspended):
            debug("make_data_packet: dropping data packet for window %s with sequence=%s", wid, sequence)
            return  None
        if data is None:
            #something went wrong.. nothing we can do about it here!
            return  None
        #tell client about delta/store for this pixmap:
        if delta>=0:
            client_options["delta"] = delta
        if store>0:
            self.last_pixmap_data = w, h, coding, store, dpixels
            client_options["store"] = store
        encoding = coding
        if not self.generic_encodings:
            #old clients use non-generic encoding names:
            encoding = NEW_ENCODING_NAMES_TO_OLD.get(coding, coding)
        #actual network packet:
        packet = ["draw", wid, x, y, outw, outh, encoding, data, self._damage_packet_sequence, outstride, client_options]
        end = time.time()
        debug("%.1fms to compress %sx%s pixels using %s with ratio=%.1f%%, delta=%s",
                 (end-start)*1000.0, w, h, coding, 100.0*len(data)/isize, delta)
        self.global_statistics.packet_count += 1
        self.statistics.packet_count += 1
        self._damage_packet_sequence += 1
        self.statistics.encoding_stats.append((encoder_type, w*h, bpp, len(data), end-start))
        #record number of frames and pixels:
        totals = self.statistics.encoding_totals.setdefault(coding, [0, 0])
        totals[0] = totals[0] + 1
        totals[1] = totals[1] + w*h
        self._last_sequence_queued = sequence
        self.encoding_last_used = coding
        #debug("make_data_packet: returning packet=%s", packet[:7]+[".."]+packet[8:])
        return packet


    def mmap_encode(self, coding, image, options):
        data = options["mmap_data"]
        return "mmap", data, {"rgb_format" : image.get_pixel_format()}, image.get_width(), image.get_height(), image.get_rowstride(), 32

    def warn_encoding_once(self, key, message):
        if key not in self._encoding_warnings:
            log.warn("Warning: "+message)
            self._encoding_warnings.add(key)

    def webp_encode(self, coding, image, options):
        enc_webp = get_codec("enc_webp")
        webp_handlers = get_codec("webp_bitmap_handlers")
        assert enc_webp and webp_handlers, "webp components are missing"

        BitmapHandler = webp_handlers.BitmapHandler
        handler_encs = {
                    "RGB" : (BitmapHandler.RGB,     "EncodeRGB",  "EncodeLosslessRGB",  False),
                    "BGR" : (BitmapHandler.BGR,     "EncodeBGR",  "EncodeLosslessBGR",  False),
                    "RGBA": (BitmapHandler.RGBA,    "EncodeRGBA", "EncodeLosslessRGBA", True),
                    "RGBX": (BitmapHandler.RGBA,    "EncodeRGBA", "EncodeLosslessRGBA", False),
                    "BGRA": (BitmapHandler.BGRA,    "EncodeBGRA", "EncodeLosslessBGRA", True),
                    "BGRX": (BitmapHandler.BGRA,    "EncodeBGRA", "EncodeLosslessBGRA", False),
                    }
        pixel_format = image.get_pixel_format()
        h_e = handler_encs.get(pixel_format)
        assert h_e is not None, "cannot handle rgb format %s with webp!" % pixel_format
        bh, lossy_enc, lossless_enc, has_alpha = h_e
        q = self.get_current_quality()
        if options:
            q = options.get("quality", q)
        q = max(1, q)
        enc = None
        if q==100 and has_codec("enc_webp_lossless"):
            enc = getattr(enc_webp, lossless_enc)
            kwargs = {}
            client_options = {}
            debug("webp_encode(%s, %s) using lossless encoder=%s for %s", image, options, enc, pixel_format)
        if enc is None:
            enc = getattr(enc_webp, lossy_enc)
            kwargs = {"quality" : q}
            client_options = {"quality" : q}
            debug("webp_encode(%s, %s) using lossy encoder=%s with quality=%s for %s", image, options, enc, q, pixel_format)
        handler = BitmapHandler(image.get_pixels(), bh, image.get_width(), image.get_height(), image.get_rowstride())
        bpp = 24
        if has_alpha:
            client_options["has_alpha"] = True
            bpp = 32
        return "webp", Compressed("webp", str(enc(handler, **kwargs).data)), client_options, image.get_width(), image.get_height(), 0, bpp

    def rgb_encode(self, coding, image, options):
        pixel_format = image.get_pixel_format()
        #debug("rgb_encode(%s, %s, %s) rgb_formats=%s", coding, image, options, self.rgb_formats)
        if pixel_format not in self.rgb_formats:
            if not self.rgb_reformat(image):
                raise Exception("cannot find compatible rgb format to use for %s! (supported: %s)" % (pixel_format, self.rgb_formats))
            #get the new format:
            pixel_format = image.get_pixel_format()
        #always tell client which pixel format we are sending:
        options = {"rgb_format" : pixel_format}
        #compress here and return a wrapper so network code knows it is already zlib compressed:
        pixels = image.get_pixels()

        level = 0
        if self.rgb_zlib or self.rgb_lz4:
            if len(pixels)<1024:
                min_level = 0
            else:
                min_level = 1
            level = max(min_level, min(5, int(110-self.get_current_speed())/20))
        #by default, wire=raw:
        raw_data = str(pixels)
        wire_data = raw_data
        algo = "not"
        if level>0:
            lz4 = use_lz4 and self.rgb_lz4 and level<=3
            wire_data = compressed_wrapper(coding, pixels, level=level, lz4=lz4)
            raw_data = wire_data.data
            #debug("%s/%s data compressed from %s bytes down to %s (%s%%) with lz4=%s",
            #         coding, pixel_format, len(pixels), len(raw_data), int(100.0*len(raw_data)/len(pixels)), self.rgb_lz4)
            if len(raw_data)>=(len(pixels)-32):
                #compressed is actually bigger! (use uncompressed)
                level = 0
                wire_data = str(pixels)
                raw_data = wire_data
            else:
                if lz4:
                    options["lz4"] = True
                    algo = "lz4"
                else:
                    options["zlib"] = level
                    algo = "zlib"
        if pixel_format.upper().find("A")>=0 or pixel_format.upper().find("X")>=0:
            bpp = 32
        else:
            bpp = 24
        debug("rgb_encode using level=%s, %s compressed %sx%s in %s/%s: %s bytes down to %s", level, algo, image.get_width(), image.get_height(), coding, pixel_format, len(pixels), len(raw_data))
        if not self.encoding_client_options or not self.supports_rgb24zlib:
            return  coding, wire_data, {}, image.get_width(), image.get_height(), image.get_rowstride(), bpp
        #wrap it using "Compressed" so the network layer receiving it
        #won't decompress it (leave it to the client's draw thread)
        return coding, Compressed(coding, raw_data), options, image.get_width(), image.get_height(), image.get_rowstride(), bpp

    def PIL_encode(self, coding, image, options):
        #for more information on pixel formats supported by PIL / Pillow, see:
        #https://github.com/python-imaging/Pillow/blob/master/libImaging/Unpack.c
        assert coding in self.server_core_encodings
        PIL = get_codec("PIL")
        assert PIL is not None, "Python PIL is not available"
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
            #it is safe to use frombuffer() here since the convert()
            #calls below will not convert and modify the data in place
            #and we save the compressed data then discard the image
            im = PIL.Image.frombuffer(rgb, (w, h), image.get_pixels(), "raw", pixel_format, image.get_rowstride())
            if coding.startswith("png") and not self.supports_transparency and rgb=="RGBA":
                im = im.convert("RGB")
                rgb = "RGB"
                bpp = 24
        except Exception, e:
            log.error("PIL_encode(%s) converting to %s failed", (w, h, coding, "%s bytes" % image.get_size(), pixel_format, image.get_rowstride(), options), rgb, exc_info=True)
            raise e
        buf = StringIOClass()
        client_options = {}
        optimize = options.get("optimize")
        if PIL_CAN_OPTIMIZE and optimize is None and self.batch_config.delay>2*self.batch_config.START_DELAY:
            ces = self.get_current_speed()
            mes = self.get_min_speed()
            optimize = ces<50 and ces<(mes+20)          #optimize if speed is close to minimum
        if coding=="jpeg":
            q = self.get_current_quality()
            if options:
                q = options.get("quality", q)
            q = int(min(99, max(1, q)))
            kwargs = im.info
            kwargs["quality"] = q
            if PIL_CAN_OPTIMIZE and optimize is True:
                kwargs["optimize"] = optimize
            im.save(buf, "JPEG", **kwargs)
            client_options["quality"] = q
        else:
            assert coding in ("png", "png/P", "png/L"), "unsupported png encoding: %s" % coding
            if coding in ("png/L", "png/P") and self.supports_transparency and rgb=="RGBA":
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
            if PIL_CAN_OPTIMIZE and optimize is True:
                kwargs["optimize"] = optimize
            if mask is not None:
                client_options["transparency"] = 255
                kwargs["transparency"] = 255
            im.save(buf, "PNG", **kwargs)
        debug("sending %sx%s %s as %s, mode=%s, options=%s", w, h, pixel_format, coding, im.mode, kwargs)
        data = buf.getvalue()
        buf.close()
        return coding, Compressed(coding, data), client_options, image.get_width(), image.get_height(), 0, bpp

    def argb_swap(self, image):
        """ use the argb codec to do the RGB byte swapping """
        pixel_format = image.get_pixel_format()
        if None in (bgra_to_rgb, bgra_to_rgba, argb_to_rgb, argb_to_rgba):
            self.warn_encoding_once("argb-module-missing", "no argb module, cannot convert %s to one of: %s" % (pixel_format, self.rgb_formats))
            return False

        #try to fallback to argb module
        #if we have one of the target pixel formats:
        pixels = image.get_pixels()
        rs = image.get_rowstride()
        if pixel_format in ("BGRX", "BGRA"):
            if self.supports_transparency and "RGBA" in self.rgb_formats:
                image.set_pixels(bgra_to_rgba(pixels))
                image.set_pixel_format("RGBA")
                return True
            if "RGB" in self.rgb_formats:
                image.set_pixels(bgra_to_rgb(pixels))
                image.set_pixel_format("RGB")
                image.set_rowstride(rs/4*3)
                return True
        if pixel_format in ("XRGB", "ARGB"):
            if self.supports_transparency and "RGBA" in self.rgb_formats:
                image.set_pixels(argb_to_rgba(pixels))
                image.set_pixel_format("RGBA")
                return True
            if "RGB" in self.rgb_formats:
                image.set_pixels(argb_to_rgb(pixels))
                image.set_pixel_format("RGB")
                image.set_rowstride(rs/4*3)
                return True
        self.warn_encoding_once(pixel_format+"-format-not-handled", "no matching argb function: cannot convert %s to one of: %s" % (pixel_format, self.rgb_formats))
        return False


    def rgb_reformat(self, image):
        """ convert the RGB pixel data into a format supported by the client """
        #need to convert to a supported format!
        pixel_format = image.get_pixel_format()
        pixels = image.get_pixels()
        PIL = get_codec("PIL")
        if not PIL:
            #try to fallback to argb module
            return self.argb_swap(image)
        modes = {
                 #source  : [(PIL input format, output format), ..]
                 "XRGB"   : [("XRGB", "RGB")],
                 "BGRX"   : [("BGRX", "RGB"), ("BGRX", "RGBX")],
                 #try with alpha first:
                 "BGRA"   : [("BGRA", "RGBA"), ("BGRX", "RGB"), ("BGRX", "RGBX")]
                 }.get(pixel_format)
        target_rgb = [(im,om) for (im,om) in modes if om in self.rgb_formats]
        if len(target_rgb)==0:
            #try argb module:
            if self.argb_swap(image):
                return True
            warning_key = "rgb_reformats(%s)" % pixel_format
            self.warn_encoding_once(warning_key, "cannot convert %s to one of: %s" % (pixel_format, self.rgb_formats))
            return False
        input_format, target_format = target_rgb[0]
        start = time.time()
        w = image.get_width()
        h = image.get_height()
        img = PIL.Image.frombuffer(target_format, (w, h), pixels, "raw", input_format, image.get_rowstride())
        rowstride = w*len(target_format)    #number of characters is number of bytes per pixel!
        data = img.tostring("raw", target_format)
        assert len(data)==rowstride*h, "expected %s bytes in %s format but got %s" % (rowstride*h, len(data))
        image.set_pixels(data)
        image.set_rowstride(rowstride)
        image.set_pixel_format(target_format)
        end = time.time()
        debug("rgb_reformat(%s) converted from %s (%s bytes) to %s (%s bytes) in %.1fms, rowstride=%s", image, pixel_format, len(pixels), target_format, len(data), (end-start)*1000.0, rowstride)
        return True

    def mmap_send(self, image):
        if image.get_pixel_format() not in self.rgb_formats:
            if not self.rgb_reformat(image):
                warning_key = "mmap_send(%s)" % image.get_pixel_format()
                self.warn_encoding_once(warning_key, "cannot use mmap to send %s" % image.get_pixel_format())
                return None
        from xpra.net.mmap_pipe import mmap_write
        start = time.time()
        data = image.get_pixels()
        mmap_data, mmap_free_size = mmap_write(self._mmap, self._mmap_size, data)
        self.global_statistics.mmap_free_size = mmap_free_size
        elapsed = time.time()-start+0.000000001 #make sure never zero!
        debug("%s MBytes/s - %s bytes written to mmap in %.1f ms", int(len(data)/elapsed/1024/1024), len(data), 1000*elapsed)
        if mmap_data is None:
            return None
        self.global_statistics.mmap_bytes_sent += len(data)
        #replace pixels with mmap info:
        return mmap_data
