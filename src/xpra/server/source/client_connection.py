# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2018 Antoine Martin <antoine@xpra.org>
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from time import sleep
from threading import Event
from collections import deque

from xpra.make_thread import start_thread
from xpra.os_util import Queue, monotonic_time
from xpra.util import merge_dicts, flatten_dict, notypedict, envbool, envint, typedict, AtomicInteger
from xpra.server.source.source_stats import GlobalPerformanceStatistics
from xpra.server.source.clientinfo_mixin import ClientInfoMixin
from xpra.server import server_features
from xpra.log import Logger

CC_BASES = [ClientInfoMixin]
#TODO: notifications mixin
if server_features.clipboard:
    from xpra.server.source.clipboard_connection import ClipboardConnection
    CC_BASES.append(ClipboardConnection)
if server_features.audio:
    from xpra.server.source.audio_mixin import AudioMixin
    CC_BASES.append(AudioMixin)
if server_features.webcam:
    from xpra.server.source.webcam_mixin import WebcamMixin
    CC_BASES.append(WebcamMixin)
if server_features.fileprint:
    from xpra.server.source.fileprint_mixin import FilePrintMixin
    CC_BASES.append(FilePrintMixin)
if server_features.mmap:
    from xpra.server.source.mmap_connection import MMAP_Connection
    CC_BASES.append(MMAP_Connection)
if server_features.input_devices:
    from xpra.server.source.input_mixin import InputMixin
    CC_BASES.append(InputMixin)
if server_features.dbus:
    from xpra.server.source.dbus_mixin import DBUS_Mixin
    CC_BASES.append(DBUS_Mixin)
if server_features.network_state:
    from xpra.server.source.networkstate_mixin import NetworkStateMixin
    CC_BASES.append(NetworkStateMixin)
if server_features.display:
    from xpra.server.source.clientdisplay_mixin import ClientDisplayMixin
    CC_BASES.append(ClientDisplayMixin)
if server_features.windows:
    from xpra.server.source.windows_mixin import WindowsMixin
    CC_BASES.append(WindowsMixin)
    #must be after windows mixin so it can assume "self.send_windows" is set
    if server_features.encoding:
        from xpra.server.source.encodings_mixin import EncodingsMixin
        CC_BASES.append(EncodingsMixin)
    if server_features.audio and server_features.av_sync:
        from xpra.server.source.avsync_mixin import AVSyncMixin
        CC_BASES.append(AVSyncMixin)
from xpra.server.source.idle_mixin import IdleMixin
CC_BASES.append(IdleMixin)
CC_BASES = tuple(CC_BASES)
ClientConnectionClass = type('ClientConnectionClass', CC_BASES, {})

log = Logger("server")
elog = Logger("encoding")
keylog = Logger("keyboard")
mouselog = Logger("mouse")
timeoutlog = Logger("timeout")
proxylog = Logger("proxy")
statslog = Logger("stats")
notifylog = Logger("notify")
netlog = Logger("network")
bandwidthlog = Logger("bandwidth")

log("ClientConnectionClass%s", CC_BASES)


BANDWIDTH_DETECTION = envbool("XPRA_BANDWIDTH_DETECTION", True)
MIN_BANDWIDTH = envint("XPRA_MIN_BANDWIDTH", 5*1024*1024)
AUTO_BANDWIDTH_PCT = envint("XPRA_AUTO_BANDWIDTH_PCT", 80)
assert 1<AUTO_BANDWIDTH_PCT<=100, "invalid value for XPRA_AUTO_BANDWIDTH_PCT: %i" % AUTO_BANDWIDTH_PCT
YIELD = envbool("XPRA_YIELD", False)

counter = AtomicInteger()


"""
This class mediates between the server class (which only knows about actual window objects and display server events)
and the client specific WindowSource instances (which only know about window ids
and manage window pixel compression).
It sends messages to the client via its 'protocol' instance (the network connection),
directly for a number of cases (cursor, sound, notifications, etc)
or on behalf of the window sources for pixel data.

Strategy: if we have 'ordinary_packets' to send, send those.
When we don't, then send packets from the 'packet_queue'. (compressed pixels or clipboard data)
See 'next_packet'.

The UI thread calls damage(), which goes into WindowSource and eventually (batching may be involved)
adds the damage pixels ready for processing to the encode_work_queue,
items are picked off by the separate 'encode' thread (see 'encode_loop')
and added to the damage_packet_queue.
"""

