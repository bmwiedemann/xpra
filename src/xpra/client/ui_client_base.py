# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2018 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008, 2010 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import sys

from xpra.log import Logger
log = Logger("client")
keylog = Logger("client", "keyboard")


from xpra.client.client_base import XpraClientBase
from xpra.client.keyboard_helper import KeyboardHelper
from xpra.platform import set_name
from xpra.platform.features import MMAP_SUPPORTED
from xpra.platform.gui import (ready as gui_ready, get_session_type, ClientExtras)
from xpra.version_util import full_version_str
from xpra.net import compression, packet_encoding
from xpra.child_reaper import reaper_cleanup
from xpra.os_util import platform_name, bytestostr, strtobytes, BITS
from xpra.util import std, envint, envbool, typedict, updict, XPRA_AUDIO_NOTIFICATION_ID
from xpra.version_util import get_version_info_full, get_platform_info
#client mixins:
from xpra.client.mixins.webcam import WebcamForwarder
from xpra.client.mixins.audio import AudioClient
from xpra.client.mixins.rpc import RPCClient
from xpra.client.mixins.clipboard import ClipboardClient
from xpra.client.mixins.notifications import NotificationClient
from xpra.client.mixins.window_manager import WindowClient
from xpra.client.mixins.mmap import MmapClient
from xpra.client.mixins.remote_logging import RemoteLogging
from xpra.client.mixins.display import DisplayClient
from xpra.client.mixins.network_state import NetworkState
from xpra.client.mixins.encodings import Encodings
from xpra.client.mixins.tray import TrayClient


MOUSE_DELAY_AUTO = envbool("XPRA_MOUSE_DELAY_AUTO", True)
TRAY_DELAY = envint("XPRA_TRAY_DELAY", 0)


