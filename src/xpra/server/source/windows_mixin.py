# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2010-2020 Antoine Martin <antoine@xpra.org>
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
from io import BytesIO

from xpra.server.source.stub_source_mixin import StubSourceMixin
from xpra.server.window.metadata import make_window_metadata
from xpra.net.compression import Compressed
from xpra.os_util import monotonic_time, strtobytes, bytestostr
from xpra.util import typedict, envint, envbool, DEFAULT_METADATA_SUPPORTED, XPRA_BANDWIDTH_NOTIFICATION_ID
from xpra.log import Logger

log = Logger("server")
cursorlog = Logger("cursor")
metalog = Logger("metadata")
bandwidthlog = Logger("bandwidth")
eventslog = Logger("events")
filterslog = Logger("filters")

CONGESTION_WARNING_EVENT_COUNT = envint("XPRA_CONGESTION_WARNING_EVENT_COUNT", 10)
CONGESTION_REPEAT_DELAY = envint("XPRA_CONGESTION_REPEAT_DELAY", 60)
SAVE_CURSORS = envbool("XPRA_SAVE_CURSORS", False)
MIN_BANDWIDTH = envint("XPRA_MIN_BANDWIDTH", 5*1024*1024)

PROPERTIES_DEBUG = [x.strip() for x in os.environ.get("XPRA_WINDOW_PROPERTIES_DEBUG", "").split(",")]


