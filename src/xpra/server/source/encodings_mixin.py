# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2019 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
from math import sqrt
from time import sleep

from xpra.server.source.stub_source_mixin import StubSourceMixin
from xpra.server.window.batch_config import DamageBatchConfig
from xpra.server.server_core import ClientException
from xpra.codecs.video_helper import getVideoHelper
from xpra.codecs.codec_constants import video_spec
from xpra.net.compression import compressed_wrapper, Compressed, use_lz4, use_lzo
from xpra.os_util import monotonic_time, strtobytes
from xpra.server.background_worker import add_work_item
from xpra.util import csv, typedict, envint
from xpra.log import Logger

log = Logger("encoding")
proxylog = Logger("proxy")
statslog = Logger("stats")

MIN_PIXEL_RECALCULATE = envint("XPRA_MIN_PIXEL_RECALCULATE", 2000)


"""
Store information about the client's support for encodings.
Runs the encode thread.
"""
class EncodingsMixin(StubSourceMixin):

    def __init__(self):
        self.server_core_encodings = []
        self.server_encodings = []
        self.default_encoding = None
        self.scaling_control = None

        self.default_quality = 40       #default encoding quality for lossy encodings
        self.default_min_quality = 10   #default minimum encoding quality
        self.default_speed = 40         #encoding speed (only used by x264)
        self.default_min_speed = 10     #default minimum encoding speed

        #contains default values, some of which may be supplied by the client:
        self.default_batch_config = DamageBatchConfig()
        self.global_batch_config = self.default_batch_config.clone()      #global batch config

        self.vrefresh = -1
        self.supports_transparency = False
        self.encoding = None                        #the default encoding for all windows
        self.encodings = ()                         #all the encodings supported by the client
        self.core_encodings = ()
        self.window_icon_encodings = ["premult_argb32"]
        self.rgb_formats = ("RGB",)
        self.encoding_options = typedict()
        self.icons_encoding_options = typedict()
        self.default_encoding_options = typedict()
        self.auto_refresh_delay = 0

        self.zlib = True
        self.lz4 = use_lz4
        self.lzo = use_lzo

        #for managing the recalculate_delays work:
        self.calculate_window_pixels = {}
        self.calculate_window_ids = set()
        self.calculate_timer = 0
        self.calculate_last_time = 0

        #if we "proxy video", we will modify the video helper to add
        #new encoders, so we must make a deep copy to preserve the original
        #which may be used by other clients (other ServerSource instances)
        self.video_helper = getVideoHelper().clone()


    def init_from(self, _protocol, server):
        self.server_core_encodings  = server.core_encodings
        self.server_encodings       = server.encodings
        self.default_encoding       = server.default_encoding
        self.scaling_control        = server.scaling_control
        self.default_quality        = server.default_quality
        self.default_min_quality    = server.default_min_quality
        self.default_speed          = server.default_speed
        self.default_min_speed      = server.default_min_speed

    def cleanup(self):
        self.cancel_recalculate_timer()
        #Warning: this mixin must come AFTER the window mixin!
        #to make sure that it is safe to add the end of queue marker:
        #(all window sources will have stopped queuing data)
        self.queue_encode(None)
        #this should be a noop since we inherit an initialized helper:
        self.video_helper.cleanup()


    def get_caps(self):
        caps = {}
        if self.wants_encodings and self.encoding:
            caps["encoding"] = self.encoding
        if self.wants_features:
            caps.update({
                "auto_refresh_delay"   : self.auto_refresh_delay,
                })
        return caps


    def compressed_wrapper(self, datatype, data, min_saving=128):
        if self.zlib or self.lz4 or self.lzo:
            cw = compressed_wrapper(datatype, data, zlib=self.zlib, lz4=self.lz4, lzo=self.lzo, can_inline=False)
            if len(cw)+min_saving<=len(data):
                #the compressed version is smaller, use it:
                return cw
            #skip compressed version: fall through
        #we can't compress, so at least avoid warnings in the protocol layer:
        return Compressed(datatype, data, can_inline=True)


    def recalculate_delays(self):
        """ calls update_averages() on ServerSource.statistics (GlobalStatistics)
            and WindowSource.statistics (WindowPerformanceStatistics) for each window id in calculate_window_ids,
            this runs in the worker thread.
        """
        self.calculate_timer = 0
        if self.is_closed():
            return
        now = monotonic_time()
        self.calculate_last_time = now
        p = self.protocol
        if not p:
            return
        conn = p._conn
        if not conn:
            return
        self.statistics.bytes_sent.append((now, conn.output_bytecount))
        self.statistics.update_averages()
        self.update_bandwidth_limits()
        wids = tuple(self.calculate_window_ids)  #make a copy so we don't clobber new wids
        focus = self.get_focus()
        sources = self.window_sources.items()
        maximized_wids = tuple(wid for wid, source in sources if source is not None and source.maximized)
        fullscreen_wids = tuple(wid for wid, source in sources if source is not None and source.fullscreen)
        log("recalculate_delays() wids=%s, focus=%s, maximized=%s, fullscreen=%s",
            wids, focus, maximized_wids, fullscreen_wids)
        for wid in wids:
            #this is safe because we only add to this set from other threads:
            self.calculate_window_ids.remove(wid)
            try:
                del self.calculate_window_pixels[wid]
            except:
                pass
            ws = self.window_sources.get(wid)
            if ws is None:
                continue
            try:
                ws.statistics.update_averages()
                ws.calculate_batch_delay(wid==focus,
                                         len(fullscreen_wids)>0 and wid not in fullscreen_wids,
                                         len(maximized_wids)>0 and wid not in maximized_wids)
                ws.reconfigure()
            except:
                log.error("error on window %s", wid, exc_info=True)
            if self.is_closed():
                return
            #allow other threads to run
            #(ideally this would be a low priority thread)
            sleep(0)
        #calculate weighted average as new global default delay:
        wdimsum, wdelay, tsize, tcount = 0, 0, 0, 0
        for ws in tuple(self.window_sources.values()):
            if ws.batch_config.last_updated<=0:
                continue
            w, h = ws.window_dimensions
            tsize += w*h
            tcount += 1
            time_w = 2.0+(now-ws.batch_config.last_updated)     #add 2 seconds to even things out
            weight = int(w*h*time_w)
            wdelay += ws.batch_config.delay*weight
            wdimsum += weight
        if wdimsum>0 and tcount>0:
            #weighted delay:
            delay = wdelay // wdimsum
            self.global_batch_config.last_delays.append((now, delay))
            self.global_batch_config.delay = delay
            #store the delay as a normalized value per megapixel
            #so we can adapt it to different window sizes:
            avg_size = tsize // tcount
            ratio = sqrt(1000000.0 / avg_size)
            normalized_delay = int(delay * ratio)
            self.global_batch_config.delay_per_megapixel = normalized_delay
            log("delay_per_megapixel=%i, delay=%i, for wdelay=%i, avg_size=%i, ratio=%.2f",
                normalized_delay, delay, wdelay, avg_size, ratio)

    def may_recalculate(self, wid, pixel_count):
        if wid in self.calculate_window_ids:
            return  #already scheduled
        v = self.calculate_window_pixels.get(wid, 0)+pixel_count
        self.calculate_window_pixels[wid] = v
        if v<MIN_PIXEL_RECALCULATE:
            return  #not enough pixel updates
        statslog("may_recalculate(%i, %i) total %i pixels, scheduling recalculate work item", wid, pixel_count, v)
        self.calculate_window_ids.add(wid)
        if self.calculate_timer:
            #already due
            return
        delta = monotonic_time() - self.calculate_last_time
        RECALCULATE_DELAY = 1.0           #1s
        if delta>RECALCULATE_DELAY:
            add_work_item(self.recalculate_delays)
        else:
            delay = int(1000*(RECALCULATE_DELAY-delta))
            self.calculate_timer = self.timeout_add(delay, add_work_item, self.recalculate_delays)

    def cancel_recalculate_timer(self):
        ct = self.calculate_timer
        if ct:
            self.calculate_timer = 0
            self.source_remove(ct)


    def parse_client_caps(self, c):
        #batch options:
        def batch_value(prop, default, minv=None, maxv=None):
            assert default is not None
            def parse_batch_int(value, varname):
                if value is not None:
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        log.error("Error: invalid value '%s' for batch option %s", value, varname)
                return None
            #from client caps first:
            cpname = "batch.%s" % prop
            v = parse_batch_int(c.get(cpname), cpname)
            #try env:
            if v is None:
                evname = "XPRA_BATCH_%s" % prop.upper()
                v = parse_batch_int(os.environ.get(evname), evname)
            #fallback to default:
            if v is None:
                v = default
            if minv is not None:
                v = max(minv, v)
            if maxv is not None:
                v = min(maxv, v)
            assert v is not None
            return v

        #general features:
        self.zlib = c.boolget("zlib", True)
        self.lz4 = c.boolget("lz4", False) and use_lz4
        self.lzo = c.boolget("lzo", False) and use_lzo

        self.vrefresh = c.intget("vrefresh", -1)

        default_min_delay = max(DamageBatchConfig.MIN_DELAY, 1000//(self.vrefresh or 60))
        dbc = self.default_batch_config
        dbc.always      = bool(batch_value("always", DamageBatchConfig.ALWAYS))
        dbc.min_delay   = batch_value("min_delay", default_min_delay, 0, 1000)
        dbc.max_delay   = batch_value("max_delay", DamageBatchConfig.MAX_DELAY, 1, 15000)
        dbc.max_events  = batch_value("max_events", DamageBatchConfig.MAX_EVENTS)
        dbc.max_pixels  = batch_value("max_pixels", DamageBatchConfig.MAX_PIXELS)
        dbc.time_unit   = batch_value("time_unit", DamageBatchConfig.TIME_UNIT, 1)
        dbc.delay       = batch_value("delay", DamageBatchConfig.START_DELAY, 0)
        log("default batch config: %s", dbc)

        #encodings:
        self.encodings = c.strlistget("encodings")
        self.core_encodings = c.strlistget("encodings.core", self.encodings)
        if self.send_windows and not self.core_encodings:
            raise ClientException("client failed to specify any supported encodings")
        if "png" in self.core_encodings:
            self.window_icon_encodings.append("png")
        self.window_icon_encodings = c.strlistget("encodings.window-icon", ["premult_argb32"])
        self.rgb_formats = c.strlistget("encodings.rgb_formats", ["RGB"])
        #skip all other encoding related settings if we don't send pixels:
        if not self.send_windows:
            log("windows/pixels forwarding is disabled for this client")
        else:
            self.parse_encoding_caps(c)

    def parse_encoding_caps(self, c):
        self.set_encoding(c.strget("encoding", None), None)
        #encoding options (filter):
        #1: these properties are special cased here because we
        #defined their name before the "encoding." prefix convention,
        #or because we want to pass default values (zlib/lz4):
        for k,ek in {"initial_quality"          : "initial_quality",
                     "quality"                  : "quality",
                     }.items():
            if k in c:
                self.encoding_options[ek] = c.intget(k)
        for k,ek in {"zlib"                     : "rgb_zlib",
                     "lz4"                      : "rgb_lz4",
                     }.items():
            if k in c:
                self.encoding_options[ek] = c.boolget(k)
        #2: standardized encoding options:
        for k in c.keys():
            #yaml gives us str..
            k = strtobytes(k)
            if k.startswith(b"theme.") or k.startswith(b"encoding.icons."):
                self.icons_encoding_options[k.replace(b"encoding.icons.", b"").replace(b"theme.", b"")] = c.get(k)
            elif k.startswith(b"encoding."):
                stripped_k = k[len(b"encoding."):]
                if stripped_k in (b"transparency",
                                  b"rgb_zlib", b"rgb_lz4", b"rgb_lzo",
                                  b"video_scaling"):
                    v = c.boolget(k)
                elif stripped_k in (b"initial_quality", b"initial_speed",
                                    b"min-quality", b"quality",
                                    b"min-speed", b"speed"):
                    v = c.intget(k)
                else:
                    v = c.get(k)
                self.encoding_options[stripped_k] = v
        log("encoding options: %s", self.encoding_options)
        log("icons encoding options: %s", self.icons_encoding_options)

        #handle proxy video: add proxy codec to video helper:
        pv = self.encoding_options.boolget("proxy.video")
        proxylog("proxy.video=%s", pv)
        if pv:
            #enabling video proxy:
            try:
                self.parse_proxy_video()
            except:
                proxylog.error("failed to parse proxy video", exc_info=True)

        sc = self.encoding_options.get("scaling.control", self.scaling_control)
        if sc is not None:
            #"encoding_options" are exposed via "xpra info",
            #so we can't have None values in there (bencoder would choke)
            self.default_encoding_options["scaling.control"] = sc
        q = self.encoding_options.intget("quality", self.default_quality)         #0.7 onwards:
        if q>0:
            self.default_encoding_options["quality"] = q
        mq = self.encoding_options.intget("min-quality", self.default_min_quality)
        if mq>0 and (q<=0 or q>mq):
            self.default_encoding_options["min-quality"] = mq
        s = self.encoding_options.intget("speed", self.default_speed)
        if s>0:
            self.default_encoding_options["speed"] = s
        ms = self.encoding_options.intget("min-speed", self.default_min_speed)
        if ms>0 and (s<=0 or s>ms):
            self.default_encoding_options["min-speed"] = ms
        log("default encoding options: %s", self.default_encoding_options)
        self.auto_refresh_delay = c.intget("auto_refresh_delay", 0)
        #check for mmap:
        if getattr(self, "mmap_size", 0)==0:
            others = tuple(x for x in self.core_encodings
                           if x in self.server_core_encodings and x!=self.encoding)
            if self.encoding=="auto":
                s = "automatic picture encoding enabled"
            else:
                s = "using %s as primary encoding" % self.encoding
            if others:
                log.info(" %s, also available:", s)
                log.info("  %s", csv(others))
            else:
                log.warn(" %s", s)
                log.warn("  no other encodings are available!")

    def parse_proxy_video(self):
        from xpra.codecs.enc_proxy.encoder import Encoder
        proxy_video_encodings = self.encoding_options.get("proxy.video.encodings")
        proxylog("parse_proxy_video() proxy.video.encodings=%s", proxy_video_encodings)
        for encoding, colorspace_specs in proxy_video_encodings.items():
            for colorspace, spec_props in colorspace_specs.items():
                for spec_prop in spec_props:
                    #make a new spec based on spec_props:
                    spec_prop = typedict(spec_prop)
                    input_colorspace = spec_prop.strget("input_colorspace")
                    output_colorspaces = spec_prop.strlistget("output_colorspaces")
                    if not input_colorspace or not output_colorspaces:
                        log.warn("Warning: invalid proxy video encoding '%s':", encoding)
                        log.warn(" missing colorspace attributes")
                        continue
                    spec = video_spec(codec_class=Encoder,
                                      has_lossless_mode=spec_prop.boolget("has_lossless_mode", False),
                                      input_colorspace=input_colorspace,
                                      output_colorspaces=output_colorspaces,
                                      codec_type="proxy", encoding=encoding,
                                      )
                    for k,v in spec_prop.items():
                        if k.startswith("_") or not hasattr(spec, k):
                            log.warn("Warning: invalid proxy codec attribute '%s'", k)
                            continue
                        setattr(spec, k, v)
                    proxylog("parse_proxy_video() adding: %s / %s / %s", encoding, colorspace, spec)
                    self.video_helper.add_encoder_spec(encoding, colorspace, spec)


    ######################################################################
    # Functions used by the server to request something
    # (window events, stats, user requests, etc)
    #
    def set_auto_refresh_delay(self, delay, window_ids):
        if window_ids is not None:
            wss = (self.window_sources.get(wid) for wid in window_ids)
        else:
            wss = self.window_sources.values()
        for ws in wss:
            if ws is not None:
                ws.set_auto_refresh_delay(delay)

    def set_encoding(self, encoding, window_ids, strict=False):
        """ Changes the encoder for the given 'window_ids',
            or for all windows if 'window_ids' is None.
        """
        log("set_encoding(%s, %s, %s)", encoding, window_ids, strict)
        if not self.ui_client:
            return
        if encoding and encoding!="auto":
            #old clients (v0.9.x and earlier) only supported 'rgb24' as 'rgb' mode:
            if encoding=="rgb24":
                encoding = "rgb"
            if encoding not in self.encodings:
                log.warn("Warning: client specified '%s' encoding,", encoding)
                log.warn(" but it only supports: %s" % csv(self.encodings))
            if encoding not in self.server_encodings:
                log.error("Error: encoding %s is not supported by this server", encoding)
                encoding = None
        if not encoding:
            encoding = "auto"
        if window_ids is not None:
            wss = [self.window_sources.get(wid) for wid in window_ids]
        else:
            wss = self.window_sources.values()
        #if we're updating all the windows, reset global stats too:
        if set(wss).issuperset(self.window_sources.values()):
            log("resetting global stats")
            self.statistics.reset()
            self.global_batch_config = self.default_batch_config.clone()
        for ws in wss:
            if ws is not None:
                ws.set_new_encoding(encoding, strict)
        if not window_ids:
            self.encoding = encoding


    def get_info(self):
        info = {
                "auto_refresh"      : self.auto_refresh_delay,
                "lz4"               : self.lz4,
                "lzo"               : self.lzo,
                "vertical-refresh"  : self.vrefresh,
                }
        ieo = dict(self.icons_encoding_options)
        try:
            del ieo["default.icons"]
        except KeyError:
            pass
        #encoding:
        info.update({
                     "encodings"        : {
                                           ""      : self.encodings,
                                           "core"  : self.core_encodings,
                                           "window-icon"    : self.window_icon_encodings,
                                           },
                     "icons"            : ieo,
                     })
        einfo = {
            "default"      : self.default_encoding or "",
            "defaults"     : self.default_encoding_options,
            "client-defaults" : self.encoding_options,
            }
        info.setdefault("encoding", {}).update(einfo)
        return info


    def set_min_quality(self, min_quality):
        for ws in tuple(self.window_sources.values()):
            ws.set_min_quality(min_quality)

    def set_quality(self, quality):
        for ws in tuple(self.window_sources.values()):
            ws.set_quality(quality)

    def set_min_speed(self, min_speed):
        for ws in tuple(self.window_sources.values()):
            ws.set_min_speed(min_speed)

    def set_speed(self, speed):
        for ws in tuple(self.window_sources.values()):
            ws.set_speed(speed)


    def update_batch(self, wid, window, batch_props):
        ws = self.window_sources.get(wid)
        if ws:
            if "reset" in batch_props:
                ws.batch_config = self.make_batch_config(wid, window)
            for x in ("always", "locked"):
                if x in batch_props:
                    setattr(ws.batch_config, x, batch_props.boolget(x))
            for x in ("min_delay", "max_delay", "timeout_delay", "delay"):
                if x in batch_props:
                    setattr(ws.batch_config, x, batch_props.intget(x))
            log("batch config updated for window %s: %s", wid, ws.batch_config)

    def make_batch_config(self, wid, window):
        batch_config = self.default_batch_config.clone()
        batch_config.wid = wid
        #scale initial delay based on window size
        #(the global value is normalized to 1MPixel)
        #but use sqrt to smooth things and prevent excesses
        #(ie: a 4MPixel window, will start at 2 times the global delay)
        #(ie: a 0.5MPixel window will start at 0.7 times the global delay)
        dpm = self.global_batch_config.delay_per_megapixel
        w, h = window.get_dimensions()
        if dpm>=0:
            ratio = sqrt(1000000.0 / (w*h))
            batch_config.delay = max(batch_config.min_delay, min(batch_config.max_delay, int(dpm * sqrt(ratio))))
        log("make_batch_config(%i, %s) global delay per megapixel=%i, new window delay for %ix%i=%s",
                 wid, window, dpm, w, h, batch_config.delay)
        return batch_config
