# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2019 Antoine Martin <antoine@xpra.org>
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
from threading import Thread, Lock

from xpra.server.server_core import ServerCore, get_thread_info
from xpra.server.mixins.server_base_controlcommands import ServerBaseControlCommands
from xpra.net.common import may_log_packet
from xpra.os_util import monotonic_time, bytestostr, strtobytes, WIN32
from xpra.util import (
    typedict, flatten_dict, updict, merge_dicts, envbool, envint,
    SERVER_EXIT, SERVER_ERROR, SERVER_SHUTDOWN, DETACH_REQUEST,
    NEW_CLIENT, DONE, SESSION_BUSY,
    )
from xpra.net.bytestreams import set_socket_timeout
from xpra.server import server_features
from xpra.server import EXITING_CODE
from xpra.log import Logger

SERVER_BASES = [ServerCore, ServerBaseControlCommands]
if server_features.notifications:
    from xpra.server.mixins.notification_forwarder import NotificationForwarder
    SERVER_BASES.append(NotificationForwarder)
if server_features.webcam:
    from xpra.server.mixins.webcam_server import WebcamServer
    SERVER_BASES.append(WebcamServer)
if server_features.clipboard:
    from xpra.server.mixins.clipboard_server import ClipboardServer
    SERVER_BASES.append(ClipboardServer)
if server_features.audio:
    from xpra.server.mixins.audio_server import AudioServer
    SERVER_BASES.append(AudioServer)
if server_features.fileprint:
    from xpra.server.mixins.fileprint_server import FilePrintServer
    SERVER_BASES.append(FilePrintServer)
if server_features.mmap:
    from xpra.server.mixins.mmap_server import MMAP_Server
    SERVER_BASES.append(MMAP_Server)
if server_features.input_devices:
    from xpra.server.mixins.input_server import InputServer
    SERVER_BASES.append(InputServer)
if server_features.commands:
    from xpra.server.mixins.child_command_server import ChildCommandServer
    SERVER_BASES.append(ChildCommandServer)
if server_features.dbus:
    from xpra.server.mixins.dbusrpc_server import DBUS_RPC_Server
    SERVER_BASES.append(DBUS_RPC_Server)
if server_features.encoding:
    from xpra.server.mixins.encoding_server import EncodingServer
    SERVER_BASES.append(EncodingServer)
if server_features.logging:
    from xpra.server.mixins.logging_server import LoggingServer
    SERVER_BASES.append(LoggingServer)
if server_features.network_state:
    from xpra.server.mixins.networkstate_server import NetworkStateServer
    SERVER_BASES.append(NetworkStateServer)
if server_features.display:
    from xpra.server.mixins.display_manager import DisplayManager
    SERVER_BASES.append(DisplayManager)
if server_features.windows:
    from xpra.server.mixins.window_server import WindowServer
    SERVER_BASES.append(WindowServer)
SERVER_BASES = tuple(SERVER_BASES)
ServerBaseClass = type('ServerBaseClass', SERVER_BASES, {})

log = Logger("server")
netlog = Logger("network")
httplog = Logger("http")
timeoutlog = Logger("timeout")
screenlog = Logger("screen")

log("ServerBaseClass%s", SERVER_BASES)

CLIENT_CAN_SHUTDOWN = envbool("XPRA_CLIENT_CAN_SHUTDOWN", True)
INIT_THREAD_TIMEOUT = envint("XPRA_INIT_THREAD_TIMEOUT", 10)
MDNS_CLIENT_COUNT = envbool("XPRA_MDNS_CLIENT_COUNT", True)