"""
Handle window forwarding:
- damage
- geometry
- events
etc
"""
class WindowsMixin(StubSourceMixin):

    @classmethod
    def is_needed(cls, caps : typedict) -> bool:
        return caps.boolget("windows")


    def __init__(self):
        self.get_transient_for = None
        self.get_focus = None
        self.get_cursor_data_cb = None
        self.get_window_id = None
        self.window_filters = []
        self.readonly = False

    def init_from(self, _protocol, server):
        self.get_transient_for  = server.get_transient_for
        self.get_focus          = server.get_focus
        self.get_cursor_data_cb = server.get_cursor_data
        self.get_window_id      = server.get_window_id
        self.window_filters     = server.window_filters
        self.readonly           = server.readonly

    def init_state(self):
        #WindowSource for each Window ID
        self.window_sources = {}

        self.window_frame_sizes = {}
        self.suspended = False
        self.send_cursors = False
        self.cursor_encodings = ()
        self.send_bell = False
        self.send_windows = True
        self.pointer_grabs = False
        self.window_min_size = 0, 0
        self.window_max_size = 0, 0
        self.system_tray = False
        self.metadata_supported = ()

        self.cursor_timer = None
        self.last_cursor_sent = None

    def cleanup(self):
        for window_source in self.all_window_sources():
            window_source.cleanup()
        self.window_sources = {}
        self.cancel_cursor_timer()

    def all_window_sources(self):
        return tuple(self.window_sources.values())


    def suspend(self, ui, wd):
        eventslog("suspend(%s, %s) suspended=%s", ui, wd, self.suspended)
        if ui:
            self.suspended = True
        for wid in wd.keys():
            ws = self.window_sources.get(wid)
            if ws:
                ws.suspend()

    def resume(self, ui, wd):
        eventslog("resume(%s, %s) suspended=%s", ui, wd, self.suspended)
        if ui:
            self.suspended = False
        for wid in wd.keys():
            ws = self.window_sources.get(wid)
            if ws:
                ws.resume()
        self.send_cursor()


    def go_idle(self):
        #usually fires from the server's idle_grace_timeout_cb
        if self.idle:
            return
        self.idle = True
        for window_source in self.all_window_sources():
            window_source.go_idle()

    def no_idle(self):
        #on user event, we stop being idle
        if not self.idle:
            return
        self.idle = False
        for window_source in self.all_window_sources():
            window_source.no_idle()


    def parse_client_caps(self, c):
        #self.ui_client = c.boolget("ui_client", True)
        self.send_windows = self.ui_client and c.boolget("windows", True)
        self.pointer_grabs = c.boolget("pointer.grabs")
        self.send_cursors = self.send_windows and c.boolget("cursors")
        self.cursor_encodings = c.strtupleget("encodings.cursor")
        self.send_bell = c.boolget("bell")
        self.system_tray = c.boolget("system_tray")
        self.metadata_supported = c.strtupleget("metadata.supported", DEFAULT_METADATA_SUPPORTED)
        self.window_frame_sizes = typedict(c.dictget("window.frame_sizes", {}))
        self.window_min_size = c.inttupleget("window.min-size", (0, 0))
        self.window_max_size = c.inttupleget("window.max-size", (0, 0))
        log("cursors=%s (encodings=%s), bell=%s, notifications=%s",
            self.send_cursors, self.cursor_encodings, self.send_bell, self.send_notifications)
        log("client uuid %s", self.uuid)

        #window filters:
        try:
            for object_name, property_name, operator, value in c.tupleget("window-filters"):
                self.add_window_filter(object_name, property_name, operator, value)
        except Exception as e:
            filterslog.error("Error parsing window-filters: %s", e)


    def get_caps(self) -> dict:
        return {}


    ######################################################################
    # info:
    def get_info(self) -> dict:
        info = {
            "windows"       : self.send_windows,
            "cursors"       : self.send_cursors,
            "bell"          : self.send_bell,
            "system-tray"   : self.system_tray,
            "suspended"     : self.suspended,
            }
        wsize = info.setdefault("window-size", {})
        wsize.update({
            "min"   : self.window_min_size,
            "max"   : self.window_max_size,
            })
        if self.window_frame_sizes:
            wsize.update({"frame-sizes" : self.window_frame_sizes})
        info.update(self.get_window_info())
        return info

    def get_window_info(self) -> dict:
        """
            Adds encoding and window specific information
        """
        from xpra.simple_stats import get_list_stats
        pqpixels = [x[2] for x in tuple(self.packet_queue)]
        pqpi = get_list_stats(pqpixels)
        if pqpixels:
            pqpi["current"] = pqpixels[-1]
        info = {"damage"    : {
                               "compression_queue"      : {"size" : {"current" : self.encode_queue_size()}},
                               "packet_queue"           : {"size" : {"current" : len(self.packet_queue)}},
                               "packet_queue_pixels"    : pqpi,
                               },
                "batch"     : self.global_batch_config.get_info(),
                }
        info.update(self.statistics.get_info())

        if self.window_sources:
            total_pixels = 0
            total_time = 0.0
            in_latencies, out_latencies = [], []
            winfo = {}
            for wid, ws in list(self.window_sources.items()):
                #per-window source stats:
                winfo[wid] = ws.get_info()
                #collect stats for global averages:
                for _, _, pixels, _, _, encoding_time in tuple(ws.statistics.encoding_stats):
                    total_pixels += pixels
                    total_time += encoding_time
                in_latencies += [x*1000 for _, _, _, x in tuple(ws.statistics.damage_in_latency)]
                out_latencies += [x*1000 for _, _, _, x in tuple(ws.statistics.damage_out_latency)]
            info["window"] = winfo
            v = 0
            if total_time>0:
                v = int(total_pixels / total_time)
            info.setdefault("encoding", {})["pixels_encoded_per_second"] = v
            dinfo = info.setdefault("damage", {})
            dinfo["in_latency"] = get_list_stats(in_latencies, show_percentile=[9])
            dinfo["out_latency"] = get_list_stats(out_latencies, show_percentile=[9])
        return info


    ######################################################################
    # grabs:
    def pointer_grab(self, wid):
        if self.pointer_grabs and self.hello_sent:
            self.send("pointer-grab", wid)

    def pointer_ungrab(self, wid):
        if self.pointer_grabs and self.hello_sent:
            self.send("pointer-ungrab", wid)


    ######################################################################
    # cursors:
    def send_cursor(self):
        if not self.send_cursors or self.suspended or not self.hello_sent:
            return
        #if not pending already, schedule it:
        if not self.cursor_timer:
            delay = max(10, int(self.global_batch_config.delay/4))
            self.cursor_timer = self.timeout_add(delay, self.do_send_cursor, delay)

    def cancel_cursor_timer(self):
        ct = self.cursor_timer
        if ct:
            self.cursor_timer = None
            self.source_remove(ct)

    def do_send_cursor(self, delay):
        self.cursor_timer = None
        cd = self.get_cursor_data_cb()
        if not cd or not cd[0]:
            self.send_empty_cursor()
            return
        cursor_data = list(cd[0])
        cursor_sizes = cd[1]
        #skip first two fields (if present) as those are coordinates:
        if self.last_cursor_sent and self.last_cursor_sent[2:9]==cursor_data[2:9]:
            cursorlog("do_send_cursor(..) cursor identical to the last one we sent, nothing to do")
            return
        self.last_cursor_sent = cursor_data[:9]
        w, h, _xhot, _yhot, serial, pixels, name = cursor_data[2:9]
        #compress pixels if needed:
        encoding = "raw"
        if pixels is not None:
            #convert bytearray to string:
            cpixels = strtobytes(pixels)
            if "png" in self.cursor_encodings:
                from PIL import Image
                cursorlog("do_send_cursor() loading %i bytes of cursor pixel data for %ix%i cursor named '%s'",
                          len(cpixels), w, h, bytestostr(name))
                img = Image.frombytes("RGBA", (w, h), cpixels, "raw", "BGRA", w*4, 1)
                buf = BytesIO()
                img.save(buf, "PNG")
                pngdata = buf.getvalue()
                buf.close()
                cpixels = Compressed("png cursor", pngdata, can_inline=True)
                encoding = "png"
                if SAVE_CURSORS:
                    filename = "raw-cursor-%#x.png" % serial
                    with open(filename, "wb") as f:
                        f.write(pngdata)
                    cursorlog("cursor saved to %s", filename)
            elif len(cpixels)>=256 and ("raw" in self.cursor_encodings or not self.cursor_encodings):
                cpixels = self.compressed_wrapper("cursor", pixels)
                cursorlog("do_send_cursor(..) pixels=%s ", cpixels)
                encoding = "raw"
            cursor_data[7] = cpixels
        cursorlog("do_send_cursor(..) %sx%s %s cursor name='%s', serial=%#x with delay=%s (cursor_encodings=%s)",
                  w, h, (encoding or "empty"), bytestostr(name), serial, delay, self.cursor_encodings)
        args = [encoding] + list(cursor_data[:9]) + [cursor_sizes[0]] + list(cursor_sizes[1])
        self.send_more("cursor", *args)

    def send_empty_cursor(self):
        cursorlog("send_empty_cursor(..)")
        self.last_cursor_sent = None
        self.send_more("cursor", "")


    def bell(self, wid, device, percent, pitch, duration, bell_class, bell_id, bell_name):
        if not self.send_bell or self.suspended or not self.hello_sent:
            return
        self.send_async("bell", wid, device, percent, pitch, duration, bell_class, bell_id, bell_name)


    ######################################################################
    # window filters:
    def reset_window_filters(self):
        self.window_filters = [(uuid, f) for uuid, f in self.window_filters if uuid!=self.uuid]

    def get_all_window_filters(self):
        return [f for uuid, f in self.window_filters if uuid==self.uuid]

    def add_window_filter(self, object_name, property_name, operator, value):
        from xpra.server.window.filters import get_window_filter
        window_filter = get_window_filter(object_name, property_name, operator, value)
        assert window_filter
        self.do_add_window_filter(window_filter)

    def do_add_window_filter(self, window_filter):
        #(reminder: filters are shared between all sources)
        self.window_filters.append((self.uuid, window_filter))

    def can_send_window(self, window):
        if not self.hello_sent or not (self.send_windows or self.system_tray):
            return False
        #we could also allow filtering for system tray windows?
        if self.window_filters and self.send_windows and not window.is_tray():
            for uuid, window_filter in self.window_filters:
                filterslog("can_send_window(%s) checking %s for uuid=%s (client uuid=%s)",
                           window, window_filter, uuid, self.uuid)
                if window_filter.matches(window):
                    v = uuid=="*" or uuid==self.uuid
                    filterslog("can_send_window(%s)=%s", window, v)
                    return v
        if self.send_windows and self.system_tray:
            #common case shortcut
            v = True
        elif window.is_tray():
            v = self.system_tray
        else:
            v = self.send_windows
        filterslog("can_send_window(%s)=%s", window, v)
        return v


    ######################################################################
    # windows:
    def initiate_moveresize(self, wid, window, x_root, y_root, direction, button, source_indication):
        if not self.can_send_window(window):
            return
        log("initiate_moveresize sending to %s", self)
        self.send("initiate-moveresize", wid, x_root, y_root, direction, button, source_indication)

    def or_window_geometry(self, wid, window, x, y, w, h):
        if not self.can_send_window(window):
            return
        self.send("configure-override-redirect", wid, x, y, w, h)

    def window_metadata(self, wid, window, prop):
        if not self.can_send_window(window):
            return
        if prop=="icons":
            self.send_window_icon(wid, window)
        else:
            metadata = self._make_metadata(window, prop)
            if prop in PROPERTIES_DEBUG:
                metalog.info("make_metadata(%s, %s, %s)=%s", wid, window, prop, metadata)
            else:
                metalog("make_metadata(%s, %s, %s)=%s", wid, window, prop, metadata)
            if metadata:
                self.send("window-metadata", wid, metadata)


    # Takes the name of a WindowModel property, and returns a dictionary of
    # xpra window metadata values that depend on that property
    def _make_metadata(self, window, propname, skip_defaults=False):
        if propname not in self.metadata_supported:
            metalog("make_metadata: client does not support '%s'", propname)
            return {}
        metadata = make_window_metadata(window, propname,
                                        get_transient_for=self.get_transient_for,
                                        get_window_id=self.get_window_id,
                                        skip_defaults=skip_defaults)
        if self.readonly:
            metalog("overriding size-constraints for readonly mode")
            size = window.get_dimensions()
            metadata["size-constraints"] = {
                "maximum-size"  : size,
                "minimum-size"  : size,
                "base-size" : size,
                }
        return metadata

    def new_tray(self, wid, window, w, h):
        assert window.is_tray()
        if not self.can_send_window(window):
            return
        metadata = {}
        for propname in list(window.get_property_names()):
            metadata.update(self._make_metadata(window, propname, skip_defaults=True))
        self.send_async("new-tray", wid, w, h, metadata)

    def new_window(self, ptype, wid, window, x, y, w, h, client_properties):
        if not self.can_send_window(window):
            return
        send_props = list(window.get_property_names())
        send_raw_icon = "icons" in send_props
        if send_raw_icon:
            send_props.remove("icons")
        metadata = {}
        for prop in send_props:
            v = self._make_metadata(window, prop, skip_defaults=True)
            if prop in PROPERTIES_DEBUG:
                metalog.info("make_metadata(%s, %s, %s)=%s", wid, window, prop, v)
            else:
                metalog("make_metadata(%s, %s, %s)=%s", wid, window, prop, v)
            metadata.update(v)
        log("new_window(%s, %s, %s, %s, %s, %s, %s, %s) metadata(%s)=%s",
            ptype, window, wid, x, y, w, h, client_properties, send_props, metadata)
        self.send_async(ptype, wid, x, y, w, h, metadata, client_properties or {})
        if send_raw_icon:
            self.send_window_icon(wid, window)

    def send_window_icon(self, wid, window):
        if not self.can_send_window(window):
            return
        #we may need to make a new source at this point:
        ws = self.make_window_source(wid, window)
        if ws:
            ws.send_window_icon()


    def lost_window(self, wid, _window):
        self.send("lost-window", wid)

    def move_resize_window(self, wid, window, x, y, ww, wh, resize_counter=0):
        """
        The server detected that the application window has been moved and/or resized,
        we forward it if the client supports this type of event.
        """
        if not self.can_send_window(window):
            return
        self.send("window-move-resize", wid, x, y, ww, wh, resize_counter)

    def resize_window(self, wid, window, ww, wh, resize_counter=0):
        if not self.can_send_window(window):
            return
        self.send("window-resized", wid, ww, wh, resize_counter)


    def cancel_damage(self, wid):
        """
        Use this method to cancel all currently pending and ongoing
        damage requests for a window.
        """
        ws = self.window_sources.get(wid)
        if ws:
            ws.cancel_damage()


    def map_window(self, wid, window, coords=None):
        ws = self.make_window_source(wid, window)
        ws.map(coords)

    def unmap_window(self, wid, _window):
        ws = self.window_sources.get(wid)
        if ws:
            ws.unmap()


    def raise_window(self, wid, window):
        if not self.can_send_window(window):
            return
        self.send_async("raise-window", wid)

    def remove_window(self, wid, window):
        """ The given window is gone, ensure we free all the related resources """
        if not self.can_send_window(window):
            return
        ws = self.window_sources.get(wid)
        if ws:
            del self.window_sources[wid]
            ws.cleanup()
        try:
            del self.calculate_window_pixels[wid]
        except KeyError:
            pass


    def refresh(self, wid, window, opts):
        if not self.can_send_window(window):
            return
        self.cancel_damage(wid)
        w, h = window.get_dimensions()
        self.damage(wid, window, 0, 0, w, h, opts)

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

    def set_client_properties(self, wid, window, new_client_properties):
        assert self.send_windows
        ws = self.make_window_source(wid, window)
        ws.set_client_properties(new_client_properties)


    def get_window_source(self, wid):
        return self.window_sources.get(wid)

    def make_window_source(self, wid, window):
        ws = self.window_sources.get(wid)
        if ws is None:
            batch_config = self.make_batch_config(wid, window)
            ww, wh = window.get_dimensions()
            bandwidth_limit = self.bandwidth_limit
            mmap = getattr(self, "mmap", None)
            mmap_size = getattr(self, "mmap_size", 0)
            av_sync = getattr(self, "av_sync", False)
            av_sync_delay = getattr(self, "av_sync_delay", 0)
            if mmap_size>0:
                bandwidth_limit = 0
            from xpra.server.window.window_video_source import WindowVideoSource
            ws = WindowVideoSource(
                              self.idle_add, self.timeout_add, self.source_remove,
                              ww, wh,
                              self.record_congestion_event, self.encode_queue_size,
                              self.call_in_encode_thread, self.queue_packet, self.compressed_wrapper,
                              self.statistics,
                              wid, window, batch_config, self.auto_refresh_delay,
                              av_sync, av_sync_delay,
                              self.video_helper,
                              self.server_core_encodings, self.server_encodings,
                              self.encoding, self.encodings, self.core_encodings,
                              self.window_icon_encodings, self.encoding_options, self.icons_encoding_options,
                              self.rgb_formats,
                              self.default_encoding_options,
                              mmap, mmap_size, bandwidth_limit, self.jitter)
            self.window_sources[wid] = ws
            if len(self.window_sources)>1:
                #re-distribute bandwidth:
                self.update_bandwidth_limits()
        return ws


    def damage(self, wid, window, x, y, w, h, options=None):
        """
            Main entry point from the window manager,
            we dispatch to the WindowSource for this window id
            (creating a new one if needed)
        """
        if not self.can_send_window(window):
            return
        assert window is not None
        if options:
            damage_options = options.copy()
        else:
            damage_options = {}
        self.statistics.damage_last_events.append((wid, monotonic_time(), w*h))
        ws = self.make_window_source(wid, window)
        ws.damage(x, y, w, h, damage_options)

    def client_ack_damage(self, damage_packet_sequence, wid, width, height, decode_time, message):
        """
            The client is acknowledging a damage packet,
            we record the 'client decode time' (which is provided by the client)
            and WindowSource will calculate and record the "client latency".
            (since it knows when the "draw" packet was sent)
        """
        if not self.send_windows:
            log.error("client_ack_damage when we don't send any window data!?")
            return
        if decode_time>0:
            self.statistics.client_decode_time.append((wid, monotonic_time(), width*height, decode_time))
        ws = self.window_sources.get(wid)
        if ws:
            ws.damage_packet_acked(damage_packet_sequence, width, height, decode_time, message)
            self.may_recalculate(wid, width*height)