class ClientConnection(ClientConnectionClass):

    def __init__(self, protocol, disconnect_cb, session_name, server,
                 idle_add, timeout_add, source_remove,
                 setting_changed,
                 socket_dir, unix_socket_paths, log_disconnect, bandwidth_limit, bandwidth_detection,
                 ):
        global counter
        self.counter = counter.increase()
        self.protocol = protocol
        self.connection_time = monotonic_time()
        self.close_event = Event()
        self.disconnect = disconnect_cb
        self.session_name = session_name

        for bc in CC_BASES:
            try:
                bc.__init__(self)
                bc.init_from(self, protocol, server)
            except Exception as e:
                raise Exception("failed to initialize %s: %s" % (bc, e))

        #holds actual packets ready for sending (already encoded)
        #these packets are picked off by the "protocol" via 'next_packet()'
        #format: packet, wid, pixels, start_send_cb, end_send_cb
        #(only packet is required - the rest can be 0/None for clipboard packets)
        self.packet_queue = deque()
        # the encode work queue is used by mixins that need to encode data before sending it,
        # ie: encodings and clipboard
        #this queue will hold functions to call to compress data (pixels, clipboard)
        #items placed in this queue are picked off by the "encode" thread,
        #the functions should add the packets they generate to the 'packet_queue'
        self.encode_work_queue = None
        self.encode_thread = None
        self.ordinary_packets = []
        self.socket_dir = socket_dir
        self.unix_socket_paths = unix_socket_paths
        self.log_disconnect = log_disconnect
        self.idle_add = idle_add
        self.timeout_add = timeout_add
        self.source_remove = source_remove

        self.setting_changed = setting_changed
        # network constraints:
        self.server_bandwidth_limit = bandwidth_limit
        self.bandwidth_detection = bandwidth_detection

        #these statistics are shared by all WindowSource instances:
        self.statistics = GlobalPerformanceStatistics()

        self.init()

        # ready for processing:
        protocol.set_packet_source(self.next_packet)


    def __repr__(self):
        return  "%s(%i : %s)" % (type(self).__name__, self.counter, self.protocol)

    def init(self):
        self.hello_sent = False
        self.info_namespace = False
        self.send_notifications = False
        self.send_notifications_actions = False
        self.notification_callbacks = {}
        self.share = False
        self.lock = False
        self.control_commands = ()
        self.bandwidth_limit = self.server_bandwidth_limit
        self.soft_bandwidth_limit = self.bandwidth_limit
        self.bandwidth_warnings = True
        self.bandwidth_warning_time = 0
        #what we send back in hello packet:
        self.ui_client = True
        self.wants_aliases = True
        self.wants_encodings = True
        self.wants_versions = True
        self.wants_features = True
        self.wants_display = True
        self.wants_events = False
        self.wants_default_cursor = False
        for c in CC_BASES:
            c.init_state(self)


    def is_closed(self):
        return self.close_event.isSet()

    def close(self):
        log("%s.close()", self)
        for c in CC_BASES:
            c.cleanup(self)
        self.close_event.set()
        self.protocol = None


    def update_bandwidth_limits(self):
        if not self.bandwidth_detection:
            return
        mmap_size = getattr(self, "mmap_size", 0)
        if mmap_size>0:
            return
        #calculate soft bandwidth limit based on send congestion data:
        bandwidth_limit = 0
        if BANDWIDTH_DETECTION:
            bandwidth_limit = self.statistics.avg_congestion_send_speed
            bandwidthlog("avg_congestion_send_speed=%s", bandwidth_limit)
            if bandwidth_limit>20*1024*1024:
                #ignore congestion speed if greater 20Mbps
                bandwidth_limit = 0
        if (self.bandwidth_limit or 0)>0:
            #command line options could overrule what we detect?
            bandwidth_limit = min(self.bandwidth_limit, bandwidth_limit)
        if bandwidth_limit>0:
            bandwidth_limit = max(MIN_BANDWIDTH, bandwidth_limit)
        self.soft_bandwidth_limit = bandwidth_limit
        bandwidthlog("update_bandwidth_limits() bandwidth_limit=%s, soft bandwidth limit=%s",
                     self.bandwidth_limit, bandwidth_limit)
        #figure out how to distribute the bandwidth amongst the windows,
        #we use the window size,
        #(we should use the number of bytes actually sent: framerate, compression, etc..)
        window_weight = {}
        for wid, ws in self.window_sources.items():
            weight = 0
            if not ws.suspended:
                ww, wh = ws.window_dimensions
                #try to reserve bandwidth for at least one screen update,
                #and add the number of pixels damaged:
                weight = ww*wh + ws.statistics.get_damage_pixels()
            window_weight[wid] = weight
        bandwidthlog("update_bandwidth_limits() window weights=%s", window_weight)
        total_weight = max(1, sum(window_weight.values()))
        for wid, ws in self.window_sources.items():
            if bandwidth_limit==0:
                ws.bandwidth_limit = 0
            else:
                weight = window_weight.get(wid, 0)
                ws.bandwidth_limit = max(MIN_BANDWIDTH//10, bandwidth_limit*weight//total_weight)


    def parse_hello(self, c):
        self.ui_client = c.boolget("ui_client", True)
        self.wants_encodings = c.boolget("wants_encodings", self.ui_client)
        self.wants_display = c.boolget("wants_display", self.ui_client)
        self.wants_events = c.boolget("wants_events", False)
        self.wants_aliases = c.boolget("wants_aliases", True)
        self.wants_versions = c.boolget("wants_versions", True)
        self.wants_features = c.boolget("wants_features", True)
        self.wants_default_cursor = c.boolget("wants_default_cursor", False)

        for mixin in CC_BASES:
            mixin.parse_client_caps(self, c)

        #general features:
        self.info_namespace = c.boolget("info-namespace")
        self.send_notifications = c.boolget("notifications")
        self.send_notifications_actions = c.boolget("notifications.actions")
        log("notifications=%s, actions=%s", self.send_notifications, self.send_notifications_actions)
        self.share = c.boolget("share")
        self.lock = c.boolget("lock")
        self.control_commands = c.strlistget("control_commands")
        bandwidth_limit = c.intget("bandwidth-limit", 0)
        server_bandwidth_limit = self.server_bandwidth_limit
        if self.server_bandwidth_limit is None:
            server_bandwidth_limit = self.get_socket_bandwidth_limit() or bandwidth_limit
        self.bandwidth_limit = min(server_bandwidth_limit, bandwidth_limit)
        if self.bandwidth_detection:
            self.bandwidth_detection = c.boolget("bandwidth-detection", True)
        cd = typedict(c.dictget("connection-data"))
        self.jitter = cd.intget("jitter", 0)
        bandwidthlog("server bandwidth-limit=%s, client bandwidth-limit=%s, value=%s, detection=%s",
                     server_bandwidth_limit, bandwidth_limit, self.bandwidth_limit, self.bandwidth_detection)

        cinfo = self.get_connect_info()
        for i,ci in enumerate(cinfo):
            log.info("%s%s", ["", " "][int(i>0)], ci)
        if self.client_proxy:
            from xpra.version_util import version_compat_check
            msg = version_compat_check(self.proxy_version)
            if msg:
                proxylog.warn("Warning: proxy version may not be compatible: %s", msg)
        self.update_connection_data(c.dictget("connection-data"))
        if getattr(self, "mmap_size", 0)>0:
            log("mmap enabled, ignoring bandwidth-limit")
            self.bandwidth_limit = 0
        #adjust max packet size if file transfers are enabled:
        #TODO: belongs in mixin:
        file_transfer = getattr(self, "file_transfer", None)
        if file_transfer:
            self.protocol.max_packet_size = max(self.protocol.max_packet_size, self.file_size_limit*1024*1024)

    def get_socket_bandwidth_limit(self):
        p = self.protocol
        if not p:
            return 0
        #auto-detect:
        pinfo = p.get_info()
        socket_speed = pinfo.get("socket", {}).get("device", {}).get("speed")
        if not socket_speed:
            return 0
        bandwidthlog("get_socket_bandwidth_limit() socket_speed=%s", socket_speed)
        #auto: use 80% of socket speed if we have it:
        return socket_speed*AUTO_BANDWIDTH_PCT//100 or 0


    def startup_complete(self):
        log("startup_complete()")
        self.send("startup-complete")


    #
    # The encode thread loop management:
    #
    def queue_encode(self, item):
        #start the encode work queue:
        #holds functions to call to compress data (pixels, clipboard)
        #items placed in this queue are picked off by the "encode" thread,
        #the functions should add the packets they generate to the 'packet_queue'
        self.encode_work_queue = Queue()
        self.queue_encode = self.encode_work_queue.put
        self.queue_encode(item)
        self.encode_thread = start_thread(self.encode_loop, "encode")

    def encode_queue_size(self):
        ewq = self.encode_work_queue
        if ewq is None:
            return 0
        return ewq.qsize()

    def call_in_encode_thread(self, *fn_and_args):
        """
            This is used by WindowSource to queue damage processing to be done in the 'encode' thread.
            The 'encode_and_send_cb' will then add the resulting packet to the 'packet_queue' via 'queue_packet'.
        """
        self.statistics.compression_work_qsizes.append((monotonic_time(), self.encode_queue_size()))
        self.queue_encode(fn_and_args)

    def queue_packet(self, packet, wid=0, pixels=0,
                     start_send_cb=None, end_send_cb=None, fail_cb=None, wait_for_more=False):
        """
            Add a new 'draw' packet to the 'packet_queue'.
            Note: this code runs in the non-ui thread
        """
        now = monotonic_time()
        self.statistics.packet_qsizes.append((now, len(self.packet_queue)))
        if wid>0:
            self.statistics.damage_packet_qpixels.append(
                (now, wid, sum(x[2] for x in tuple(self.packet_queue) if x[1]==wid))
                )
        self.packet_queue.append((packet, wid, pixels, start_send_cb, end_send_cb, fail_cb, wait_for_more))
        p = self.protocol
        if p:
            p.source_has_more()

    def encode_loop(self):
        """
            This runs in a separate thread and calls all the function callbacks
            which are added to the 'encode_work_queue'.
            Must run until we hit the end of queue marker,
            to ensure all the queued items get called,
            those that are marked as optional will be skipped when is_closed()
        """
        while True:
            fn_and_args = self.encode_work_queue.get(True)
            if fn_and_args is None:
                return              #empty marker
            #some function calls are optional and can be skipped when closing:
            #(but some are not, like encoder clean functions)
            optional_when_closing = fn_and_args[0]
            if optional_when_closing and self.is_closed():
                continue
            try:
                fn_and_args[1](*fn_and_args[2:])
            except Exception as e:
                if self.is_closed():
                    log("ignoring encoding error in %s as source is already closed:", fn_and_args[0])
                    log(" %s", e)
                else:
                    log.error("Error during encoding:", exc_info=True)
                del e
            if YIELD:
                sleep(0)

    ######################################################################
    # network:
    def next_packet(self):
        """ Called by protocol.py when it is ready to send the next packet """
        packet, start_send_cb, end_send_cb, fail_cb = None, None, None, None
        synchronous, have_more, will_have_more = True, False, False
        if not self.is_closed():
            if self.ordinary_packets:
                packet, synchronous, fail_cb, will_have_more = self.ordinary_packets.pop(0)
            elif self.packet_queue:
                packet, _, _, start_send_cb, end_send_cb, fail_cb, will_have_more = self.packet_queue.popleft()
            have_more = packet is not None and (self.ordinary_packets or self.packet_queue)
        return packet, start_send_cb, end_send_cb, fail_cb, synchronous, have_more, will_have_more

    def send(self, *parts, **kwargs):
        """ This method queues non-damage packets (higher priority) """
        synchronous = kwargs.get("synchronous", True)
        will_have_more = kwargs.get("will_have_more", not synchronous)
        fail_cb = kwargs.get("fail_cb", None)
        p = self.protocol
        if p:
            self.ordinary_packets.append((parts, synchronous, fail_cb, will_have_more))
            p.source_has_more()

    def send_more(self, *parts, **kwargs):
        kwargs["will_have_more"] = True
        self.send(*parts, **kwargs)

    def send_async(self, *parts, **kwargs):
        kwargs["synchronous"] = False
        self.send(*parts, **kwargs)


    #client tells us about network connection status:
    def update_connection_data(self, data):
        netlog("update_connection_data(%s)", data)
        self.client_connection_data = data


    def send_hello(self, server_capabilities):
        capabilities = server_capabilities.copy()
        for bc in CC_BASES:
            merge_dicts(capabilities, bc.get_caps(self))
        self.send("hello", capabilities)
        self.hello_sent = True


    ######################################################################
    # info:
    def get_info(self):
        info = {
                "protocol"          : "xpra",
                "connection_time"   : int(self.connection_time),
                "elapsed_time"      : int(monotonic_time()-self.connection_time),
                "counter"           : self.counter,
                "hello-sent"        : self.hello_sent,
                "jitter"            : self.jitter,
                "bandwidth-limit"   : {
                    "setting"       : self.bandwidth_limit or 0,
                    "detection"     : self.bandwidth_detection,
                    "actual"        : self.soft_bandwidth_limit or 0,
                    }
                }
        p = self.protocol
        if p:
            info.update({
                         "connection"       : p.get_info(),
                         })
        info.update(self.get_features_info())
        for bc in CC_BASES:
            try:
                merge_dicts(info, bc.get_info(self))
            except Exception as e:
                log("merge_dicts on %s", bc, exc_info=True)
                log.error("Error: cannot add information from %s:", bc)
                log.error(" %s", e)
        return info

    def get_features_info(self):
        info = {
            "lock"  : bool(self.lock),
            "share" : bool(self.share),
            "notifications" : self.send_notifications,
            }
        return info


    def send_info_response(self, info):
        if self.info_namespace:
            v = notypedict(info)
        else:
            v = flatten_dict(info)
        self.send_async("info-response", v)


    def send_setting_change(self, setting, value):
        if self.client_setting_change:
            self.send_more("setting-change", setting, value)


    def send_server_event(self, *args):
        if self.wants_events:
            self.send_more("server-event", *args)


    ######################################################################
    # notifications:
    # Utility functions for mixins (makes notifications optional)
    def may_notify(self, nid, summary, body, actions=(), hints={}, expire_timeout=10*1000,
                   icon_name=None, user_callback=None):
        try:
            from xpra.platform.paths import get_icon_filename
            from xpra.notifications.common import parse_image_path
        except ImportError as e:
            notifylog("not sending notification: %s", e)
        else:
            icon_filename = get_icon_filename(icon_name)
            icon = parse_image_path(icon_filename) or ""
            self.notify("", nid, "Xpra", 0, "", summary, body, actions, hints, expire_timeout, icon, user_callback)

    def notify(self, dbus_id, nid, app_name, replaces_nid, app_icon,
               summary, body, actions, hints, expire_timeout, icon, user_callback=None):
        args = (dbus_id, nid, app_name, replaces_nid, app_icon, summary, body, actions, hints, expire_timeout, icon)
        notifylog("notify%s types=%s", args, tuple(type(x) for x in args))
        if not self.send_notifications:
            notifylog("client %s does not support notifications", self)
            return False
        if self.suspended:
            notifylog("client %s is suspended, notification not sent", self)
            return False
        if user_callback:
            self.notification_callbacks[nid] = user_callback
        #this is one of the few places where we actually do care about character encoding:
        try:
            summary = summary.encode("utf8")
        except UnicodeEncodeError:
            summary = str(summary)
        try:
            body = body.encode("utf8")
        except UnicodeEncodeError:
            body = str(body)
        if self.hello_sent:
            #Warning: actions and hints are send last because they were added later (in version 2.3)
            self.send_async("notify_show", dbus_id, nid, app_name, replaces_nid, app_icon,
                            summary, body, expire_timeout, icon, actions, hints)
        return True

    def notify_close(self, nid):
        if not self.send_notifications or self.suspended  or not self.hello_sent:
            return
        self.send_more("notify_close", nid)


    def set_deflate(self, level):
        self.send("set_deflate", level)


    def send_client_command(self, *args):
        if self.hello_sent:
            self.send_more("control", *args)


    def rpc_reply(self, *args):
        if self.hello_sent:
            self.send("rpc-reply", *args)
