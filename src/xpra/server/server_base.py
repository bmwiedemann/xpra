# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2018 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os.path
from time import sleep

from xpra.log import Logger
log = Logger("server")
focuslog = Logger("focus")
clientlog = Logger("client")
screenlog = Logger("screen")
netlog = Logger("network")
metalog = Logger("metadata")
geomlog = Logger("geometry")
windowlog = Logger("window")
rpclog = Logger("rpc")
notifylog = Logger("notify")
httplog = Logger("http")
bandwidthlog = Logger("bandwidth")
timeoutlog = Logger("timeout")

from xpra.server.server_core import ServerCore, get_thread_info
from xpra.server.mixins.server_base_controlcommands import ServerBaseControlCommands
from xpra.server.mixins.notification_forwarder import NotificationForwarder
from xpra.server.mixins.webcam_server import WebcamServer
from xpra.server.mixins.clipboard_server import ClipboardServer
from xpra.server.mixins.audio_server import AudioServer
from xpra.server.mixins.fileprint_server import FilePrintServer
from xpra.server.mixins.mmap_server import MMAP_Server
from xpra.server.mixins.input_server import InputServer
from xpra.server.mixins.child_command_server import ChildCommandServer
from xpra.server.mixins.dbusrpc_server import DBUS_RPC_Server
from xpra.server.mixins.encoding_server import EncodingServer

from xpra.simple_stats import std_unit
from xpra.os_util import thread, livefds, monotonic_time, bytestostr, WIN32, POSIX, PYTHON3
from xpra.util import typedict, flatten_dict, updict, envbool, envint, log_screen_sizes, engs, iround, detect_leaks, \
    SERVER_EXIT, SERVER_ERROR, SERVER_SHUTDOWN, DETACH_REQUEST, NEW_CLIENT, DONE, IDLE_TIMEOUT, SESSION_BUSY
from xpra.net.bytestreams import set_socket_timeout
from xpra.platform.paths import get_icon_filename, get_icon_dir
from xpra.notifications.common import parse_image_path, XPRA_IDLE_NOTIFICATION_ID
from xpra.scripts.config import parse_bool_or_int, FALSE_OPTIONS
from xpra.server import EXITING_CODE
from xpra.codecs.loader import codec_versions


DETECT_MEMLEAKS = envbool("XPRA_DETECT_MEMLEAKS", False)
DETECT_FDLEAKS = envbool("XPRA_DETECT_FDLEAKS", False)
MAX_CONCURRENT_CONNECTIONS = 20
CLIENT_CAN_SHUTDOWN = envbool("XPRA_CLIENT_CAN_SHUTDOWN", True)
TERMINATE_DELAY = envint("XPRA_TERMINATE_DELAY", 1000)/1000.0
AUTO_BANDWIDTH_PCT = envint("XPRA_AUTO_BANDWIDTH_PCT", 80)
assert AUTO_BANDWIDTH_PCT>1 and AUTO_BANDWIDTH_PCT<=100, "invalid value for XPRA_AUTO_BANDWIDTH_PCT: %i" % AUTO_BANDWIDTH_PCT