"""
Utility superclass for client classes which have a UI.
See gtk_client_base and its subclasses.
"""
class UIXpraClient(XpraClientBase, DisplayClient, WindowClient, WebcamForwarder, AudioClient, ClipboardClient, NotificationClient, RPCClient, MmapClient, RemoteLogging, NetworkState, Encodings, TrayClient):
    #NOTE: these signals aren't registered here because this class
    #does not extend GObject,
    #the gtk client subclasses will take care of it.
    #these are all "no-arg" signals
    __signals__ = ["first-ui-received", "keyboard-sync-toggled"]
    for c in (DisplayClient, WindowClient, WebcamForwarder, AudioClient, ClipboardClient, NotificationClient, RPCClient, MmapClient, RemoteLogging, NetworkState, Encodings, TrayClient):
        __signals__ += c.__signals__

    def __init__(self):
        log.info("Xpra %s client version %s %i-bit", self.client_toolkit(), full_version_str(), BITS)
        for c in UIXpraClient.__bases__:
            c.__init__(self)
        try:
            pinfo = get_platform_info()
            osinfo = "%s" % platform_name(sys.platform, pinfo.get("linux_distribution") or pinfo.get("sysrelease", ""))
            log.info(" running on %s", osinfo)
        except:
            log("platform name error:", exc_info=True)

        self._ui_events = 0
        self.title = ""
        self.session_name = u""

        self.server_platform = ""
        self.server_session_name = None

        #features:
        self.opengl_enabled = False
        self.opengl_props = {}
        self.readonly = False
        self.xsettings_enabled = False
        self.server_start_new_commands = False

        #in WindowClient - should it be?
        #self.server_is_desktop = False
        self.server_sharing = False
        self.server_sharing_toggle = False
        self.server_lock = False
        self.server_lock_toggle = False
        self.server_window_filters = False

        self.client_supports_opengl = False
        self.client_supports_sharing = False
        self.client_lock = False

        #helpers and associated flags:
        self.client_extras = None
        self.keyboard_helper_class = KeyboardHelper
        self.keyboard_helper = None
        self.keyboard_grabbed = False
        self.pointer_grabbed = False
        self.kh_warning = False
        self.menu_helper = None

        #state:
        self._on_handshake = []
        self._on_server_setting_changed = {}


    def init(self, opts):
        """ initialize variables from configuration """
        for c in UIXpraClient.__bases__:
            c.init(self, opts)

        self.title = opts.title
        self.session_name = bytestostr(opts.session_name)
        self.xsettings_enabled = opts.xsettings
        self.readonly = opts.readonly
        self.client_supports_sharing = opts.sharing is True
        self.client_lock = opts.lock is True


    def init_ui(self, opts, extra_args=[]):
        """ initialize user interface """
        if not self.readonly:
            def noauto(v):
                if not v:
                    return None
                if str(v).lower()=="auto":
                    return None
                return v
            overrides = [noauto(getattr(opts, "keyboard_%s" % x)) for x in ("layout", "layouts", "variant", "variants", "options")]
            self.keyboard_helper = self.keyboard_helper_class(self.send, opts.keyboard_sync, opts.shortcut_modifiers, opts.key_shortcut, opts.keyboard_raw, *overrides)
        TrayClient.init_ui(self)
        NotificationClient.init_ui(self)

        self.init_opengl(opts.opengl)

        #audio tagging:
        AudioClient.init_audio_tagging(self, opts.tray_icon)

        if ClientExtras is not None:
            self.client_extras = ClientExtras(self, opts)

        WindowClient.init_ui(self, opts, extra_args)

        if MOUSE_DELAY_AUTO:
            try:
                from xpra.platform.gui import get_vrefresh
                v = get_vrefresh()
                if v<=0:
                    #some platforms don't detect the vrefresh correctly
                    #(ie: macos in virtualbox?), so use a sane default:
                    v = 60
                self._mouse_position_delay = 1000//v
                log("mouse delay: %s", self._mouse_position_delay)
            except Exception:
                log("failed to calculate automatic delay", exc_info=True)


    def run(self):
        if self.client_extras:
            self.idle_add(self.client_extras.ready)
        for c in UIXpraClient.__bases__:
            c.run(self)
        self.send_hello()


    def quit(self, exit_code=0):
        raise Exception("override me!")

    def cleanup(self):
        log("UIXpraClient.cleanup()")
        for c in UIXpraClient.__bases__:
            c.cleanup(self)
        for x in (self.keyboard_helper, self.tray, self.menu_helper, self.client_extras):
            if x is None:
                continue
            log("UIXpraClient.cleanup() calling %s.cleanup()", type(x))
            try:
                x.cleanup()
            except:
                log.error("error on %s cleanup", type(x), exc_info=True)
        #the protocol has been closed, it is now safe to close all the windows:
        #(cleaner and needed when we run embedded in the client launcher)
        self.destroy_all_windows()
        reaper_cleanup()
        log("UIXpraClient.cleanup() done")


    def signal_cleanup(self):
        log("UIXpraClient.signal_cleanup()")
        XpraClientBase.signal_cleanup(self)
        reaper_cleanup()
        log("UIXpraClient.signal_cleanup() done")


    def show_about(self, *_args):
        log.warn("show_about() is not implemented in %s", self)

    def show_session_info(self, *_args):
        log.warn("show_session_info() is not implemented in %s", self)

    def show_bug_report(self, *_args):
        log.warn("show_bug_report() is not implemented in %s", self)


    def init_opengl(self, _enable_opengl):
        self.opengl_enabled = False
        self.client_supports_opengl = False
        self.opengl_props = {"info" : "not supported"}


    def _ui_event(self):
        if self._ui_events==0:
            self.emit("first-ui-received")
        self._ui_events += 1


    def get_mouse_position(self):
        raise NotImplementedError()

    def get_current_modifiers(self):
        raise NotImplementedError()


    def send_start_command(self, name, command, ignore, sharing=True):
        log("send_start_command(%s, %s, %s, %s)", name, command, ignore, sharing)
        self.send("start-command", name, command, ignore, sharing)


    def get_version_info(self):
        return get_version_info_full()


    ######################################################################
    # hello:
    def make_hello(self):
        caps = XpraClientBase.make_hello(self)
        caps["session-type"] = get_session_type()

        #don't try to find the server uuid if this platform cannot run servers..
        #(doing so causes lockups on win32 and startup errors on osx)
        if MMAP_SUPPORTED:
            #we may be running inside another server!
            try:
                from xpra.server.server_uuid import get_uuid
                caps["server_uuid"] = get_uuid() or ""
            except:
                pass
        for x in (
            #generic feature flags:
            "notify-startup-complete",
            "wants_events",
            "setting-change",
            ):
            caps[x] = True
        #FIXME: the messy bits without proper namespace:
        caps.update({
            #generic server flags:
            "share"                     : self.client_supports_sharing,
            "lock"                      : self.client_lock,
            })
        #messy unprefixed:
        caps.update(WindowClient.get_caps(self))
        caps.update(DisplayClient.get_caps(self))
        caps.update(NetworkState.get_caps(self))
        caps.update(Encodings.get_caps(self))
        caps.update(ClipboardClient.get_caps(self))
        caps.update(self.get_keyboard_caps())
        #nicely prefixed:
        def u(prefix, c):
            updict(caps, prefix, c, flatten_dicts=False)
        u("sound",              AudioClient.get_audio_capabilities(self))
        u("notifications",      self.get_notifications_caps())
        u("control_commands",   self.get_control_commands_caps())
        u("platform",           get_platform_info())
        mmap_caps = MmapClient.get_caps(self)
        u("mmap",               mmap_caps)
        #pre 2.3 servers only use underscore instead of "." prefix for mmap caps:
        for k,v in mmap_caps.items():
            caps["mmap_%s" % k] = v
        return caps



    ######################################################################
    # connection setup:
    def setup_connection(self, conn):
        XpraClientBase.setup_connection(self, conn)
        MmapClient.setup_connection(self, conn)

    def server_connection_established(self):
        if not XpraClientBase.server_connection_established(self):
            return False
        #process the rest from the UI thread:
        self.idle_add(self.process_ui_capabilities)
        return True


    def parse_server_capabilities(self):
        for c in UIXpraClient.__bases__:
            if not c.parse_server_capabilities(self):
                log.info("failed to parse server capabilities in %s", c)
                return  False
        c = self.server_capabilities
        self.server_session_name = strtobytes(c.rawget("session_name", b"")).decode("utf-8")
        set_name("Xpra", self.session_name or self.server_session_name or "Xpra")
        self.server_platform = c.strget("platform")
        self.server_sharing = c.boolget("sharing")
        self.server_sharing_toggle = c.boolget("sharing-toggle")
        self.server_lock = c.boolget("lock")
        self.server_lock_toggle = c.boolget("lock-toggle")
        self.server_start_new_commands = c.boolget("start-new-commands")
        self.server_commands_info = c.boolget("server-commands-info")
        self.server_commands_signals = c.strlistget("server-commands-signals")
        self.server_readonly = c.boolget("readonly")
        if self.server_readonly and not self.readonly:
            log.info("server is read only")
            self.readonly = True

        i = platform_name(self._remote_platform, c.strlistget("platform.linux_distribution") or c.strget("platform.release", ""))
        r = self._remote_version
        if self._remote_revision:
            r += "-r%s" % self._remote_revision
        mode = c.strget("server.mode", "server")
        bits = c.intget("python.bits", 32)
        log.info("Xpra %s server version %s %i-bit", mode, std(r), bits)
        if i:
            log.info(" running on %s", std(i))
        if c.boolget("proxy"):
            proxy_hostname = c.strget("proxy.hostname")
            proxy_platform = c.strget("proxy.platform")
            proxy_release = c.strget("proxy.platform.release")
            proxy_version = c.strget("proxy.version")
            proxy_version = c.strget("proxy.build.version", proxy_version)
            proxy_distro = c.strget("linux_distribution")
            msg = "via: %s proxy version %s" % (platform_name(proxy_platform, proxy_distro or proxy_release), std(proxy_version or "unknown"))
            if proxy_hostname:
                msg += " on '%s'" % std(proxy_hostname)
            log.info(msg)
        return True

    def process_ui_capabilities(self):
        for c in UIXpraClient.__bases__:
            if c!=XpraClientBase:
                c.process_ui_capabilities(self)
        #keyboard:
        c = self.server_capabilities
        if self.keyboard_helper:
            modifier_keycodes = c.dictget("modifier_keycodes")
            if modifier_keycodes:
                self.keyboard_helper.set_modifier_mappings(modifier_keycodes)
        self.key_repeat_delay, self.key_repeat_interval = c.intpair("key_repeat", (-1,-1))
        self.connect("keyboard-sync-toggled", self.send_keyboard_sync_enabled_status)
        self.handshake_complete()


    def _process_startup_complete(self, packet):
        log("all the existing windows and system trays have been received: %s items", len(self._id_to_window))
        XpraClientBase._process_startup_complete(self, packet)
        gui_ready()
        if self.tray:
            self.tray.ready()
        self.send_info_request()


    def handshake_complete(self):
        oh = self._on_handshake
        self._on_handshake = None
        for cb, args in oh:
            try:
                cb(*args)
            except:
                log.error("Error processing handshake callback %s", cb, exc_info=True)

    def after_handshake(self, cb, *args):
        log("after_handshake(%s, %s) on_handshake=%s", cb, args, self._on_handshake)
        if self._on_handshake is None:
            #handshake has already occurred, just call it:
            self.idle_add(cb, *args)
        else:
            self._on_handshake.append((cb, args))


    ######################################################################
    # server messages:
    def _process_server_event(self, packet):
        log(u": ".join((str(x) for x in packet[1:])))

    def on_server_setting_changed(self, setting, cb):
        self._on_server_setting_changed.setdefault(setting, []).append(cb)

    def _process_setting_change(self, packet):
        setting, value = packet[1:3]
        setting = bytestostr(setting)
        #convert "hello" / "setting" variable names to client variables:
        if setting in (
            "bell", "randr", "cursors", "notifications", "dbus-proxy", "clipboard",
            "clipboard-direction", "session_name",
            "sharing", "sharing-toggle", "lock", "lock-toggle",
            "start-new-commands", "client-shutdown", "webcam",
            "bandwidth-limit",
            ):
            setattr(self, "server_%s" % setting.replace("-", "_"), value)
        else:
            log.info("unknown server setting changed: %s=%s", setting, value)
            return
        log.info("server setting changed: %s=%s", setting, value)
        self.server_setting_changed(setting, value)

    def server_setting_changed(self, setting, value):
        log("setting_changed(%s, %s)", setting, value)
        cbs = self._on_server_setting_changed.get(setting)
        if cbs:
            for cb in cbs:
                log("setting_changed(%s, %s) calling %s", setting, value, cb)
                cb(setting, value)


    def get_control_commands_caps(self):
        caps = ["show_session_info", "show_bug_report", "debug"]
        for x in compression.get_enabled_compressors():
            caps.append("enable_"+x)
        for x in packet_encoding.get_enabled_encoders():
            caps.append("enable_"+x)
        log("get_control_commands_caps()=%s", caps)
        return {"" : caps}

    def _process_control(self, packet):
        command = packet[1]
        if command=="show_session_info":
            args = packet[2:]
            log("calling show_session_info%s on server request", args)
            self.show_session_info(*args)
        elif command=="show_bug_report":
            self.show_bug_report()
        elif command in ("enable_%s" % x for x in compression.get_enabled_compressors()):
            compressor = command.split("_")[1]
            log.info("switching to %s on server request", compressor)
            self._protocol.enable_compressor(compressor)
        elif command in ("enable_%s" % x for x in packet_encoding.get_enabled_encoders()):
            pe = command.split("_")[1]
            log.info("switching to %s on server request", pe)
            self._protocol.enable_encoder(pe)
        elif command=="name":
            assert len(args)>=3
            self.server_session_name = args[2]
            log.info("session name updated from server: %s", self.server_session_name)
            #TODO: reset tray tooltip, session info title, etc..
        elif command=="debug":
            args = packet[2:]
            if len(args)<2:
                log.warn("not enough arguments for debug control command")
                return
            log_cmd = args[0]
            if log_cmd not in ("enable", "disable"):
                log.warn("invalid debug control mode: '%s' (must be 'enable' or 'disable')", log_cmd)
                return
            categories = args[1:]
            from xpra.log import add_debug_category, add_disabled_category, enable_debug_for, disable_debug_for
            if log_cmd=="enable":
                add_debug_category(*categories)
                loggers = enable_debug_for(*categories)
            else:
                assert log_cmd=="disable"
                add_disabled_category(*categories)
                loggers = disable_debug_for(*categories)
            log.info("%sd debugging for: %s", log_cmd, loggers)
            return
        else:
            log.warn("received invalid control command from server: %s", command)


    def may_notify_audio(self, summary, body):
        self.may_notify(XPRA_AUDIO_NOTIFICATION_ID, summary, body, icon_name="audio")


    ######################################################################
    # features:
    def send_sharing_enabled(self):
        assert self.server_sharing and self.server_sharing_toggle
        self.send("sharing-toggle", self.client_supports_sharing)

    def send_lock_enabled(self):
        assert self.server_lock_toggle
        self.send("lock-toggle", self.client_lock)

    def send_notify_enabled(self):
        assert self.client_supports_notifications, "cannot toggle notifications: the feature is disabled by the client"
        self.send("set-notify", self.notifications_enabled)

    def send_bell_enabled(self):
        assert self.client_supports_bell, "cannot toggle bell: the feature is disabled by the client"
        assert self.server_bell, "cannot toggle bell: the feature is disabled by the server"
        self.send("set-bell", self.bell_enabled)

    def send_cursors_enabled(self):
        assert self.client_supports_cursors, "cannot toggle cursors: the feature is disabled by the client"
        assert self.server_cursors, "cannot toggle cursors: the feature is disabled by the server"
        self.send("set-cursors", self.cursors_enabled)

    def send_force_ungrab(self, wid):
        self.send("force-ungrab", wid)

    def send_keyboard_sync_enabled_status(self, *_args):
        self.send("set-keyboard-sync-enabled", self.keyboard_sync)


    ######################################################################
    # keyboard:
    def get_keyboard_caps(self):
        caps = {}
        if self.readonly:
            #don't bother sending keyboard info, as it won't be used
            caps["keyboard"] = False
        else:
            caps.update(self.get_keymap_properties())
            #show the user a summary of what we have detected:
            self.keyboard_helper.log_keyboard_info()

        caps["modifiers"] = self.get_current_modifiers()
        if self.keyboard_helper:
            delay_ms, interval_ms = self.keyboard_helper.key_repeat_delay, self.keyboard_helper.key_repeat_interval
            if delay_ms>0 and interval_ms>0:
                caps["key_repeat"] = (delay_ms,interval_ms)
            else:
                #cannot do keyboard_sync without a key repeat value!
                #(maybe we could just choose one?)
                self.keyboard_helper.keyboard_sync = False
            caps["keyboard_sync"] = self.keyboard_helper.keyboard_sync
        log("keyboard capabilities: %s", caps)
        return caps

    def window_keyboard_layout_changed(self, window):
        #win32 can change the keyboard mapping per window...
        keylog("window_keyboard_layout_changed(%s)", window)
        if self.keyboard_helper:
            self.keyboard_helper.keymap_changed()

    def get_keymap_properties(self):
        props = self.keyboard_helper.get_keymap_properties()
        props["modifiers"] = self.get_current_modifiers()
        return props

    def handle_key_action(self, window, key_event):
        if self.readonly or self.keyboard_helper is None:
            return
        wid = self._window_to_id[window]
        keylog("handle_key_action(%s, %s) wid=%s", window, key_event, wid)
        self.keyboard_helper.handle_key_action(window, wid, key_event)

    def mask_to_names(self, mask):
        if self.keyboard_helper is None:
            return []
        return self.keyboard_helper.mask_to_names(mask)


    ######################################################################
    # windows overrides
    def cook_metadata(self, _new_window, metadata):
        #convert to a typedict and apply client-side overrides:
        metadata = typedict(metadata)
        if self.server_is_desktop and self.desktop_fullscreen:
            #force it fullscreen:
            try:
                del metadata["size-constraints"]
            except:
                pass
            metadata["fullscreen"] = True
            #FIXME: try to figure out the monitors we go fullscreen on for X11:
            #if POSIX:
            #    metadata["fullscreen-monitors"] = [0, 1, 0, 1]
        return metadata

    ######################################################################
    # network and status:
    def server_connection_state_change(self):
        if not self._server_ok:
            log.info("server is not responding, drawing spinners over the windows")
            def timer_redraw():
                if self._protocol is None:
                    #no longer connected!
                    return False
                ok = self.server_ok()
                self.redraw_spinners()
                if ok:
                    log.info("server is OK again")
                return not ok           #repaint again until ok
            self.idle_add(self.redraw_spinners)
            self.timeout_add(250, timer_redraw)

    def redraw_spinners(self):
        #draws spinner on top of the window, or not (plain repaint)
        #depending on whether the server is ok or not
        ok = self.server_ok()
        log("redraw_spinners() ok=%s", ok)
        for w in self._id_to_window.values():
            if not w.is_tray():
                w.spinner(ok)


    ######################################################################
    # packets:
    def init_authenticated_packet_handlers(self):
        log("init_authenticated_packet_handlers()")
        for c in UIXpraClient.__bases__:
            c.init_authenticated_packet_handlers(self)
        #run from the UI thread:
        self.set_packet_handlers(self._ui_packet_handlers, {
            "startup-complete":     self._process_startup_complete,
            "setting-change":       self._process_setting_change,
            "control" :             self._process_control,
            })
        #run directly from the network thread:
        self.set_packet_handlers(self._packet_handlers, {
            "server-event":         self._process_server_event,
            })


    def process_packet(self, proto, packet):
        self.check_server_echo(0)
        XpraClientBase.process_packet(self, proto, packet)