#
# Methods used by WindowSource:
#
    def record_congestion_event(self, source, late_pct=0, send_speed=0):
        if not self.bandwidth_detection:
            return
        gs = self.statistics
        if not gs:
            #window cleaned up?
            return
        now = monotonic_time()
        elapsed = now-self.bandwidth_warning_time
        bandwidthlog("record_congestion_event(%s, %i, %i) bandwidth_warnings=%s, elapsed time=%i",
                     source, late_pct, send_speed, self.bandwidth_warnings, elapsed)
        gs.last_congestion_time = now
        gs.congestion_send_speed.append((now, late_pct, send_speed))
        if self.bandwidth_warnings and elapsed>CONGESTION_REPEAT_DELAY:
            #enough congestion events?
            T = 10
            min_time = now-T
            count = len(tuple(True for x in gs.congestion_send_speed if x[0]>min_time))
            bandwidthlog("record_congestion_event: %i events in the last %i seconds (warnings after %i)",
                         count, T, CONGESTION_WARNING_EVENT_COUNT)
            if count>CONGESTION_WARNING_EVENT_COUNT:
                self.bandwidth_warning_time = now
                nid = XPRA_BANDWIDTH_NOTIFICATION_ID
                summary = "Network Performance Issue"
                body = "Your network connection is struggling to keep up,\n" + \
                        "consider lowering the bandwidth limit,\n" + \
                        "or turning off automatic network congestion management.\n" + \
                        "Choosing 'ignore' will silence all further warnings."
                actions = []
                if self.bandwidth_limit==0 or self.bandwidth_limit>MIN_BANDWIDTH:
                    actions += ["lower-bandwidth", "Lower bandwidth limit"]
                actions += ["bandwidth-off", "Turn off"]
                #if self.default_min_quality>10:
                #    actions += ["lower-quality", "Lower quality"]
                actions += ["ignore", "Ignore"]
                hints = {}
                self.may_notify(nid, summary, body, actions, hints,
                                icon_name="connect", user_callback=self.congestion_notification_callback)

    def congestion_notification_callback(self, nid, action_id):
        bandwidthlog("congestion_notification_callback(%i, %s)", nid, action_id)
        if action_id=="lower-bandwidth":
            bandwidth_limit = 50*1024*1024
            if self.bandwidth_limit>256*1024:
                bandwidth_limit = self.bandwidth_limit//2
            css = 50*1024*1024
            if self.statistics.avg_congestion_send_speed>256*1024:
                #round up:
                css = int(self.statistics.avg_congestion_send_speed//16/1024)*16*1024
            self.bandwidth_limit = max(MIN_BANDWIDTH, min(bandwidth_limit, css))
            self.setting_changed("bandwidth-limit", self.bandwidth_limit)
        #elif action_id=="lower-quality":
        #    self.default_min_quality = max(1, self.default_min_quality-15)
        #    self.set_min_quality(self.default_min_quality)
        #    self.setting_changed("min-quality", self.default_min_quality)
        elif action_id=="bandwidth-off":
            self.bandwidth_detection = False
        elif action_id=="ignore":
            self.bandwidth_warnings = False