"""
This is the base class for seamless and desktop servers. (not proxy servers)
It provides all the generic functions but is not tied
to a specific backend (X11 or otherwise).
See GTKServerBase/X11ServerBase and other platform specific subclasses.
"""
class ServerBase(ServerCore, ServerBaseControlCommands, NotificationForwarder, WebcamServer, ClipboardServer, AudioServer, FilePrintServer, MMAP_Server, InputServer, ChildCommandServer, DBUS_RPC_Server, EncodingServer):

    def __init__(self):
        for c in ServerBase.__bases__:
            c.__init__(self)
        log("ServerBase.__init__()")
        self.init_uuid()

        self._authenticated_packet_handlers = {}
        self._authenticated_ui_packet_handlers = {}

        # This must happen early, before loading in windows at least:
        self._server_sources = {}

        #so clients can store persistent attributes on windows:
        self.client_properties = {}

        self.randr = False

        self._window_to_id = {}
        self._id_to_window = {}
        self.window_filters = []
        # Window id 0 is reserved for "not a window"
        self._max_window_id = 1
        self.ui_driver = None

        self.sharing = None
        self.lock = None
        self.bell = False
        self.cursors = False
        self.default_dpi = 96
        self.dpi = 0
        self.xdpi = 0
        self.ydpi = 0
        self.antialias = {}
        self.cursor_size = 0
        self.idle_timeout = 0
        #duplicated from Server Source...
        self.double_click_time  = -1
        self.double_click_distance = -1, -1
        self.remote_logging = False
        self.pings = 0
        self.scaling_control = False
        self.mem_bytes = 0
        self.client_shutdown = CLIENT_CAN_SHUTDOWN

        self.init_packet_handlers()
        self.init_aliases()

        if DETECT_MEMLEAKS:
            print_leaks = detect_leaks()
            if print_leaks:
                def leak_thread():
                    while True:
                        print_leaks()
                        sleep(10)
                from xpra.make_thread import start_thread
                start_thread(leak_thread, "leak thread", daemon=True)
        if DETECT_FDLEAKS:
            self.fds = livefds()
            self.timeout_add(10, self.print_fds)

    def print_fds(self):
        fds = livefds()
        newfds = fds-self.fds
        self.fds = fds
        log.info("print_fds() new fds=%s (total=%s)", newfds, len(fds))
        return True


    def idle_add(self, *args, **kwargs):
        raise NotImplementedError()

    def timeout_add(self, *args, **kwargs):
        raise NotImplementedError()

    def source_remove(self, timer):
        raise NotImplementedError()


    def server_event(self, *args):
        for s in self._server_sources.values():
            s.send_server_event(*args)
        if self.dbus_server:
            self.dbus_server.Event(str(args[0]), [str(x) for x in args[1:]])


    def init(self, opts):
        for c in ServerBase.__bases__:
            c.init(self, opts)
        log("ServerBase.init(%s)", opts)
        self.init_options(opts)

    def init_options(self, opts):
        #from now on, use the logger for parsing errors:
        from xpra.scripts import config
        config.warn = log.warn

        self.sharing = opts.sharing
        self.lock = opts.lock
        self.bell = opts.bell
        self.cursors = opts.cursors
        self.default_dpi = int(opts.dpi)
        self.idle_timeout = opts.idle_timeout
        self.remote_logging = not ((opts.remote_logging or "").lower() in FALSE_OPTIONS)
        self.pings = opts.pings
        self.av_sync = opts.av_sync
        self.scaling_control = parse_bool_or_int("video-scaling", opts.video_scaling)


    def setup(self, opts):
        log("starting component init")
        for c in ServerBase.__bases__:
            c.setup(self, opts)

        if opts.system_tray:
            self.add_system_tray()
        self.load_existing_windows()
        thread.start_new_thread(self.threaded_init, ())

    def threaded_init(self):
        log("threaded_init() start")
        sleep(0.1)
        #re-init list of encodings now that we have video initialized
        self.init_memcheck()
        for c in ServerBase.__bases__:
            if c!=ServerCore:
                c.threaded_setup(self)
        log("threaded_init() end")


    def server_is_ready(self):
        ServerCore.server_is_ready(self)
        self.server_event("ready")


    def run(self):
        if self.pings>0:
            self.timeout_add(1000*self.pings, self.send_ping)
        return ServerCore.run(self)

    def do_cleanup(self):
        self.server_event("exit")
        for c in ServerBase.__bases__:
            if c!=ServerCore:
                c.cleanup(self)


    def init_memcheck(self):
        #verify we have enough memory:
        if POSIX and self.mem_bytes==0:
            try:
                self.mem_bytes = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')  # e.g. 4015976448
                LOW_MEM_LIMIT = 512*1024*1024
                if self.mem_bytes<=LOW_MEM_LIMIT:
                    log.warn("Warning: only %iMB total system memory available", self.mem_bytes//(1024**2))
                    log.warn(" this may not be enough to run a server")
                else:
                    log.info("%.1fGB of system memory", self.mem_bytes/(1024.0**3))
            except:
                pass


    def add_system_tray(self):
        pass

    def load_existing_windows(self):
        pass

    def is_shown(self, _window):
        return True


    ######################################################################
    # shutdown / exit commands:
    def _process_exit_server(self, _proto, _packet):
        log.info("Exiting in response to client request")
        self.cleanup_all_protocols(SERVER_EXIT)
        self.timeout_add(500, self.clean_quit, EXITING_CODE)

    def _process_shutdown_server(self, _proto, _packet):
        if not self.client_shutdown:
            log.warn("Warning: ignoring shutdown request")
            return
        log.info("Shutting down in response to client request")
        self.cleanup_all_protocols(SERVER_SHUTDOWN)
        self.timeout_add(500, self.clean_quit)


    ######################################################################
    # handle new connections:
    def handle_sharing(self, proto, ui_client=True, detach_request=False, share=False, uuid=None):
        share_count = 0
        disconnected = 0
        existing_sources = set(ss for p,ss in self._server_sources.items() if p!=proto)
        is_existing_client = uuid and any(ss.uuid==uuid for ss in existing_sources)
        log("handle_sharing%s lock=%s, sharing=%s, existing sources=%s, is existing client=%s", (proto, ui_client, detach_request, share, uuid), self.lock, self.sharing, existing_sources, is_existing_client)
        #if other clients are connected, verify we can steal or share:
        if existing_sources and not is_existing_client:
            if self.sharing is True or (self.sharing is None and share and all(ss.share for ss in existing_sources)):
                log("handle_sharing: sharing with %s", tuple(existing_sources))
            elif self.lock is True:
                self.disconnect_client(proto, SESSION_BUSY, "this session is locked")
                return False, 0, 0
            elif self.lock is not False and any(ss.lock for ss in existing_sources):
                self.disconnect_client(proto, SESSION_BUSY, "a client has locked this session")
                return False, 0, 0
        for p,ss in tuple(self._server_sources.items()):
            if detach_request and p!=proto:
                self.disconnect_client(p, DETACH_REQUEST)
                disconnected += 1
            elif uuid and ss.uuid==uuid:
                self.disconnect_client(p, NEW_CLIENT, "new connection from the same uuid")
                disconnected += 1
            elif ui_client and ss.ui_client:
                #check if existing sessions are willing to share:
                if self.sharing is True:
                    share_count += 1
                elif self.sharing is False:
                    self.disconnect_client(p, NEW_CLIENT, "this session does not allow sharing")
                    disconnected += 1
                else:
                    assert self.sharing is None
                    if not share:
                        self.disconnect_client(p, NEW_CLIENT, "the new client does not wish to share")
                        disconnected += 1
                    elif not ss.share:
                        self.disconnect_client(p, NEW_CLIENT, "this client had not enabled sharing")
                        disconnected += 1
                    else:
                        share_count += 1

        #don't accept this connection if we're going to exit-with-client:
        accepted = True
        if disconnected>0 and share_count==0 and self.exit_with_client:
            self.disconnect_client(proto, SERVER_EXIT, "last client has exited")
            accepted = False
        return accepted, share_count, disconnected

    def hello_oked(self, proto, packet, c, auth_caps):
        if ServerCore.hello_oked(self, proto, packet, c, auth_caps):
            #has been handled
            return
        if not c.boolget("steal", True) and self._server_sources:
            self.disconnect_client(proto, SESSION_BUSY, "this session is already active")
            return
        if c.boolget("screenshot_request"):
            self.send_screenshot(proto)
            return
        #added in 2.2:
        generic_request = c.strget("request")
        def is_req(mode):
            return generic_request==mode or c.boolget("%s_request" % mode, False)
        detach_request  = is_req("detach")
        stop_request    = is_req("stop_request")
        exit_request    = is_req("exit_request")
        event_request   = is_req("event_request")
        print_request   = is_req("print_request")
        is_request = detach_request or stop_request or exit_request or event_request or print_request
        if not is_request:
            #"normal" connection, so log welcome message:
            log.info("Handshake complete; enabling connection")
        else:
            log("handling request %s", generic_request)
        self.server_event("handshake-complete")

        # Things are okay, we accept this connection, and may disconnect previous one(s)
        # (but only if this is going to be a UI session - control sessions can co-exist)
        ui_client = c.boolget("ui_client", True)
        share = c.boolget("share")
        uuid = c.strget("uuid")
        accepted, share_count, disconnected = self.handle_sharing(proto, ui_client, detach_request, share, uuid)
        if not accepted:
            return

        if detach_request:
            self.disconnect_client(proto, DONE, "%i other clients have been disconnected" % disconnected)
            return

        if not is_request and ui_client:
            #a bit of explanation:
            #normally these things are synchronized using xsettings, which we handle already
            #but non-posix clients have no such thing,
            #and we don't want to expose that as an interface
            #(it's not very nice and it is very X11 specific)
            #also, clients may want to override what is in their xsettings..
            #so if the client specifies what it wants to use, we patch the xsettings with it
            #(the actual xsettings part is done in update_all_server_settings in the X11 specific subclasses)
            if share_count>0:
                log.info("sharing with %s other client(s)", share_count)
                self.dpi = 0
                self.xdpi = 0
                self.ydpi = 0
                self.double_click_time = -1
                self.double_click_distance = -1, -1
                self.antialias = {}
                self.cursor_size = 24
            else:
                self.dpi = c.intget("dpi", 0)
                self.xdpi = c.intget("dpi.x", 0)
                self.ydpi = c.intget("dpi.y", 0)
                self.double_click_time = c.intget("double_click.time", -1)
                self.double_click_distance = c.intpair("double_click.distance", (-1, -1))
                self.antialias = c.dictget("antialias")
                self.cursor_size = c.intget("cursor.size", 0)
            screenlog("dpi=%s, dpi.x=%s, dpi.y=%s, double_click_time=%s, double_click_distance=%s, antialias=%s, cursor_size=%s", self.dpi, self.xdpi, self.ydpi, self.double_click_time, self.double_click_distance, self.antialias, self.cursor_size)
            #if we're not sharing, reset all the settings:
            reset = share_count==0
            self.update_all_server_settings(reset)

        self.accept_client(proto, c)
        #use blocking sockets from now on:
        if not (PYTHON3 and WIN32):
            set_socket_timeout(proto._conn, None)

        def drop_client(reason="unknown", *args):
            self.disconnect_client(proto, reason, *args)
        def get_window_id(wid):
            return self._window_to_id.get(wid)
        bandwidth_limit = self.get_client_bandwidth_limit(proto)
        ServerSourceClass = self.get_server_source_class()
        ss = ServerSourceClass(proto, drop_client,
                          self.idle_add, self.timeout_add, self.source_remove, self.setting_changed,
                          self.idle_timeout, self.idle_timeout_cb, self.idle_grace_timeout_cb,
                          self._socket_dir, self.unix_socket_paths, not is_request, self.dbus_control,
                          self.get_transient_for, self.get_focus, self.get_cursor_data,
                          get_window_id,
                          self.window_filters,
                          self.file_transfer,
                          self.supports_mmap, self.mmap_filename,
                          bandwidth_limit,
                          self.av_sync,
                          self.core_encodings, self.encodings, self.default_encoding, self.scaling_control,
                          self.sound_properties,
                          self.sound_source_plugin,
                          self.supports_speaker, self.supports_microphone,
                          self.speaker_codecs, self.microphone_codecs,
                          self.default_quality, self.default_min_quality,
                          self.default_speed, self.default_min_speed)
        log("process_hello serversource=%s", ss)
        try:
            ss.parse_hello(c, self.min_mmap_size)
        except:
            #close it already
            ss.close()
            raise
        try:
            self.notify_new_user(ss)
        except Exception as e:
            notifylog("%s(%s)", self.notify_new_user, ss, exc_info=True)
            notifylog.error("Error: failed to show notification of user login:")
            notifylog.error(" %s", e)
        self._server_sources[proto] = ss
        #process ui half in ui thread:
        send_ui = ui_client and not is_request
        self.idle_add(self.parse_hello_ui, ss, c, auth_caps, send_ui, share_count)

    def notify_new_user(self, ss):
        #tell other users:
        notifylog("notify_new_user(%s) sources=%s", ss, self._server_sources)
        if not self._server_sources:
            return
        try:
            from xpra.notifications.common import XPRA_NEW_USER_ID
        except ImportError as e:
            notifylog("notify_new_user(%s) %s", ss, e)
        else:
            nid = XPRA_NEW_USER_ID
            icon = parse_image_path(get_icon_filename("user"))
            title = "User '%s' connected to the session" % (ss.name or ss.username or ss.uuid)
            body = "\n".join(ss.get_connect_info())
            for s in self._server_sources.values():
                s.notify("", nid, "Xpra", 0, "", title, body, [], {}, 10*1000, icon)
        
    def get_client_bandwidth_limit(self, proto):
        if self.bandwidth_limit is None:
            #auto-detect:
            pinfo = proto.get_info()
            socket_speed = pinfo.get("socket", {}).get("speed")
            if socket_speed:
                #auto: use 80% of socket speed if we have it:
                v = socket_speed*AUTO_BANDWIDTH_PCT//100 or 0
            else:
                v = 0
        else:
            v = self.bandwidth_limit
        bandwidthlog("get_client_bandwidth_limit(%s)=%s", proto, v)            
        return v

    def get_server_source_class(self):
        from xpra.server.source import ServerSource
        return ServerSource

    def reset_window_filters(self):
        self.window_filters = []

    def parse_hello_ui(self, ss, c, auth_caps, send_ui, share_count):
        #adds try:except around parse hello ui code:
        try:
            if self._closing:
                raise Exception("server is shutting down")
            self.do_parse_hello_ui(ss, c, auth_caps, send_ui, share_count)
            if self._closing:
                raise Exception("server is shutting down")
        except Exception as e:
            #log exception but don't disclose internal details to the client
            p = ss.protocol
            log("parse_hello_ui%s", (ss, c, auth_caps, send_ui, share_count), exc_info=True)
            log.error("Error: processing new connection from %s:", p or ss)
            log.error(" %s", e)
            if p:
                self.disconnect_client(p, SERVER_ERROR, "error accepting new connection")

    def do_parse_hello_ui(self, ss, c, auth_caps, send_ui, share_count):
        #process screen size (if needed)
        if send_ui:
            root_w, root_h = self.parse_screen_info(ss)
            self.parse_hello_ui_clipboard(ss, c)
            key_repeat = self.parse_hello_ui_keyboard(ss, c)
            self.parse_hello_ui_window_settings(ss, c)
            if self.notifications_forwarder:
                client_notification_actions = dict((s.uuid,s.send_notifications_actions) for s in self._server_sources.values())
                notifylog("client_notification_actions=%s", client_notification_actions)
                self.notifications_forwarder.support_actions = any(v for v in client_notification_actions.values())
        else:
            root_w, root_h = self.get_root_window_size()
            key_repeat = (0, 0)

        #send_hello will take care of sending the current and max screen resolutions
        self.send_hello(ss, root_w, root_h, key_repeat, auth_caps)

        if send_ui:
            self.send_initial_windows(ss, share_count>0)
            self.send_initial_cursors(ss, share_count>0)
        self.client_startup_complete(ss)

    def client_startup_complete(self, ss):
        ss.startup_complete()
        self.server_event("startup-complete", ss.uuid)
        if not self.start_after_connect_done:
            self.start_after_connect_done = True
            self.exec_after_connect_commands()
        self.exec_on_connect_commands()

    def sanity_checks(self, proto, c):
        server_uuid = c.strget("server_uuid")
        if server_uuid:
            if server_uuid==self.uuid:
                self.send_disconnect(proto, "cannot connect a client running on the same display that the server it connects to is managing - this would create a loop!")
                return  False
            log.warn("This client is running within the Xpra server %s", server_uuid)
        return True


    def update_all_server_settings(self, reset=False):
        pass        #may be overriden in subclasses (ie: x11 server)


    ######################################################################
    # hello:
    def get_server_features(self, server_source=None):
        #these are flags that have been added over time with new versions
        #to expose new server features:
        f = dict((k, True) for k in (
                #all these flags are assumed enabled in 0.17 (they are present in 0.14.x onwards):
                "window_refresh_config",
                "toggle_cursors_bell_notify",
                "toggle_keyboard_sync",
                "window_unmap",
                "xsettings-tuple",
                "event_request",
                "sound_sequence",
                "notify-startup-complete",
                "suspend-resume",
                "server-events",
                #newer flags:
                "window.configure.skip-geometry",
                "av-sync",
                "auto-video-encoding",
                "window-filters",
                "connection-data",
                ))
        f["sound"] = {
                      "ogg-latency-fix" : True,
                      "eos-sequence"    : True,
                      }
        f["network"] = {
                 "bandwidth-limit-change"       : True,
                 "bandwidth-limit"              : self.bandwidth_limit or 0,
                 }
        for c in ServerBase.__bases__:
            if c!=ServerCore:
                f.update(c.get_server_features(self, server_source))
        return f

    def make_hello(self, source):
        capabilities = ServerCore.make_hello(self, source)
        for c in ServerBase.__bases__:
            if c!=ServerCore:
                capabilities.update(c.get_caps(self))
        capabilities["server_type"] = "base"
        if source.wants_display:
            capabilities.update({
                 "max_desktop_size"             : self.get_max_screen_size(),
                 })
        if source.wants_features:
            capabilities.update({
                 "bell"                         : self.bell,
                 "cursors"                      : self.cursors,
                 "av-sync.enabled"              : self.av_sync,
                 "input-devices"                : self.input_devices,
                 "client-shutdown"              : self.client_shutdown,
                 "window.states"                : [],   #subclasses set this as needed
                 "sharing"                      : self.sharing is not False,
                 "sharing-toggle"               : self.sharing is None,
                 "lock"                         : self.lock is not False,
                 "lock-toggle"                  : self.lock is None,
                 })
            capabilities.update(self.file_transfer.get_file_transfer_features())
            capabilities.update(flatten_dict(self.get_server_features(source)))
        #this is a feature, but we would need the hello request
        #to know if it is really needed.. so always include it:
        capabilities["exit_server"] = True

        return capabilities

    def send_hello(self, server_source, root_w, root_h, key_repeat, server_cipher):
        capabilities = self.make_hello(server_source)
        if server_source.wants_encodings:
            updict(capabilities, "encoding", codec_versions, "version")
            for k,v in self.get_encoding_info().items():
                if k=="":
                    k = "encodings"
                else:
                    k = "encodings.%s" % k
                capabilities[k] = v
        if server_source.wants_display:
            capabilities.update({
                         "actual_desktop_size"  : (root_w, root_h),
                         "root_window_size"     : (root_w, root_h),
                         "desktop_size"         : self._get_desktop_size_capability(server_source, root_w, root_h),
                         })
        if key_repeat:
            capabilities.update({
                     "key_repeat"           : key_repeat,
                     "key_repeat_modifiers" : True})
        if server_source.wants_features:
            capabilities["remote-logging"] = self.remote_logging
            capabilities["remote-logging.multi-line"] = True
        if self._reverse_aliases and server_source.wants_aliases:
            capabilities["aliases"] = self._reverse_aliases
        if server_cipher:
            capabilities.update(server_cipher)
        server_source.send_hello(capabilities)


    def _process_logging(self, proto, packet):
        assert self.remote_logging
        ss = self._server_sources.get(proto)
        if ss is None:
            return
        level, msg = packet[1:3]
        prefix = "client "
        if len(self._server_sources)>1:
            prefix += "%3i " % ss.counter
        if len(packet)>=4:
            dtime = packet[3]
            prefix += "@%02i.%03i " % ((dtime//1000)%60, dtime%1000)
        if isinstance(msg, (tuple, list)):
            msg = " ".join(bytestostr(x) for x in msg)
        for x in bytestostr(msg).splitlines():
            clientlog.log(level, prefix+x)


    ######################################################################
    # info:
    def _process_info_request(self, proto, packet):
        log("process_info_request(%s, %s)", proto, packet)
        #ignoring the list of client uuids supplied in packet[1]
        ss = self._server_sources.get(proto)
        if not ss:
            return
        window_ids, categories = [], None
        #if len(packet>=2):
        #    uuid = packet[1]
        if len(packet)>=3:
            window_ids = packet[2]
        if len(packet)>=4:
            categories = packet[3]
        def info_callback(_proto, info):
            assert proto==_proto
            if categories:
                info = dict((k,v) for k,v in info.items() if k in categories)
            ss.send_info_response(info)
        self.get_all_info(info_callback, proto, None, window_ids)

    def send_hello_info(self, proto, flatten=True):
        start = monotonic_time()
        def cb(proto, info):
            self.do_send_info(proto, info, flatten)
            end = monotonic_time()
            log.info("processed %s info request from %s in %ims", ["structured", "flat"][flatten], proto._conn, (end-start)*1000)
        self.get_all_info(cb, proto, None, self._id_to_window.keys())

    def get_ui_info(self, _proto, _client_uuids=None, wids=None, *_args):
        """ info that must be collected from the UI thread
            (ie: things that query the display)
        """
        info = {"server"    : {"max_desktop_size"   : self.get_max_screen_size()}}
        if self.keyboard_config:
            info["keyboard"] = {"state" : {"modifiers"          : self.keyboard_config.get_current_mask()}}
        #window info:
        self.add_windows_info(info, wids)
        return info

    def get_thread_info(self, proto):
        return get_thread_info(proto, tuple(self._server_sources.keys()))


    def get_info(self, proto=None, client_uuids=None, wids=None, *args):
        log("ServerBase.get_info%s", (proto, client_uuids, wids, args))
        start = monotonic_time()
        info = ServerCore.get_info(self, proto)
        server_info = info.setdefault("server", {})
        server_info["pings"] = self.pings
        if self.mem_bytes:
            server_info["total-memory"] = self.mem_bytes
        if client_uuids:
            sources = [ss for ss in self._server_sources.values() if ss.uuid in client_uuids]
        else:
            sources = tuple(self._server_sources.values())
        if not wids:
            wids = self._id_to_window.keys()
        log("info-request: sources=%s, wids=%s", sources, wids)
        dgi = self.do_get_info(proto, sources, wids)
        #ugly alert: merge nested dictionaries,
        #ie: do_get_info may return a dictionary for "server" and we already have one,
        # so we update it with the new values
        for k,v in dgi.items():
            cval = info.get(k)
            if cval is None:
                info[k] = v
                continue
            cval.update(v)
        info.setdefault("dpi", {}).update({
                             "default"      : self.default_dpi,
                             "value"        : self.dpi,
                             "x"            : self.xdpi,
                             "y"            : self.ydpi,
                             })
        info.setdefault("antialias", {}).update(self.antialias)
        info.setdefault("cursor", {}).update({"size" : self.cursor_size})
        if self.notifications_forwarder:
            info.setdefault("notifications", {}).update(self.notifications_forwarder.get_info())
        log("ServerBase.get_info took %.1fms", 1000.0*(monotonic_time()-start))
        return info

    def get_features_info(self):
        i = {
             "randr"            : self.randr,
             "cursors"          : self.cursors,
             "bell"             : self.bell,
             "sharing"          : self.sharing is not False,
             "idle_timeout"     : self.idle_timeout,
             }
        i.update(self.get_server_features())
        return i

    def do_get_info(self, proto, server_sources=None, window_ids=None):
        start = monotonic_time()
        info = {}
        def up(prefix, d):
            info[prefix] = d

        for c in ServerBase.__bases__:
            try:
                info.update(c.get_info(self, proto))
            except Exception as e:
                log("do_get_info%s", (proto, server_sources, window_ids), exc_info=True)
                log.error("Error collecting information from %s: %s", c, e)

        up("features",  self.get_features_info())
        up("network", {
            "sharing"                      : self.sharing is not False,
            "sharing-toggle"               : self.sharing is None,
            "lock"                         : self.lock is not False,
            "lock-toggle"                  : self.lock is None,
            })
        for k,v in codec_versions.items():
            info.setdefault("encoding", {}).setdefault(k, {})["version"] = v
        # csc and video encoders:

        info.setdefault("state", {})["windows"] = len([window for window in tuple(self._id_to_window.values()) if window.is_managed()])
        # other clients:
        info["clients"] = {""                   : len([p for p in self._server_sources.keys() if p!=proto]),
                           "unauthenticated"    : len([p for p in self._potential_protocols if ((p is not proto) and (p not in self._server_sources.keys()))])}
        #find the server source to report on:
        n = len(server_sources or [])
        if n==1:
            ss = server_sources[0]
            up("client", ss.get_info())
            info.update(ss.get_window_info(window_ids))
        elif n>1:
            cinfo = {}
            for i, ss in enumerate(server_sources):
                sinfo = ss.get_info()
                sinfo["ui-driver"] = self.ui_driver==ss.uuid
                sinfo.update(ss.get_window_info(window_ids))
                cinfo[i] = sinfo
            up("client", cinfo)
        log("ServerBase.do_get_info took %ims", (monotonic_time()-start)*1000)
        return info


    ######################################################################
    # display / screen / root window:
    def set_screen_geometry_attributes(self, w, h):
        #by default, use the screen as desktop area:
        self.set_desktop_geometry_attributes(w, h)

    def set_desktop_geometry_attributes(self, w, h):
        self.calculate_desktops()
        self.calculate_workarea(w, h)
        self.set_desktop_geometry(w, h)


    def parse_screen_info(self, ss):
        return self.do_parse_screen_info(ss, ss.desktop_size)

    def do_parse_screen_info(self, ss, desktop_size):
        log("do_parse_screen_info%s", (ss, desktop_size))
        dw, dh = None, None
        if desktop_size:
            try:
                dw, dh = desktop_size
                if not ss.screen_sizes:
                    screenlog.info(" client root window size is %sx%s", dw, dh)
                else:
                    screenlog.info(" client root window size is %sx%s with %s display%s:", dw, dh, len(ss.screen_sizes), engs(ss.screen_sizes))
                    log_screen_sizes(dw, dh, ss.screen_sizes)
            except:
                dw, dh = None, None
        sw, sh = self.configure_best_screen_size()
        screenlog("configure_best_screen_size()=%s", (sw, sh))
        #we will tell the client about the size chosen in the hello we send back,
        #so record this size as the current server desktop size to avoid change notifications:
        ss.desktop_size_server = sw, sh
        #prefer desktop size, fallback to screen size:
        w = dw or sw
        h = dh or sh
        #clamp to max supported:
        maxw, maxh = self.get_max_screen_size()
        w = min(w, maxw)
        h = min(h, maxh)
        self.set_desktop_geometry_attributes(w, h)
        self.set_icc_profile()
        return w, h


    def set_icc_profile(self):
        screenlog("set_icc_profile() not implemented")

    def reset_icc_profile(self):
        screenlog("reset_icc_profile() not implemented")

    def _screen_size_changed(self, screen):
        screenlog("_screen_size_changed(%s)", screen)
        #randr has resized the screen, tell the client (if it supports it)
        w, h = screen.get_width(), screen.get_height()
        screenlog("new screen dimensions: %ix%i", w, h)
        self.set_screen_geometry_attributes(w, h)
        self.idle_add(self.send_updated_screen_size)

    def get_root_window_size(self):
        raise NotImplementedError()

    def send_updated_screen_size(self):
        max_w, max_h = self.get_max_screen_size()
        root_w, root_h = self.get_root_window_size()
        root_w = min(root_w, max_w)
        root_h = min(root_h, max_h)
        count = 0
        for ss in self._server_sources.values():
            if ss.updated_desktop_size(root_w, root_h, max_w, max_h):
                count +=1
        if count>0:
            log.info("sent updated screen size to %s client%s: %sx%s (max %sx%s)", count, engs(count), root_w, root_h, max_w, max_h)

    def get_max_screen_size(self):
        max_w, max_h = self.get_root_window_size()
        return max_w, max_h

    def _get_desktop_size_capability(self, server_source, root_w, root_h):
        client_size = server_source.desktop_size
        log("client resolution is %s, current server resolution is %sx%s", client_size, root_w, root_h)
        if not client_size:
            """ client did not specify size, just return what we have """
            return    root_w, root_h
        client_w, client_h = client_size
        w = min(client_w, root_w)
        h = min(client_h, root_h)
        return    w, h

    def configure_best_screen_size(self):
        root_w, root_h = self.get_root_window_size()
        return root_w, root_h

    def _process_desktop_size(self, proto, packet):
        width, height = packet[1:3]
        ss = self._server_sources.get(proto)
        if ss is None:
            return
        ss.desktop_size = (width, height)
        if len(packet)>=10:
            #added in 0.16 for scaled client displays:
            xdpi, ydpi = packet[8:10]
            if xdpi!=self.xdpi or ydpi!=self.ydpi:
                self.xdpi, self.ydpi = xdpi, ydpi
                screenlog("new dpi: %ix%i", self.xdpi, self.ydpi)
                self.dpi = iround((self.xdpi + self.ydpi)/2.0)
                self.dpi_changed()
        if len(packet)>=8:
            #added in 0.16 for scaled client displays:
            ss.desktop_size_unscaled = packet[6:8]
        if len(packet)>=6:
            desktops, desktop_names = packet[4:6]
            ss.set_desktops(desktops, desktop_names)
            self.calculate_desktops()
        if len(packet)>=4:
            ss.set_screen_sizes(packet[3])
        screenlog("client requesting new size: %sx%s", width, height)
        self.set_screen_size(width, height)
        if len(packet)>=4:
            screenlog.info("received updated display dimensions")
            screenlog.info("client display size is %sx%s with %s screen%s:", width, height, len(ss.screen_sizes), engs(ss.screen_sizes))
            log_screen_sizes(width, height, ss.screen_sizes)
            self.calculate_workarea(width, height)
        #ensures that DPI and antialias information gets reset:
        self.update_all_server_settings()

    def dpi_changed(self):
        pass

    def calculate_desktops(self):
        count = 1
        for ss in self._server_sources.values():
            if ss.desktops:
                count = max(count, ss.desktops)
        count = max(1, min(20, count))
        names = []
        for i in range(count):
            if i==0:
                name = "Main"
            else:
                name = "Desktop %s" % (i+1)
            for ss in self._server_sources.values():
                if ss.desktops and i<len(ss.desktop_names) and ss.desktop_names[i]:
                    name = ss.desktop_names[i]
            names.append(name)
        self.set_desktops(names)

    def set_desktops(self, names):
        pass

    def calculate_workarea(self, w, h):
        raise NotImplementedError()

    def set_workarea(self, workarea):
        pass


    def _process_server_settings(self, proto, packet):
        #only used by x11 servers
        pass


    def _set_client_properties(self, proto, wid, window, new_client_properties):
        """
        Allows us to keep window properties for a client after disconnection.
        (we keep it in a map with the client's uuid as key)
        """
        ss = self._server_sources.get(proto)
        if ss:
            ss.set_client_properties(wid, window, typedict(new_client_properties))
            #filter out encoding properties, which are expected to be set everytime:
            ncp = {}
            for k,v in new_client_properties.items():
                if v is None:
                    log.warn("removing invalid None property for %s", k)
                    continue
                if not k.startswith(b"encoding"):
                    ncp[k] = v
            if ncp:
                log("set_client_properties updating window %s of source %s with %s", wid, ss.uuid, ncp)
                client_properties = self.client_properties.setdefault(wid, {}).setdefault(ss.uuid, {})
                client_properties.update(ncp)


    ######################################################################
    # screenshots:
    def _process_screenshot(self, proto, _packet):
        packet = self.make_screenshot_packet()
        ss = self._server_sources.get(proto)
        if packet and ss:
            ss.send(*packet)

    def make_screenshot_packet(self):
        try:
            return self.do_make_screenshot_packet()
        except:
            log.error("make_screenshot_packet()", exc_info=True)
            return None

    def do_make_screenshot_packet(self):
        raise NotImplementedError("no screenshot capability in %s" % type(self))

    def send_screenshot(self, proto):
        #this is a screenshot request, handle it and disconnect
        try:
            packet = self.make_screenshot_packet()
            if not packet:
                self.send_disconnect(proto, "screenshot failed")
                return
            proto.send_now(packet)
            self.timeout_add(5*1000, self.send_disconnect, proto, "screenshot sent")
        except Exception as e:
            log.error("failed to capture screenshot", exc_info=True)
            self.send_disconnect(proto, "screenshot failed: %s" % e)


    ######################################################################
    # windows:
    def parse_hello_ui_window_settings(self, ss, c):
        pass

    def add_windows_info(self, info, window_ids):
        winfo = info.setdefault("window", {})
        for wid, window in self._id_to_window.items():
            if window_ids is not None and wid not in window_ids:
                continue
            winfo.setdefault(wid, {}).update(self.get_window_info(window))

    def get_window_info(self, window):
        from xpra.server.source import make_window_metadata
        info = {}
        for prop in window.get_property_names():
            if prop=="icon" or prop is None:
                continue
            metadata = make_window_metadata(window, prop, get_transient_for=self.get_transient_for)
            info.update(metadata)
        for prop in window.get_internal_property_names():
            metadata = make_window_metadata(window, prop)
            info.update(metadata)
        info.update({
             "override-redirect"    : window.is_OR(),
             "tray"                 : window.is_tray(),
             "size"                 : window.get_dimensions(),
             })
        wid = self._window_to_id.get(window)
        if wid:
            wprops = self.client_properties.get(wid)
            if wprops:
                info["client-properties"] = wprops
        return info

    def _update_metadata(self, window, pspec):
        metalog("updating metadata on %s: %s", window, pspec)
        wid = self._window_to_id[window]
        for ss in self._server_sources.values():
            ss.window_metadata(wid, window, pspec.name)


    def _add_new_window_common(self, window):
        props = window.get_dynamic_property_names()
        metalog("add_new_window_common(%s) watching for dynamic properties: %s", window, props)
        for prop in props:
            window.managed_connect("notify::%s" % prop, self._update_metadata)
        wid = self._max_window_id
        self._max_window_id += 1
        self._window_to_id[window] = wid
        self._id_to_window[wid] = window
        return wid

    def _do_send_new_window_packet(self, ptype, window, geometry):
        wid = self._window_to_id[window]
        for ss in self._server_sources.values():
            wprops = self.client_properties.get(wid, {}).get(ss.uuid)
            x, y, w, h = geometry
            #adjust if the transient-for window is not mapped in the same place by the client we send to:
            if "transient-for" in window.get_property_names():
                transient_for = self.get_transient_for(window)
                if transient_for>0:
                    parent = self._id_to_window.get(transient_for)
                    parent_ws = ss.get_window_source(transient_for)
                    pos = self.get_window_position(parent)
                    geomlog("transient-for=%s : %s, ws=%s, pos=%s", transient_for, parent, parent_ws, pos)
                    if parent and parent_ws and parent_ws.mapped_at and pos:
                        cx, cy = parent_ws.mapped_at[:2]
                        px, py = pos
                        x += cx-px
                        y += cy-py
            ss.new_window(ptype, wid, window, x, y, w, h, wprops)

    def _process_damage_sequence(self, proto, packet):
        packet_sequence, wid, width, height, decode_time = packet[1:6]
        if len(packet)>=7:
            message = packet[6]
        else:
            message = ""
        ss = self._server_sources.get(proto)
        if ss:
            ss.client_ack_damage(packet_sequence, wid, width, height, decode_time, message)

    def _damage(self, window, x, y, width, height, options=None):
        wid = self._window_to_id[window]
        for ss in self._server_sources.values():
            ss.damage(wid, window, x, y, width, height, options)

    def _process_buffer_refresh(self, proto, packet):
        """ can be used for requesting a refresh, or tuning batch config, or both """
        wid, _, qual = packet[1:4]
        if len(packet)>=6:
            options = typedict(packet[4])
            client_properties = packet[5]
        else:
            options = typedict({})
            client_properties = {}
        if wid==-1:
            wid_windows = self._id_to_window
        elif wid in self._id_to_window:
            wid_windows = {wid : self._id_to_window.get(wid)}
        else:
            #may have been destroyed since the request was made
            log("invalid window specified for refresh: %s", wid)
            return
        log("process_buffer_refresh for windows: %s options=%s, client_properties=%s", wid_windows, options, client_properties)
        batch_props = options.dictget("batch", {})
        if batch_props or client_properties:
            #change batch config and/or client properties
            self.update_batch_config(proto, wid_windows, typedict(batch_props), client_properties)
        #default to True for backwards compatibility:
        if options.get("refresh-now", True):
            refresh_opts = {"quality"           : qual,
                            "override_options"  : True}
            self.refresh_windows(proto, wid_windows, refresh_opts)


    def update_batch_config(self, proto, wid_windows, batch_props, client_properties):
        ss = self._server_sources.get(proto)
        if ss is None:
            return
        for wid, window in wid_windows.items():
            if window is None or not window.is_managed():
                continue
            self._set_client_properties(proto, wid, window, client_properties)
            ss.update_batch(wid, window, batch_props)

    def refresh_windows(self, proto, wid_windows, opts={}):
        ss = self._server_sources.get(proto)
        if ss is None:
            return
        for wid, window in wid_windows.items():
            if window is None or not window.is_managed():
                continue
            if not self.is_shown(window):
                log("window is no longer shown, ignoring buffer refresh which would fail")
                continue
            ss.refresh(wid, window, opts)

    def _idle_refresh_all_windows(self, proto):
        self.idle_add(self.refresh_windows, proto, self._id_to_window)


    def get_window_position(self, _window):
        #where the window is actually mapped on the server screen:
        return None

    def _window_mapped_at(self, proto, wid, window, coords=None):
        #record where a window is mapped by a client
        #(in order to support multiple clients and different offsets)
        ss = self._server_sources.get(proto)
        if not ss:
            return
        ws = ss.make_window_source(wid, window)
        ws.mapped_at = coords
        #log("window %i mapped at %s for client %s", wid, coords, ss)

    def get_transient_for(self, _window):
        return  None

    def _process_map_window(self, proto, packet):
        log.info("_process_map_window(%s, %s)", proto, packet)

    def _process_unmap_window(self, proto, packet):
        log.info("_process_unmap_window(%s, %s)", proto, packet)

    def _process_close_window(self, proto, packet):
        log.info("_process_close_window(%s, %s)", proto, packet)

    def _process_configure_window(self, proto, packet):
        log.info("_process_configure_window(%s, %s)", proto, packet)

    def _get_window_dict(self, wids):
        wd = {}
        for wid in wids:
            window = self._id_to_window.get(wid)
            if window:
                wd[wid] = window
        return wd

    def _process_suspend(self, proto, packet):
        log("suspend(%s)", packet[1:])
        ui = packet[1]
        wd = self._get_window_dict(packet[2])
        ss = self._server_sources.get(proto)
        if ss:
            ss.suspend(ui, wd)

    def _process_resume(self, proto, packet):
        log("resume(%s)", packet[1:])
        ui = packet[1]
        wd = self._get_window_dict(packet[2])
        ss = self._server_sources.get(proto)
        if ss:
            ss.resume(ui, wd)


    def send_initial_windows(self, ss, sharing=False):
        raise NotImplementedError()


    def send_initial_cursors(self, ss, sharing=False):
        pass


    ######################################################################
    # focus:
    def _process_focus(self, proto, packet):
        if self.readonly:
            return
        wid = packet[1]
        focuslog("process_focus: wid=%s", wid)
        if len(packet)>=3:
            modifiers = packet[2]
        else:
            modifiers = None
        ss = self._server_sources.get(proto)
        if ss:
            self._focus(ss, wid, modifiers)
            #if the client focused one of our windows, count this as a user event:
            if wid>0:
                ss.user_event()

    def _focus(self, _server_source, wid, modifiers):
        focuslog("_focus(%s,%s)", wid, modifiers)

    def get_focus(self):
        #can be overriden by subclasses that do manage focus
        #(ie: not shadow servers which only have a single window)
        #default: no focus
        return -1




    ######################################################################
    # settings toggle:
    def setting_changed(self, setting, value):
        #tell all the clients (that can) about the new value for this setting
        for ss in tuple(self._server_sources.values()):
            ss.send_setting_change(setting, value)

    def _process_set_cursors(self, proto, packet):
        assert self.cursors, "cannot toggle send_cursors: the feature is disabled"
        ss = self._server_sources.get(proto)
        if ss:
            ss.send_cursors = bool(packet[1])

    def _process_set_bell(self, proto, packet):
        assert self.bell, "cannot toggle send_bell: the feature is disabled"
        ss = self._server_sources.get(proto)
        if ss:
            ss.send_bell = bool(packet[1])

    def _process_set_deflate(self, proto, packet):
        level = packet[1]
        log("client has requested compression level=%s", level)
        proto.set_compression_level(level)
        #echo it back to the client:
        ss = self._server_sources.get(proto)
        if ss:
            ss.set_deflate(level)

    def _process_sharing_toggle(self, proto, packet):
        assert self.sharing is None
        ss = self._server_sources.get(proto)
        if not ss:
            return
        sharing = bool(packet[1])
        ss.share = sharing
        if not sharing:
            #disconnect other users:
            for p,ss in tuple(self._server_sources.items()):
                if p!=proto:
                    self.disconnect_client(p, DETACH_REQUEST, "client %i no longer wishes to share the session" % ss.counter)

    def _process_lock_toggle(self, proto, packet):
        assert self.lock is None
        ss = self._server_sources.get(proto)
        if ss:
            ss.lock = bool(packet[1])
            log("lock set to %s for client %i", ss.lock, ss.counter)




    ######################################################################
    # connection state:
    def _process_connection_data(self, proto, packet):
        ss = self._server_sources.get(proto)
        if ss:
            ss.update_connection_data(packet[1])

    def _process_bandwidth_limit(self, proto, packet):
        ss = self._server_sources.get(proto)
        if not ss:
            return
        bandwidth_limit = packet[1]
        if self.bandwidth_limit:
            bandwidth_limit = min(self.bandwidth_limit, bandwidth_limit)
        ss.bandwidth_limit = bandwidth_limit
        bandwidthlog.info("bandwidth-limit changed to %sbps for client %i", std_unit(bandwidth_limit), ss.counter)

    def send_ping(self):
        for ss in self._server_sources.values():
            ss.ping()
        return True

    def _process_ping_echo(self, proto, packet):
        ss = self._server_sources.get(proto)
        if ss:
            ss.process_ping_echo(packet)

    def _process_ping(self, proto, packet):
        time_to_echo = packet[1]
        ss = self._server_sources.get(proto)
        if ss:
            ss.process_ping(time_to_echo)


    ######################################################################
    # http server and http audio stream:
    def get_http_info(self):
        info = ServerCore.get_http_info(self)
        info["clients"] = len(self._server_sources)
        return info

    def get_http_scripts(self):
        scripts = ServerCore.get_http_scripts(self)
        scripts["/audio.mp3"] = self.http_audio_mp3_request
        return scripts

    def http_audio_mp3_request(self, handler):
        def err(code=500):
            handler.send_response(code)
            return None
        try:
            args_str = handler.path.split("?", 1)[1]
        except:
            return err()
        #parse args:
        args = {}
        for x in args_str.split("&"):
            v = x.split("=", 1)
            if len(v)==1:
                args[v[0]] = ""
            else:
                args[v[0]] = v[1]
        httplog("http_audio_mp3_request(%s) args(%s)=%s", handler, args_str, args)
        uuid = args.get("uuid")
        if not uuid:
            httplog.warn("Warning: http-stream audio request, missing uuid")
            return err()
        source = None
        for x in self._server_sources.values():
            if x.uuid==uuid:
                source = x
                break
        if not source:
            httplog.warn("Warning: no client matching uuid '%s'", uuid)
            return err()
        state = {}
        def new_buffer(_sound_source, data, _metadata, packet_metadata=[]):
            if not state.get("started"):
                httplog.warn("buffer received but stream is not started yet")
                err()
                source.stop_sending_sound()
                return
            count = state.get("buffers", 0)
            httplog("new_buffer [%i] for %s sound stream: %i bytes", count, state.get("codec", "?"), len(data))
            #httplog("buffer %i: %s", count, hexstr(data))
            state["buffers"] = count+1
            try:
                for x in packet_metadata:
                    handler.wfile.write(x)
                handler.wfile.write(data)
            except Exception as e:
                httplog.warn("Error: failed to send audio packet:")
                httplog.warn(" %s", e)
                source.stop_sending_sound()
                return
        def new_stream(_sound_source, codec):
            httplog("new_stream: %s", codec)
            state["started"] = True
            state["buffers"] = 0
            state["codec"] = codec
            handler.send_response(200)
            headers = {
                "Content-type"      : "audio/mpeg",
                }
            for k,v in headers.items():
                handler.send_header(k, v)
            handler.end_headers()
        def timeout_check():
            if not state.get("started"):
                err()
        if source.sound_source:
            source.stop_sending_sound()
        source.start_sending_sound("mp3", volume=1.0, new_stream=new_stream, new_buffer=new_buffer, skip_client_codec_check=True)
        self.timeout_add(1000*5, timeout_check)


    ######################################################################
    # client connections:
    def init_sockets(self, sockets):
        ServerCore.init_sockets(self, sockets)
        #verify we have a local socket for printing:
        nontcpsockets = [info for socktype, _, info in sockets if socktype=="unix-domain"]
        netlog("local sockets we can use for printing: %s", nontcpsockets)
        if not nontcpsockets and self.file_transfer.printing:
            if not WIN32:
                log.warn("Warning: no local sockets defined,")
                log.warn(" disabling printer forwarding")
            self.file_transfer.printing = False

    def force_disconnect(self, proto):
        self.cleanup_protocol(proto)
        ServerCore.force_disconnect(self, proto)

    def disconnect_protocol(self, protocol, reason, *extra):
        ServerCore.disconnect_protocol(self, protocol, reason, *extra)
        self.cleanup_protocol(protocol)

    def cleanup_protocol(self, protocol):
        netlog("cleanup_protocol(%s)", protocol)
        #this ensures that from now on we ignore any incoming packets coming
        #from this connection as these could potentially set some keys pressed, etc
        try:
            del self._potential_protocols[protocol]
        except:
            pass
        source = self._server_sources.get(protocol)
        if source:
            self.cleanup_source(source)
            try:
                del self._server_sources[protocol]
            except:
                pass
        return source

    def cleanup_source(self, source):
        had_client = len(self._server_sources)>0
        self.server_event("connection-lost", source.uuid)
        if self.ui_driver==source.uuid:
            self.ui_driver = None
        source.close()
        remaining_sources = [x for x in self._server_sources.values() if x!=source]
        netlog("cleanup_source(%s) remaining sources: %s", source, remaining_sources)
        netlog.info("xpra client %i disconnected.", source.counter)
        has_client = len(remaining_sources)>0
        if had_client and not has_client:
            self.idle_add(self.last_client_exited)

    def last_client_exited(self):
        #must run from the UI thread (modifies focus and keys)
        if self.exit_with_client:
            netlog.info("Last client has disconnected, terminating")
            self.clean_quit(False)
        else:
            self.reset_server_timeout(True)
            #so it is now safe to clear them:
            #(this may fail during shutdown - which is ok)
            try:
                self._clear_keys_pressed()
            except:
                pass
            self._focus(None, 0, [])
            self.reset_icc_profile()


    def get_all_protocols(self):
        return list(self._potential_protocols) + list(self._server_sources.keys())


    def is_timedout(self, protocol):
        v = ServerCore.is_timedout(self, protocol) and protocol not in self._server_sources
        netlog("is_timedout(%s)=%s", protocol, v)
        return v


    def idle_timeout_cb(self, source):
        timeoutlog("idle_timeout_cb(%s)", source)
        p = source.protocol
        if p:
            self.disconnect_client(p, IDLE_TIMEOUT)

    def idle_grace_timeout_cb(self, source):
        timeoutlog("idle_grace_timeout_cb(%s)", source)
        nid = XPRA_IDLE_NOTIFICATION_ID
        actions = ()
        if source.send_notifications_actions:
            actions = ("cancel", "Cancel Timeout")
        user_icon = os.path.join(get_icon_dir(), "timer.png")
        icon = parse_image_path(user_icon) or ()
        def idle_notification_action(nid, action_id):
            timeoutlog("idle_notification_action(%i, %s)", nid, action_id)
            if action_id=="cancel":
                source.user_event()
                source.no_idle()
        if self.session_name!="Xpra":
            summary = "The Xpra session %s" % self.session_name
        else:
            summary = "Xpra session"
        summary += " is about to timeout"
        body = "Unless this session sees some activity,\n" + \
               "it will be terminated soon."
        source.notify("", nid, "Xpra", 0, "", summary, body, actions, {}, source.idle_grace_duration*1000, icon, user_callback=idle_notification_action)
        source.go_idle()


    def _log_disconnect(self, proto, *args):
        #skip logging of disconnection events for server sources
        #we have tagged during hello ("info_request", "exit_request", etc..)
        ss = self._server_sources.get(proto)
        if ss and not ss.log_disconnect:
            #log at debug level only:
            netlog(*args)
            return
        ServerCore._log_disconnect(self, proto, *args)

    def _disconnect_proto_info(self, proto):
        #only log protocol info if there is more than one client:
        if len(self._server_sources)>1:
            return " %s" % proto
        return ""

    def _process_connection_lost(self, proto, packet):
        ServerCore._process_connection_lost(self, proto, packet)
        ch = self._clipboard_helper
        if ch and self._clipboard_client and self._clipboard_client.protocol==proto:
            self._clipboard_client = None
            ch.client_reset()
        self.cleanup_protocol(proto)


    ######################################################################
    # packets:
    def init_packet_handlers(self):
        for c in ServerBase.__bases__:
            c.init_packet_handlers(self)
        self._authenticated_packet_handlers.update({
            "damage-sequence":                      self._process_damage_sequence,
            "ping":                                 self._process_ping,
            "ping_echo":                            self._process_ping_echo,
            "set-cursors":                          self._process_set_cursors,
            "set-bell":                             self._process_set_bell,
            "logging":                              self._process_logging,
            "connection-data":                      self._process_connection_data,
            "bandwidth-limit":                      self._process_bandwidth_limit,
            "sharing-toggle":                       self._process_sharing_toggle,
            "lock-toggle":                          self._process_lock_toggle,
          })
        self._authenticated_ui_packet_handlers.update({
            #windows:
            "map-window":                           self._process_map_window,
            "unmap-window":                         self._process_unmap_window,
            "configure-window":                     self._process_configure_window,
            "close-window":                         self._process_close_window,
            "focus":                                self._process_focus,
            #attributes / settings:
            "server-settings":                      self._process_server_settings,
            "set_deflate":                          self._process_set_deflate,
            "desktop_size":                         self._process_desktop_size,
            "suspend":                              self._process_suspend,
            "resume":                               self._process_resume,
            #requests:
            "shutdown-server":                      self._process_shutdown_server,
            "exit-server":                          self._process_exit_server,
            "buffer-refresh":                       self._process_buffer_refresh,
            "screenshot":                           self._process_screenshot,
            "info-request":                         self._process_info_request,
            })

    def init_aliases(self):
        packet_types = list(self._default_packet_handlers.keys())
        packet_types += list(self._authenticated_packet_handlers.keys())
        packet_types += list(self._authenticated_ui_packet_handlers.keys())
        self.do_init_aliases(packet_types)

    def process_packet(self, proto, packet):
        try:
            handler = None
            packet_type = bytestostr(packet[0])
            if proto in self._server_sources:
                handler = self._authenticated_ui_packet_handlers.get(packet_type)
                if handler:
                    netlog("process ui packet %s", packet_type)
                    self.idle_add(handler, proto, packet)
                    return
                handler = self._authenticated_packet_handlers.get(packet_type)
                if handler:
                    netlog("process non-ui packet %s", packet_type)
                    handler(proto, packet)
                    return
            handler = self._default_packet_handlers.get(packet_type)
            if handler:
                netlog("process default packet %s", packet_type)
                handler(proto, packet)
                return
            def invalid_packet():
                ss = self._server_sources.get(proto)
                if not self._closing and not proto._closed and (ss is None or not ss.is_closed()):
                    netlog("invalid packet: %s", packet)
                    netlog.error("unknown or invalid packet type: %s from %s", packet_type, proto)
                if not ss:
                    proto.close()
            self.idle_add(invalid_packet)
        except KeyboardInterrupt:
            raise
        except:
            netlog.error("Unhandled error while processing a '%s' packet from peer using %s", packet_type, handler, exc_info=True)