"""
This is the base class for seamless and desktop servers. (not proxy servers)
It provides all the generic functions but is not tied
to a specific backend (X11 or otherwise).
See GTKServerBase/X11ServerBase and other platform specific subclasses.
"""
class ServerBase(ServerBaseClass):

    def __init__(self):
        for c in SERVER_BASES:
            c.__init__(self)
        log("ServerBase.__init__()")
        self.init_uuid()

        self._authenticated_packet_handlers = {}
        self._authenticated_ui_packet_handlers = {}

        self.display_pid = 0
        self._server_sources = {}
        self.client_properties = {}
        self.ui_driver = None
        self.sharing = None
        self.lock = None
        self.init_thread = None
        self.init_thread_callbacks = []
        self.init_thread_lock = Lock()

        self.idle_timeout = 0
        #duplicated from Server Source...
        self.client_shutdown = CLIENT_CAN_SHUTDOWN
        self.mp3_stream_check_timer = None

        self.init_packet_handlers()
        self.init_aliases()


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

    def get_server_source(self, proto):
        return self._server_sources.get(proto)


    def init(self, opts):
        #from now on, use the logger for parsing errors:
        from xpra.scripts import config
        config.warn = log.warn
        for c in SERVER_BASES:
            start = monotonic_time()
            c.init(self, opts)
            end = monotonic_time()
            log("%3ims in %s.init", 1000*(end-start), c)
        self.sharing = opts.sharing
        self.lock = opts.lock
        self.idle_timeout = opts.idle_timeout
        self.bandwidth_detection = opts.bandwidth_detection

    def setup(self):
        log("starting component init")
        for c in SERVER_BASES:
            start = monotonic_time()
            c.setup(self)
            end = monotonic_time()
            log("%3ims in %s.setup", 1000*(end-start), c)
        self.init_thread = Thread(target=self.threaded_init)
        self.init_thread.start()

    def threaded_init(self):
        log("threaded_init() start")
        from xpra.platform import threaded_server_init
        threaded_server_init()
        for c in SERVER_BASES:
            if c!=ServerCore:
                try:
                    c.threaded_setup(self)
                except Exception:
                    log.error("Error during threaded setup of %s", c, exc_info=True)
        #populate the platform info cache:
        from xpra.version_util import get_platform_info
        get_platform_info()
        with self.init_thread_lock:
            for cb in self.init_thread_callbacks:
                try:
                    cb()
                except Exception as e:
                    log("threaded_init()", exc_info=True)
                    log.error("Error in initialization thread callback %s", cb)
                    log.error(" %s", e)
        log("threaded_init() end")

    def after_threaded_init(self, callback):
        with self.init_thread_lock:
            if not self.init_thread or not self.init_thread.is_alive():
                callback()
            else:
                self.init_thread_callbacks.append(callback)

    def wait_for_threaded_init(self):
        if not self.init_thread:
            #looks like we didn't make it as far as calling setup()
            log("wait_for_threaded_init() no init thread")
            return
        log("wait_for_threaded_init() %s.is_alive()=%s", self.init_thread, self.init_thread.is_alive())
        if self.init_thread.is_alive():
            log.info("waiting for initialization thread to complete")
            self.init_thread.join(INIT_THREAD_TIMEOUT)
            if self.init_thread.is_alive():
                log.warn("Warning: initialization thread is still active")


    def server_is_ready(self):
        ServerCore.server_is_ready(self)
        self.server_event("ready")


    def do_cleanup(self):
        self.server_event("exit")
        self.wait_for_threaded_init()
        self.cancel_mp3_stream_check_timer()
        for c in SERVER_BASES:
            if c!=ServerCore:
                c.cleanup(self)


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


    def get_mdns_info(self) -> dict:
        mdns_info = ServerCore.get_mdns_info(self)
        if MDNS_CLIENT_COUNT:
            mdns_info["clients"] = len(self._server_sources)
        return mdns_info


    ######################################################################
    # handle new connections:
    def handle_sharing(self, proto, ui_client=True, detach_request=False, share=False, uuid=None):
        share_count = 0
        disconnected = 0
        existing_sources = set(ss for p,ss in self._server_sources.items() if p!=proto)
        is_existing_client = uuid and any(ss.uuid==uuid for ss in existing_sources)
        log("handle_sharing%s lock=%s, sharing=%s, existing sources=%s, is existing client=%s",
            (proto, ui_client, detach_request, share, uuid),
            self.lock, self.sharing, existing_sources, is_existing_client)
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
            elif uuid and ss.uuid==uuid and ui_client and ss.ui_client:
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
                self.antialias = c.dictget("antialias", {})
                self.cursor_size = c.intget("cursor.size", 0)
            #FIXME: this belongs in DisplayManager!
            screenlog("dpi=%s, dpi.x=%s, dpi.y=%s, antialias=%s, cursor_size=%s",
                      self.dpi, self.xdpi, self.ydpi, self.antialias, self.cursor_size)
            log("double-click time=%s, distance=%s", self.double_click_time, self.double_click_distance)
            #if we're not sharing, reset all the settings:
            reset = share_count==0
            self.update_all_server_settings(reset)

        self.accept_client(proto, c)
        #use blocking sockets from now on:
        if not WIN32:
            set_socket_timeout(proto._conn, None)

        def drop_client(reason="unknown", *args):
            self.disconnect_client(proto, reason, *args)
        cc_class = self.get_client_connection_class(c)
        ss = cc_class(proto, drop_client,
                      self.session_name, self,
                      self.idle_add, self.timeout_add, self.source_remove,
                      self.setting_changed,
                      self._socket_dir, self.unix_socket_paths, not is_request,
                      self.bandwidth_limit, self.bandwidth_detection,
                      )
        log("process_hello clientconnection=%s", ss)
        try:
            ss.parse_hello(c)
        except:
            #close it already
            ss.close()
            raise
        self._server_sources[proto] = ss
        self.mdns_update()
        #process ui half in ui thread:
        send_ui = ui_client and not is_request
        self.idle_add(self._process_hello_ui, ss, c, auth_caps, send_ui, share_count)

    def get_client_connection_class(self, caps):
        from xpra.server.source.client_connection_factory import get_client_connection_class
        return get_client_connection_class(caps)


    def _process_hello_ui(self, ss, c, auth_caps, send_ui : bool, share_count : int):
        #adds try:except around parse hello ui code:
        try:
            if self._closing:
                raise Exception("server is shutting down")

            self.notify_new_user(ss)

            self.parse_hello(ss, c, send_ui)
            #send_hello will take care of sending the current and max screen resolutions
            root_w, root_h = self.get_root_window_size()
            self.send_hello(ss, root_w, root_h, auth_caps)
            self.add_new_client(ss, c, send_ui, share_count)
            self.send_initial_data(ss, c, send_ui, share_count)
            self.client_startup_complete(ss)

            if self._closing:
                raise Exception("server is shutting down")
        except Exception as e:
            #log exception but don't disclose internal details to the client
            p = ss.protocol
            log("_process_hello_ui%s", (ss, c, auth_caps, send_ui, share_count), exc_info=True)
            log.error("Error: processing new connection from %s:", p or ss)
            log.error(" %s", e)
            if p:
                self.disconnect_client(p, SERVER_ERROR, "error accepting new connection")

    def parse_hello(self, ss, c, send_ui):
        for bc in SERVER_BASES:
            if bc!=ServerCore:
                bc.parse_hello(self, ss, c, send_ui)

    def add_new_client(self, ss, c, send_ui, share_count):
        for bc in SERVER_BASES:
            if bc!=ServerCore:
                bc.add_new_client(self, ss, c, send_ui, share_count)

    def send_initial_data(self, ss, c, send_ui, share_count):
        for bc in SERVER_BASES:
            if bc!=ServerCore:
                bc.send_initial_data(self, ss, c, send_ui, share_count)

    def client_startup_complete(self, ss):
        ss.startup_complete()
        self.server_event("startup-complete", ss.uuid)
        if not self.start_after_connect_done:   #pylint: disable=access-member-before-definition
            self.start_after_connect_done = True
            self.exec_after_connect_commands()
        self.exec_on_connect_commands()

    def sanity_checks(self, proto, c):
        server_uuid = c.strget("server_uuid")
        if server_uuid:
            if server_uuid==self.uuid:
                self.send_disconnect(proto, "cannot connect a client running on the same display"
                                     +" that the server it connects to is managing - this would create a loop!")
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
        f = {
            "toggle_keyboard_sync" : True,  #v4.0 clients assume this is always available
            }
        for c in SERVER_BASES:
            if c!=ServerCore:
                merge_dicts(f, c.get_server_features(self, server_source))
        return f

    def make_hello(self, source):
        capabilities = super().make_hello(source)
        for c in SERVER_BASES:
            if c!=ServerCore:
                merge_dicts(capabilities, c.get_caps(self, source))
        capabilities["server_type"] = "base"
        if source.wants_display:
            capabilities.update({
                 "max_desktop_size"             : self.get_max_screen_size(),
                 "display"                      : os.environ.get("DISPLAY", "Main"),
                 })
        if source.wants_features:
            capabilities.update({
                 "client-shutdown"              : self.client_shutdown,
                 "sharing"                      : self.sharing is not False,
                 "sharing-toggle"               : self.sharing is None,
                 "lock"                         : self.lock is not False,
                 "lock-toggle"                  : self.lock is None,
                 "windows"                      : server_features.windows,
                 "keyboard"                     : server_features.input_devices,
                 "pointer"                      : server_features.input_devices,
                 })
            capabilities.update(flatten_dict(self.get_server_features(source)))
        capabilities["configure.pointer"] = True    #v4 clients assume this is enabled
        return capabilities

    def send_hello(self, server_source, root_w, root_h, server_cipher):
        capabilities = self.make_hello(server_source)
        if server_source.wants_encodings and server_features.windows:
            try:
                from xpra.codecs.loader import codec_versions
            except ImportError:
                log("no codecs", exc_info=True)
            else:
                def add_encoding_caps(d):
                    updict(d, "encoding", codec_versions, "version")
                    for k,v in self.get_encoding_info().items():
                        if k=="":
                            k = "encodings"
                        else:
                            k = "encodings.%s" % k
                        d[k] = v
                if server_source.encodings_packet:
                    #we can send it later,
                    #when the init thread has finished:
                    def send_encoding_caps():
                        d = {}
                        add_encoding_caps(d)
                        #make sure the 'hello' packet goes out first:
                        self.idle_add(server_source.send_async, "encodings", d)
                    self.after_threaded_init(send_encoding_caps)
                else:
                    self.wait_for_threaded_init()
                    add_encoding_caps(capabilities)
                #check for mmap:
                if getattr(self, "mmap_size", 0)==0:
                    self.after_threaded_init(server_source.print_encoding_info)
        if server_source.wants_display:
            capabilities.update({
                         "actual_desktop_size"  : (root_w, root_h),
                         "root_window_size"     : (root_w, root_h),
                         })
        if self._aliases and server_source.wants_aliases:
            reverse_aliases = {}
            for i, packet_type in self._aliases.items():
                reverse_aliases[packet_type] = i
            capabilities["aliases"] = reverse_aliases
        if server_cipher:
            capabilities.update(server_cipher)
        server_source.send_hello(capabilities)


    ######################################################################
    # info:
    def _process_info_request(self, proto, packet):
        log("process_info_request(%s, %s)", proto, packet)
        #ignoring the list of client uuids supplied in packet[1]
        ss = self.get_server_source(proto)
        if not ss:
            return
        categories = None
        #if len(packet>=2):
        #    uuid = packet[1]
        if len(packet)>=4:
            categories = tuple(bytestostr(x) for x in packet[3])
        def info_callback(_proto, info):
            assert proto==_proto
            if categories:
                info = dict((k,v) for k,v in info.items() if k in categories)
            ss.send_info_response(info)
        self.get_all_info(info_callback, proto, None)

    def send_hello_info(self, proto):
        self.wait_for_threaded_init()
        start = monotonic_time()
        def cb(proto, info):
            self.do_send_info(proto, info)
            end = monotonic_time()
            log.info("processed info request from %s in %ims",
                     proto._conn, (end-start)*1000)
        self.get_all_info(cb, proto, None)

    def get_ui_info(self, proto, client_uuids=None, *args) -> dict:
        """ info that must be collected from the UI thread
            (ie: things that query the display)
        """
        info = {"server"    : {"max_desktop_size"   : self.get_max_screen_size()}}
        for c in SERVER_BASES:
            try:
                merge_dicts(info, c.get_ui_info(self, proto, client_uuids, *args))
            except Exception:
                log.error("Error gathering UI info on %s", c, exc_info=True)
        return info

    def get_thread_info(self, proto) -> dict:
        return get_thread_info(proto, tuple(self._server_sources.keys()))


    def get_info(self, proto=None, client_uuids=None) -> dict:
        log("ServerBase.get_info%s", (proto, client_uuids))
        start = monotonic_time()
        info = ServerCore.get_info(self, proto)
        if client_uuids:
            sources = [ss for ss in self._server_sources.values() if ss.uuid in client_uuids]
        else:
            sources = tuple(self._server_sources.values())
        log("info-request: sources=%s", sources)
        dgi = self.do_get_info(proto, sources)
        #ugly alert: merge nested dictionaries,
        #ie: do_get_info may return a dictionary for "server" and we already have one,
        # so we update it with the new values
        for k,v in dgi.items():
            cval = info.get(k)
            if cval is None:
                info[k] = v
                continue
            cval.update(v)
        log("ServerBase.get_info took %.1fms", 1000.0*(monotonic_time()-start))
        return info

    def get_packet_handlers_info(self) -> dict:
        info = ServerCore.get_packet_handlers_info(self)
        info.update({
            "authenticated" : sorted(self._authenticated_packet_handlers.keys()),
            "ui"            : sorted(self._authenticated_ui_packet_handlers.keys()),
            })
        return info


    def get_features_info(self) -> dict:
        i = {
             "sharing"          : self.sharing is not False,
             "idle_timeout"     : self.idle_timeout,
             }
        i.update(self.get_server_features())
        return i

    def do_get_info(self, proto, server_sources=None) -> dict:
        start = monotonic_time()
        info = {}
        def up(prefix, d):
            merge_dicts(info, {prefix : d})

        for c in SERVER_BASES:
            try:
                merge_dicts(info, c.get_info(self, proto))
            except Exception as e:
                log("do_get_info%s", (proto, server_sources), exc_info=True)
                log.error("Error collecting information from %s", c)
                log.error(" %s", e)

        up("features",  self.get_features_info())
        up("network", {
            "sharing"                      : self.sharing is not False,
            "sharing-toggle"               : self.sharing is None,
            "lock"                         : self.lock is not False,
            "lock-toggle"                  : self.lock is None,
            })

        # other clients:
        info["clients"] = {
            ""                   : sum(1 for p in self._server_sources if p!=proto),
            "unauthenticated"    : sum(1 for p in self._potential_protocols if ((p is not proto) and (p not in self._server_sources))),
           }
        #find the server source to report on:
        n = len(server_sources or [])
        if n==1:
            ss = server_sources[0]
            up("client", ss.get_info())
        elif n>1:
            cinfo = {}
            for i, ss in enumerate(server_sources):
                sinfo = ss.get_info()
                sinfo["ui-driver"] = self.ui_driver==ss.uuid
                cinfo[i] = sinfo
            up("client", cinfo)
        log("ServerBase.do_get_info took %ims", (monotonic_time()-start)*1000)
        return info


    def _process_server_settings(self, proto, packet):
        #only used by x11 servers
        pass


    def _set_client_properties(self, proto, wid, window, new_client_properties):
        """
        Allows us to keep window properties for a client after disconnection.
        (we keep it in a map with the client's uuid as key)
        """
        ss = self.get_server_source(proto)
        if ss:
            ss.set_client_properties(wid, window, typedict(new_client_properties))
            #filter out encoding properties, which are expected to be set everytime:
            ncp = {}
            for k,v in new_client_properties.items():
                if v is None:
                    log.warn("removing invalid None property for %s", k)
                    continue
                k = strtobytes(k)
                if not k.startswith(b"encoding"):
                    ncp[k] = v
            if ncp:
                log("set_client_properties updating window %s of source %s with %s", wid, ss.uuid, ncp)
                client_properties = self.client_properties.setdefault(wid, {}).setdefault(ss.uuid, {})
                client_properties.update(ncp)


    ######################################################################
    # settings toggle:
    def setting_changed(self, setting, value):
        #tell all the clients (that can) about the new value for this setting
        for ss in tuple(self._server_sources.values()):
            ss.send_setting_change(setting, value)

    def _process_set_deflate(self, proto, packet):
        level = packet[1]
        log("client has requested compression level=%s", level)
        proto.set_compression_level(level)
        #echo it back to the client:
        ss = self.get_server_source(proto)
        if ss:
            ss.set_deflate(level)

    def _process_sharing_toggle(self, proto, packet):
        assert self.sharing is None
        ss = self.get_server_source(proto)
        if not ss:
            return
        sharing = bool(packet[1])
        ss.share = sharing
        if not sharing:
            #disconnect other users:
            for p,ss in tuple(self._server_sources.items()):
                if p!=proto:
                    self.disconnect_client(p, DETACH_REQUEST,
                                           "client %i no longer wishes to share the session" % ss.counter)

    def _process_lock_toggle(self, proto, packet):
        assert self.lock is None
        ss = self.get_server_source(proto)
        if ss:
            ss.lock = bool(packet[1])
            log("lock set to %s for client %i", ss.lock, ss.counter)




    ######################################################################
    # http server and http audio stream:
    def get_http_info(self) -> dict:
        info = ServerCore.get_http_info(self)
        info["clients"] = len(self._server_sources)
        return info

    def get_http_scripts(self) -> dict:
        scripts = ServerCore.get_http_scripts(self)
        scripts["/audio.mp3"] = self.http_audio_mp3_request
        return scripts

    def http_audio_mp3_request(self, handler):
        def err(code=500):
            handler.send_response(code)
            return None
        try:
            args_str = handler.path.split("?", 1)[1]
        except IndexError:
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
        #don't close the connection when handler.finish() is called,
        #we will continue to write to this socket as we process more buffers:
        finish = handler.finish
        def do_finish():
            try:
                finish()
            except:
                log("error calling %s", finish, exc_info=True)
        def noop():
            pass
        handler.finish = noop
        state = {}
        def new_buffer(_sound_source, data, _metadata, packet_metadata=()):
            if state.get("failed"):
                return
            if not state.get("started"):
                httplog.warn("buffer received but stream is not started yet")
                source.stop_sending_sound()
                err()
                do_finish()
                return
            count = state.get("buffers", 0)
            httplog("new_buffer [%i] for %s sound stream: %i bytes", count, state.get("codec", "?"), len(data))
            #httplog("buffer %i: %s", count, hexstr(data))
            state["buffers"] = count+1
            try:
                for x in packet_metadata:
                    handler.wfile.write(x)
                handler.wfile.write(data)
                handler.wfile.flush()
            except Exception as e:
                state["failed"] = True
                httplog("failed to send new audio buffer", exc_info=True)
                httplog.warn("Error: failed to send audio packet:")
                httplog.warn(" %s", e)
                source.stop_sending_sound()
                do_finish()
        def new_stream(sound_source, codec):
            codec = bytestostr(codec)
            httplog("new_stream: %s", codec)
            sound_source.codec = codec
            headers = {
                "Content-type"      : "audio/mpeg",
                }
            try:
                handler.send_response(200)
                for k,v in headers.items():
                    handler.send_header(k, v)
                handler.end_headers()
            except ValueError:
                httplog("new_stream error writing headers", exc_info=True)
                state["failed"] = True
                source.stop_sending_sound()
                do_finish()
            else:
                state["started"] = True
                state["buffers"] = 0
                state["codec"] = codec
        def timeout_check():
            if not state.get("started"):
                err()
                source.stop_sending_sound()
        if source.sound_source:
            source.stop_sending_sound()
        def start_sending_sound():
            source.start_sending_sound("mp3", volume=1.0, new_stream=new_stream,
                                       new_buffer=new_buffer, skip_client_codec_check=True)
        self.idle_add(start_sending_sound)
        self.mp3_stream_check_timer = self.timeout_add(1000*5, timeout_check)

    def cancel_mp3_stream_check_timer(self):
        msct = self.mp3_stream_check_timer
        if msct:
            self.mp3_stream_check_timer = None
            self.source_remove(msct)


    ######################################################################
    # client connections:
    def init_sockets(self, sockets):
        for c in SERVER_BASES:
            c.init_sockets(self, sockets)

    def cleanup_protocol(self, protocol):
        netlog("cleanup_protocol(%s)", protocol)
        #this ensures that from now on we ignore any incoming packets coming
        #from this connection as these could potentially set some keys pressed, etc
        try:
            self._potential_protocols.remove(protocol)
        except ValueError:
            pass
        source = self._server_sources.pop(protocol, None)
        if source:
            self.cleanup_source(source)
            self.mdns_update()
        for c in SERVER_BASES:
            c.cleanup_protocol(self, protocol)
        return source

    def cleanup_source(self, source):
        self.server_event("connection-lost", source.uuid)
        remaining_sources = tuple(self._server_sources.values())
        if self.ui_driver==source.uuid:
            if len(remaining_sources)==1:
                self.set_ui_driver(remaining_sources[0])
            else:
                self.set_ui_driver(None)
        source.close()
        netlog("cleanup_source(%s) remaining sources: %s", source, remaining_sources)
        netlog.info("xpra client %i disconnected.", source.counter)
        has_client = len(remaining_sources)>0
        if not has_client:
            self.idle_add(self.last_client_exited)

    def last_client_exited(self):
        #must run from the UI thread (modifies focus and keys)
        netlog("last_client_exited() exit_with_client=%s", self.exit_with_client)
        self.reset_server_timeout(True)
        for c in SERVER_BASES:
            if c!=ServerCore:
                try:
                    c.last_client_exited(self)
                except Exception:
                    log("last_client_exited calling %s", c.last_client_exited, exc_info=True)
        if self.exit_with_client:
            if not self._closing:
                netlog.info("Last client has disconnected, terminating")
                self.clean_quit(False)


    def set_ui_driver(self, source):
        if source and self.ui_driver==source.uuid:
            return
        log("new ui driver: %s", source)
        if not source:
            self.ui_driver = None
        else:
            self.ui_driver = source.uuid
        for c in SERVER_BASES:
            if c!=ServerCore:
                c.set_session_driver(self, source)


    def reset_focus(self):
        for c in SERVER_BASES:
            if c!=ServerCore:
                c.reset_focus(self)


    def get_all_protocols(self) -> list:
        return list(self._potential_protocols) + list(self._server_sources.keys())


    def is_timedout(self, protocol):
        v = ServerCore.is_timedout(self, protocol) and protocol not in self._server_sources
        netlog("is_timedout(%s)=%s", protocol, v)
        return v


    def _log_disconnect(self, proto, *args):
        #skip logging of disconnection events for server sources
        #we have tagged during hello ("info_request", "exit_request", etc..)
        ss = self.get_server_source(proto)
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

    ######################################################################
    # packets:
    def add_packet_handlers(self, defs, main_thread=True):
        for packet_type, handler in defs.items():
            self.add_packet_handler(packet_type, handler, main_thread)

    def add_packet_handler(self, packet_type, handler, main_thread=True):
        netlog("add_packet_handler%s", (packet_type, handler, main_thread))
        if main_thread:
            handlers = self._authenticated_ui_packet_handlers
        else:
            handlers = self._authenticated_packet_handlers
        handlers[packet_type] = handler

    def init_packet_handlers(self):
        for c in SERVER_BASES:
            c.init_packet_handlers(self)
        #no need for main thread:
        self.add_packet_handlers({
            "sharing-toggle"    : self._process_sharing_toggle,
            "lock-toggle"       : self._process_lock_toggle,
            }, False)
        #attributes / settings:
        self.add_packet_handlers({
            "server-settings"   : self._process_server_settings,
            "set_deflate"       : self._process_set_deflate,
            "shutdown-server"   : self._process_shutdown_server,
            "exit-server"       : self._process_exit_server,
            "info-request"      : self._process_info_request,
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
            def call_handler():
                may_log_packet(False, packet_type, packet)
                handler(proto, packet)
            if proto in self._server_sources:
                handler = self._authenticated_ui_packet_handlers.get(packet_type)
                if handler:
                    netlog("process ui packet %s", packet_type)
                    self.idle_add(call_handler)
                    return
                handler = self._authenticated_packet_handlers.get(packet_type)
                if handler:
                    netlog("process non-ui packet %s", packet_type)
                    call_handler()
                    return
            handler = self._default_packet_handlers.get(packet_type)
            if handler:
                netlog("process default packet %s", packet_type)
                call_handler()
                return
            def invalid_packet():
                ss = self.get_server_source(proto)
                if not self._closing and not proto.is_closed() and (ss is None or not ss.is_closed()):
                    netlog("invalid packet: %s", packet)
                    netlog.error("Error: unknown or invalid packet type '%s'", packet_type)
                    netlog.error(" received from %s", proto)
                if not ss:
                    proto.close()
            self.idle_add(invalid_packet)
        except Exception:
            netlog.error("Error processing a '%s' packet", packet_type)
            netlog.error(" received from %s:", proto)
            netlog.error(" using %s", handler, exc_info=True)
