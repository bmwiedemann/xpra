# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2015 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os.path
import sys
import time
import hashlib

from xpra.log import Logger
log = Logger("server")
keylog = Logger("keyboard")
focuslog = Logger("focus")
execlog = Logger("exec")
commandlog = Logger("command")
soundlog = Logger("sound")
clientlog = Logger("client")
screenlog = Logger("screen")
printlog = Logger("printing")
netlog = Logger("network")
metalog = Logger("metadata")
windowlog = Logger("window")
clipboardlog = Logger("clipboard")
rpclog = Logger("rpc")
dbuslog = Logger("dbus")
webcamlog = Logger("webcam")

from xpra.keyboard.mask import DEFAULT_MODIFIER_MEANINGS
from xpra.server.server_core import ServerCore, get_thread_info
from xpra.server.control_command import ArgsControlCommand, ControlError
from xpra.simple_stats import to_std_unit
from xpra.child_reaper import getChildReaper
from xpra.os_util import BytesIOClass, thread, get_hex_uuid, livefds, load_binary_file
from xpra.util import typedict, updict, log_screen_sizes, engs, repr_ellipsized, csv, iround, \
    SERVER_EXIT, SERVER_ERROR, SERVER_SHUTDOWN, DETACH_REQUEST, NEW_CLIENT, DONE, IDLE_TIMEOUT
from xpra.net.bytestreams import set_socket_timeout
from xpra.platform import get_username
from xpra.platform.paths import get_icon_filename
from xpra.child_reaper import reaper_cleanup
from xpra.scripts.config import python_platform, parse_bool_or_int, FALSE_OPTIONS, TRUE_OPTIONS
from xpra.scripts.main import sound_option
from xpra.codecs.loader import PREFERED_ENCODING_ORDER, PROBLEMATIC_ENCODINGS, load_codecs, codec_versions, has_codec, get_codec
from xpra.codecs.video_helper import getVideoHelper, ALL_VIDEO_ENCODER_OPTIONS, ALL_CSC_MODULE_OPTIONS
from xpra.net.file_transfer import FileTransferHandler
if sys.version > '3':
    unicode = str           #@ReservedAssignment


DETECT_MEMLEAKS = os.environ.get("XPRA_DETECT_MEMLEAKS", "0")=="1"
DETECT_FDLEAKS = os.environ.get("XPRA_DETECT_FDLEAKS", "0")=="1"
MAX_CONCURRENT_CONNECTIONS = 20


def parse_env(env):
    d = {}
    try:
        for ev in env:
            try:
                if ev.startswith("#"):
                    continue
                v = ev.split("=", 1)
                if len(v)!=2:
                    log.warn("Warning: invalid environment option '%s'", ev)
                    continue
                d[v[0]] = v[1]
            except Exception as e:
                log.error("Error parsing child environment '%s':", ev)
                log.error(" %s", e)
    except Exception as e:
        log.error("Error parsing child environment '%s':", env)
        log.error(" %s", e)
    return d


class ServerBase(ServerCore, FileTransferHandler):
    """
        This is the base class for servers.
        It provides all the generic functions but is not tied
        to a specific backend (X11 or otherwise).
        See GTKServerBase/X11ServerBase and other platform specific subclasses.
    """

    def __init__(self):
        ServerCore.__init__(self)
        FileTransferHandler.__init__(self)
        log("ServerBase.__init__()")
        self.init_uuid()

        # This must happen early, before loading in windows at least:
        self._server_sources = {}

        #so clients can store persistent attributes on windows:
        self.client_properties = {}

        self.supports_mmap = False
        self.min_mmap_size = 64*1024*1024
        self.randr = False

        self._window_to_id = {}
        self._id_to_window = {}
        self.window_filters = []
        # Window id 0 is reserved for "not a window"
        self._max_window_id = 1

        self.default_quality = -1
        self.default_min_quality = 0
        self.default_speed = -1
        self.default_min_speed = 0
        self.pulseaudio = False
        self.sharing = False
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
        self.supports_clipboard = False
        self.supports_dbus_proxy = False
        self.dbus_helper = None
        self.dbus_control = False
        self.dbus_server = None
        self.lpadmin = ""
        self.lpinfo = ""
        #starting child commands:
        self.start_commands = []
        self.start_child_commands = []
        self.start_after_connect = []
        self.start_child_after_connect = []
        self.start_on_connect = []
        self.start_child_on_connect = []
        self.exit_with_children = False
        self.start_after_connect_done = False
        self.start_new_commands = False
        self.remote_logging = False
        self.env = []
        self.exec_cwd = None
        self.exec_wrapper = None
        self.child_reaper = None
        self.send_pings = False
        self.scaling_control = False
        self.rpc_handlers = {}
        self.webcam_forwarding = False
        self.webcam_encodings = []
        self.webcam_forwarding_device = None
        self.virtual_video_devices = 0

        #sound:
        self.pulseaudio = False
        self.pulseaudio_command = None
        self.pulseaudio_configure_commands = []
        self.pulseaudio_proc = None
        self.sound_properties = typedict()

        #encodings:
        self.allowed_encodings = None
        self.core_encodings = []
        self.encodings = []
        self.lossless_encodings = []
        self.lossless_mode_encodings = []
        self.default_encoding = None

        self.init_encodings()
        self.init_packet_handlers()
        self.init_aliases()

        if DETECT_MEMLEAKS:
            from xpra.util import detect_leaks
            detailed = []
            #example: warning, uses ugly direct import:
            #try:
            #    from xpra.x11.bindings.ximage import XShmImageWrapper       #@UnresolvedImport
            #    detailed.append(XShmImageWrapper)
            #except:
            #    pass
            print_leaks = detect_leaks(log, detailed)
            self.timeout_add(10*1000, print_leaks)
        self.fds = livefds()
        if DETECT_FDLEAKS:
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


    def init(self, opts):
        ServerCore.init(self, opts)
        log("ServerBase.init(%s)", opts)
        self.init_options(opts)

    def init_options(self, opts):
        self.supports_mmap = opts.mmap
        self.allowed_encodings = opts.encodings
        self.init_encoding(opts.encoding)

        self.default_quality = opts.quality
        self.default_min_quality = opts.min_quality
        self.default_speed = opts.speed
        self.default_min_speed = opts.min_speed
        self.pulseaudio = opts.pulseaudio
        self.sharing = opts.sharing
        self.bell = opts.bell
        self.cursors = opts.cursors
        self.default_dpi = int(opts.dpi)
        self.idle_timeout = opts.idle_timeout
        self.supports_clipboard = not ((opts.clipboard or "").lower() in FALSE_OPTIONS)
        self.clipboard_filter_file = opts.clipboard_filter_file
        self.supports_dbus_proxy = opts.dbus_proxy
        self.exit_with_children = opts.exit_with_children
        self.start_new_commands = opts.start_new_commands
        if opts.exec_wrapper:
            import shlex
            self.exec_wrapper = shlex.split(opts.exec_wrapper)
        self.child_reaper = getChildReaper(self.reaper_exit, exit_with_children=opts.exit_with_children)
        self.remote_logging = not ((opts.remote_logging or "").lower() in FALSE_OPTIONS)
        self.env = parse_env(opts.env)
        self.send_pings = opts.pings
        #printing and file transfer:
        FileTransferHandler.init(self, opts)
        self.lpadmin = opts.lpadmin
        self.lpinfo = opts.lpinfo
        self.av_sync = opts.av_sync
        self.dbus_control = opts.dbus_control
        #server-side printer handling is only for posix via pycups for now:
        self.printing = os.name=="posix" and opts.printing
        self.postscript_printer = opts.postscript_printer
        self.pdf_printer = opts.pdf_printer
        self.notifications_forwarder = None
        self.notifications = opts.notifications
        self.scaling_control = parse_bool_or_int("video-scaling", opts.video_scaling)
        self.webcam_forwarding = opts.webcam.lower() not in FALSE_OPTIONS

        #sound:
        self.pulseaudio = opts.pulseaudio
        self.pulseaudio_command = opts.pulseaudio_command
        self.pulseaudio_configure_commands = opts.pulseaudio_configure_commands

        #video init: default to ALL if not specified
        video_encoders = opts.video_encoders or ALL_VIDEO_ENCODER_OPTIONS
        csc_modules = opts.csc_modules or ALL_CSC_MODULE_OPTIONS
        getVideoHelper().set_modules(video_encoders=video_encoders, csc_modules=csc_modules)

    def init_components(self, opts):
        log("starting component init")
        self.init_webcam()
        self.init_clipboard()
        self.init_keyboard()
        self.init_pulseaudio()
        self.init_sound_options(opts)
        self.init_notification_forwarder()
        self.init_dbus_helper()
        self.init_dbus_server()

        self.load_existing_windows(opts.system_tray)
        thread.start_new_thread(self.threaded_init, ())

    def threaded_init(self):
        log("threaded_init() start")
        #try to load video encoders in advance as this can take some time:
        time.sleep(0.1)
        getVideoHelper().init()
        #re-init list of encodings now that we have video initialized
        self.init_encodings()
        self.init_printing()
        log("threaded_init() end")

    def init_encodings(self):
        load_codecs(decoders=False)
        encs, core_encs = [], []
        def add_encodings(encodings):
            for ce in encodings:
                e = {"rgb32" : "rgb", "rgb24" : "rgb"}.get(ce, ce)
                if self.allowed_encodings is not None and e not in self.allowed_encodings:
                    #not in whitelist (if it exists)
                    continue
                if e not in encs:
                    encs.append(e)
                if ce not in core_encs:
                    core_encs.append(ce)

        add_encodings(["rgb24", "rgb32"])

        #video encoders (empty when first called - see threaded_init)
        ve = getVideoHelper().get_encodings()
        log("init_encodings() adding video encodings: %s", ve)
        add_encodings(ve)  #ie: ["vp8", "h264"]
        #Pithon Imaging Libary:
        enc_pillow = get_codec("enc_pillow")
        if enc_pillow:
            pil_encs = enc_pillow.get_encodings()
            add_encodings(pil_encs)
            #Note: webp will only be enabled if we have a Python-PIL fallback
            #(either "webp" or "png")
            if has_codec("enc_webp") and ("webp" in pil_encs or "png" in pil_encs):
                add_encodings(["webp"])
                if "webp" not in self.lossless_mode_encodings:
                    self.lossless_mode_encodings.append("webp")
        #look for video encodings with lossless mode:
        for e in ve:
            for colorspace,especs in getVideoHelper().get_encoder_specs(e).items():
                for espec in especs:
                    if espec.has_lossless_mode:
                        if e not in self.lossless_mode_encodings:
                            log("found lossless mode for encoding %s with %s and colorspace %s", e, espec, colorspace)
                            self.lossless_mode_encodings.append(e)
                            break
        #now update the variables:
        self.encodings = encs
        self.core_encodings = core_encs
        self.lossless_encodings = [x for x in self.core_encodings if (x.startswith("png") or x.startswith("rgb") or x=="webp")]
        pref = [x for x in PREFERED_ENCODING_ORDER if x in self.encodings]
        if pref:
            self.default_encoding = pref[0]
        else:
            self.default_encoding = None


    def init_encoding(self, cmdline_encoding):
        if cmdline_encoding and cmdline_encoding not in self.encodings:
            log.warn("ignored invalid default encoding option: %s", cmdline_encoding)
        else:
            self.default_encoding = cmdline_encoding

    def init_printing(self):
        if not self.printing:
            return
        try:
            from xpra.platform import pycups_printing
            pycups_printing.set_lpadmin_command(self.lpadmin)
            pycups_printing.set_lpinfo_command(self.lpinfo)
            if self.postscript_printer:
                pycups_printing.add_printer_def("application/postscript", self.postscript_printer)
            if self.pdf_printer:
                pycups_printing.add_printer_def("application/pdf", self.pdf_printer)
            self.printing = pycups_printing.validate_setup()
        except ImportError as e:
            printlog("printing module is not installed: %s", e)
            self.printing = False
        except Exception:
            printlog.error("Error: failed to set lpadmin and lpinfo commands", exc_info=True)
            self.printing = False
        #verify that we can talk to the socket:
        if self.printing and self.auth_class and self.auth_class!="none":
            log.warn("Warning: printing conflicts with socket authentication module '%s'", getattr(self.auth_class, "auth_name", self.auth_class))
            self.printing = False
        printlog("init_printing() printing=%s", self.printing)

    def init_webcam(self):
        if not self.webcam_forwarding:
            return
        try:
            from xpra.codecs.pillow.decode import get_encodings            
            self.webcam_encodings = get_encodings()
        except Exception as e:
            webcamlog.error("Error: webcam forwarding disabled:")
            webcamlog.error(" %s", e)
            self.webcam_forwarding = False
        self.virtual_video_devices = self.init_virtual_video_devices()
        if self.virtual_video_devices==0:
            self.webcam_forwarding = False

    def init_virtual_video_devices(self):
        webcamlog("init_virtual_video_devices")
        if os.name!="posix":
            return 0
        try:
            from xpra.codecs.v4l2.pusher import Pusher
            assert Pusher
        except ImportError as e:
            webcamlog.error("Error: failed to import the virtual video module:")
            webcamlog.error(" %s", e)
            return 0
        try:
            from xpra.platform.xposix.webcam_util import get_virtual_video_devices
        except ImportError as e:
            webcamlog.warn("Warning: cannot load webcam components")
            webcamlog.warn(" %s", e)
            webcamlog.warn(" webcam forwarding disabled")
            return 0
        devices = get_virtual_video_devices()
        webcamlog.info("found %i virtual video device%s", len(devices), engs(devices))
        return len(devices)

    def init_uuid(self):
        # Define a server UUID if needed:
        self.uuid = self.get_uuid()
        if not self.uuid:
            self.uuid = unicode(get_hex_uuid())
            self.save_uuid()
        log("server uuid is %s", self.uuid)

    def get_uuid(self):
        return  None

    def save_uuid(self):
        pass

    def init_notification_forwarder(self):
        log("init_notification_forwarder() enabled=%s", self.notifications)
        if self.notifications and os.name=="posix" and not sys.platform.startswith("darwin"):
            try:
                from xpra.dbus.notifications_forwarder import register
                self.notifications_forwarder = register(self.notify_callback, self.notify_close_callback)
                if self.notifications_forwarder:
                    log.info("D-Bus notification forwarding is available")
                    log("%s", self.notifications_forwarder)
            except Exception as e:
                if str(e).endswith("is already claimed on the session bus"):
                    log.warn("Warning: cannot forward notifications, the interface is already claimed")
                else:
                    log.warn("Warning: failed to load or register our dbus notifications forwarder:")
                    log.warn(" %s", e)
                log.warn(" if you do not have a dedicated dbus session for this xpra instance,")
                log.warn(" use the 'notifications=no' option")

    def init_pulseaudio(self):
        soundlog("init_pulseaudio() pulseaudio=%s, pulseaudio_command=%s", self.pulseaudio, self.pulseaudio_command)
        if not self.pulseaudio:
            return
        started_at = time.time()
        def pulseaudio_warning():
            soundlog.warn("Warning: pulseaudio has terminated shortly after startup.")
            soundlog.warn(" pulseaudio is limited to a single instance per user account,")
            soundlog.warn(" and one may be running already for user '%s'", get_username())
            soundlog.warn(" to avoid this warning, either fix the pulseaudio command line")
            soundlog.warn(" or use the 'pulseaudio=no' option")
        def pulseaudio_ended(proc):
            soundlog("pulseaudio_ended(%s) pulseaudio_proc=%s, returncode=%s", proc, self.pulseaudio_proc, proc.returncode)
            if self.pulseaudio_proc is None:
                #cleared by cleanup already, ignore
                return
            elapsed = time.time()-started_at
            if elapsed<2:
                self.timeout_add(1000, pulseaudio_warning)
            else:
                soundlog.warn("Warning: the pulseaudio server process has terminated after %i seconds", int(elapsed))
            self.pulseaudio_proc = None
        import subprocess
        env = self.get_child_env()
        self.pulseaudio_proc = subprocess.Popen(self.pulseaudio_command, stdin=None, env=env, shell=True, close_fds=True)
        self.add_process(self.pulseaudio_proc, "pulseaudio", self.pulseaudio_command, ignore=True, callback=pulseaudio_ended)
        if self.pulseaudio_proc:
            soundlog.info("pulseaudio server started with pid %s", self.pulseaudio_proc.pid)
            def configure_pulse():
                p = self.pulseaudio_proc
                if p is None or p.poll() is not None:
                    return
                for i, x in enumerate(self.pulseaudio_configure_commands):
                    proc = subprocess.Popen(x, stdin=None, env=env, shell=True, close_fds=True)
                    self.add_process(proc, "pulseaudio-configure-command-%i" % i, x, ignore=True)
            self.timeout_add(2*1000, configure_pulse)

    def cleanup_pulseaudio(self):
        proc = self.pulseaudio_proc
        if not proc:
            return
        soundlog("cleanup_pa() process.poll()=%s, pid=%s", proc.poll(), proc.pid)
        if self.is_child_alive(proc):
            self.pulseaudio_proc = None
            soundlog.info("stopping pulseaudio with pid %s", proc.pid)
            try:
                #first we try pactl (required on Ubuntu):
                from xpra.scripts.exec_util import safe_exec
                r, _, _ = safe_exec(["pactl", "exit"])
                #warning: pactl will return 0 whether it succeeds or not...
                #but we can't kill the process because Ubuntu starts a new one
                if r!=0:
                    #fallback to using SIGINT:
                    proc.terminate()
            except:
                #only log the full stacktrace if the process failed to terminate:
                full_trace = self.is_child_alive(proc)
                soundlog.warn("error trying to stop pulseaudio", exc_info=full_trace)

    def init_sound_options(self, opts):
        self.supports_speaker = sound_option(opts.speaker) in ("on", "off")
        self.supports_microphone = sound_option(opts.microphone) in ("on", "off")
        def sound_option_or_all(*args):
            return []
        if self.supports_speaker or self.supports_microphone:
            try:
                from xpra.sound.gstreamer_util import sound_option_or_all
                from xpra.sound.wrapper import query_sound
                self.sound_properties = query_sound()
                assert self.sound_properties, "query did not return any data"
                def vinfo(k):
                    val = self.sound_properties.get(k)
                    assert val, "%s not found in sound properties" % k
                    return ".".join(val[:2])
                log.info("GStreamer version %s for Python %s", vinfo(b"gst.version"), vinfo(b"python.version"))
            except Exception as e:
                soundlog.error("Error: failed to query sound subsystem:")
                soundlog.error(" %s", e)
                self.speaker_allowed = False
                self.microphone_allowed = False
        self.sound_source_plugin = opts.sound_source
        encoders = self.sound_properties.strlistget("encoders", [])
        decoders = self.sound_properties.strlistget("decoders", [])
        self.speaker_codecs = sound_option_or_all("speaker-codec", opts.speaker_codec, encoders)
        self.microphone_codecs = sound_option_or_all("microphone-codec", opts.microphone_codec, decoders)
        if not self.speaker_codecs:
            self.supports_speaker = False
        if not self.microphone_codecs:
            self.supports_microphone = False
        if bool(self.sound_properties):
            try:
                from xpra.sound.pulseaudio.pulseaudio_util import set_icon_path, get_info as get_pa_info
                self.sound_properties.update(get_pa_info())
                set_icon_path(get_icon_filename("xpra.png"))
            except ImportError as e:
                if os.name=="posix" and not sys.platform.startswith("darwin"):
                    log.warn("Warning: failed to set pulseaudio tagging icon:")
                    log.warn(" %s", e)
        soundlog("init_sound_options speaker: supported=%s, encoders=%s", self.supports_speaker, csv(self.speaker_codecs))
        soundlog("init_sound_options microphone: supported=%s, decoders=%s", self.supports_microphone, csv(self.microphone_codecs))
        soundlog("init_sound_options sound properties=%s", self.sound_properties)

    def init_clipboard(self):
        clipboardlog("init_clipboard() enabled=%s, filter file=%s", self.supports_clipboard, self.clipboard_filter_file)
        ### Clipboard handling:
        self._clipboard_helper = None
        self._clipboard_client = None
        self._clipboards = []
        if not self.supports_clipboard:
            return
        from xpra.platform.features import CLIPBOARDS
        clipboard_filter_res = []
        if self.clipboard_filter_file:
            if not os.path.exists(self.clipboard_filter_file):
                clipboardlog.error("invalid clipboard filter file: '%s' does not exist - clipboard disabled!", self.clipboard_filter_file)
                return
            try:
                with open(self.clipboard_filter_file, "r" ) as f:
                    for line in f:
                        clipboard_filter_res.append(line.strip())
                    clipboardlog("loaded %s regular expressions from clipboard filter file %s", len(clipboard_filter_res), self.clipboard_filter_file)
            except:
                clipboardlog.error("error reading clipboard filter file %s - clipboard disabled!", self.clipboard_filter_file, exc_info=True)
                return
        try:
            from xpra.clipboard.gdk_clipboard import GDKClipboardProtocolHelper
            kwargs = {"filters" : clipboard_filter_res}
            self._clipboard_helper = GDKClipboardProtocolHelper(self.send_clipboard_packet, self.clipboard_progress, **kwargs)
            self._clipboards = CLIPBOARDS
        except Exception:
            #clipboardlog("gdk clipboard helper failure", exc_info=True)
            clipboardlog.error("Error: failed to setup clipboard helper", exc_info=True)

    def init_keyboard(self):
        keylog("init_keyboard()")
        ## These may get set by the client:
        self.xkbmap_mod_meanings = {}

        self.keyboard_config = None
        self.keymap_changing = False            #to ignore events when we know we are changing the configuration
        self.keyboard_sync = True
        self.key_repeat_delay = -1
        self.key_repeat_interval = -1
        #store list of currently pressed keys
        #(using a dict only so we can display their names in debug messages)
        self.keys_pressed = {}
        self.keys_timedout = {}
        #timers for cancelling key repeat when we get jitter
        self.key_repeat_timer = None
        self.watch_keymap_changes()

    def watch_keymap_changes(self):
        pass

    def init_dbus_helper(self):
        if not self.supports_dbus_proxy:
            return
        try:
            from xpra.dbus.helper import DBusHelper
            self.dbus_helper = DBusHelper()
            self.rpc_handlers["dbus"] = self._handle_dbus_rpc
        except Exception as e:
            log.warn("cannot load dbus helper: %s", e)
            self.supports_dbus_proxy = False

    def init_dbus_server(self):
        if not self.dbus_control:
            return
        try:
            from xpra.server.dbus.dbus_server import DBUS_Server
            self.dbus_server = DBUS_Server(self, os.environ.get("DISPLAY", "").lstrip(":"))
            dbuslog("init_dbus_server() DBUS_Server=%s", self.dbus_server)
        except Exception as e:
            dbuslog.error("Error setting up our dbus server:", exc_info=True)
            dbuslog.error(" %s", e)


    def load_existing_windows(self, system_tray):
        pass

    def is_shown(self, window):
        return True

    def init_packet_handlers(self):
        ServerCore.init_packet_handlers(self)
        self._authenticated_packet_handlers = {
            "set-clipboard-enabled":                self._process_clipboard_enabled_status,
            "set-keyboard-sync-enabled":            self._process_keyboard_sync_enabled_status,
            "damage-sequence":                      self._process_damage_sequence,
            "ping":                                 self._process_ping,
            "ping_echo":                            self._process_ping_echo,
            "set-cursors":                          self._process_set_cursors,
            "set-notify":                           self._process_set_notify,
            "set-bell":                             self._process_set_bell,
            "logging":                              self._process_logging,
            "command_request":                      self._process_command_request,
            "printers":                             self._process_printers,
            "send-file":                            self._process_send_file,
            "webcam-start":                         self._process_webcam_start,
            "webcam-stop":                          self._process_webcam_stop,
            "webcam-frame":                         self._process_webcam_frame,
          }
        self._authenticated_ui_packet_handlers = self._default_packet_handlers.copy()
        self._authenticated_ui_packet_handlers.update({
            #windows:
            "map-window":                           self._process_map_window,
            "unmap-window":                         self._process_unmap_window,
            "configure-window":                     self._process_configure_window,
            "close-window":                         self._process_close_window,
            "focus":                                self._process_focus,
            #keyboard:
            "key-action":                           self._process_key_action,
            "key-repeat":                           self._process_key_repeat,
            "layout-changed":                       self._process_layout,
            "keymap-changed":                       self._process_keymap,
            #mouse:
            "button-action":                        self._process_button_action,
            "pointer-position":                     self._process_pointer_position,
            #attributes / settings:
            "server-settings":                      self._process_server_settings,
            "quality":                              self._process_quality,
            "min-quality":                          self._process_min_quality,
            "speed":                                self._process_speed,
            "min-speed":                            self._process_min_speed,
            "set_deflate":                          self._process_set_deflate,
            "desktop_size":                         self._process_desktop_size,
            "encoding":                             self._process_encoding,
            "suspend":                              self._process_suspend,
            "resume":                               self._process_resume,
            #dbus:
            "rpc":                                  self._process_rpc,
            #sound:
            "sound-control":                        self._process_sound_control,
            "sound-data":                           self._process_sound_data,
            #requests:
            "shutdown-server":                      self._process_shutdown_server,
            "exit-server":                          self._process_exit_server,
            "buffer-refresh":                       self._process_buffer_refresh,
            "screenshot":                           self._process_screenshot,
            "info-request":                         self._process_info_request,
            "start-command":                        self._process_start_command,
            "print":                                self._process_print,
            # Note: "clipboard-*" packets are handled via a special case..
            })

    def init_aliases(self):
        packet_types = list(self._default_packet_handlers.keys())
        packet_types += list(self._authenticated_packet_handlers.keys())
        packet_types += list(self._authenticated_ui_packet_handlers.keys())
        self.do_init_aliases(packet_types)

    def init_control_commands(self):
        super(ServerBase, self).init_control_commands()
        def parse_boolean_value(v):
            if str(v).lower() in TRUE_OPTIONS:
                return True
            elif str(v).lower() in FALSE_OPTIONS:
                return False
            else:
                raise ControlError("a boolean is required, not %s" % v)

        from xpra.util import parse_scaling_value, from0to100
        for cmd in (
            ArgsControlCommand("focus",                 "give focus to the window id",      validation=[int]),
            #window source:
            ArgsControlCommand("suspend",               "suspend screen updates",           max_args=0),
            ArgsControlCommand("resume",                "resume screen updates",            max_args=0),
            ArgsControlCommand("ungrab",                "cancels any grabs",                max_args=0),
            #server globals:
            ArgsControlCommand("idle-timeout",          "set the idle tiemout",             validation=[int]),
            ArgsControlCommand("server-idle-timeout",   "set the server idle timeout",      validation=[int]),
            ArgsControlCommand("start",                 "executes the command arguments in the server context", min_args=1),
            ArgsControlCommand("start-child",           "executes the command arguments in the server context, as a 'child' (honouring exit-with-children)", min_args=1),
            #network and transfers:
            ArgsControlCommand("print",                 "sends the file to the client(s) for printing", min_args=3),
            ArgsControlCommand("send-file",             "sends the file to the client(s)",  min_args=3),
            ArgsControlCommand("compression",           "sets the packet compressor",       min_args=1, max_args=1),
            ArgsControlCommand("encoder",               "sets the packet encoder",          min_args=1, max_args=1),
            #session and clients:
            ArgsControlCommand("client",                "forwards a control command to the client(s)", min_args=1),
            ArgsControlCommand("name",                  "set the session name",             min_args=1, max_args=1),
            ArgsControlCommand("key",                   "press or unpress a key",           min_args=1, max_args=2),
            ArgsControlCommand("sound-output",          "control sound forwarding",         min_args=1, max_args=2),
            #windows:
            ArgsControlCommand("workspace",             "move a window to a different workspace", min_args=2, max_args=2, validation=[int, int]),
            ArgsControlCommand("scaling-control",       "set the scaling-control aggressiveness (from 0 to 100)", min_args=1, validation=[from0to100]),
            ArgsControlCommand("scaling",               "set a specific scaling value",     min_args=1, validation=[parse_scaling_value]),
            ArgsControlCommand("auto-refresh",          "set a specific auto-refresh value", min_args=1, validation=[float]),
            ArgsControlCommand("refresh",               "refresh some or all windows",      min_args=0),
            ArgsControlCommand("encoding",              "picture encoding",                 min_args=1, max_args=1),
            ArgsControlCommand("video-region-enabled",  "enable video region",              min_args=2, max_args=2, validation=[int, parse_boolean_value]),
            ArgsControlCommand("video-region-detection","enable video detection",           min_args=2, max_args=2, validation=[int, parse_boolean_value]),
            ArgsControlCommand("video-region",          "set the video region",             min_args=5, max_args=5, validation=[int, int, int, int, int]),
            ArgsControlCommand("lock-batch-delay",      "set a specific batch delay for a window",       min_args=2, max_args=2, validation=[int, int]),
            ArgsControlCommand("unlock-batch-delay",    "let the heuristics calculate the batch delay again for a window (following a 'lock-batch-delay')",  min_args=1, max_args=1, validation=[int]),
            ):
            cmd.do_run = getattr(self, "control_command_%s" % cmd.name.replace("-", "_"))
            self.control_commands[cmd.name] = cmd
        #encoding bits:
        for name in ("quality", "min-quality", "speed", "min-speed"):
            fn = getattr(self, "control_command_%s" % name.replace("-", "_"))
            self.control_commands[name] = ArgsControlCommand(name, "set encoding %s (from 0 to 100)" % name, run=fn, min_args=1, max_args=1, validation=[from0to100])


    def server_is_ready(self):
        ServerCore.server_is_ready(self)
        self.server_event("ready")


    def run(self):
        if self.send_pings:
            self.timeout_add(1000, self.send_ping)
        else:
            self.timeout_add(10*1000, self.send_ping)
        return ServerCore.run(self)


    def get_child_env(self):
        #subclasses may add more items (ie: fakexinerama)
        env = os.environ.copy()
        env.update(self.env)
        return env

    def get_full_child_command(self, cmd, use_wrapper=True):
        #make sure we have it as a list:
        if type(cmd) not in (list, tuple):
            cmd = [str(cmd)]
        if not use_wrapper or not self.exec_wrapper:
            return cmd
        return self.exec_wrapper + cmd

    def exec_start_commands(self):
        execlog("exec_start_commands() start=%s, start_child=%s", self.start_commands, self.start_child_commands)
        self._exec_commands(self.start_commands, self.start_child_commands)

    def exec_after_connect_commands(self):
        execlog("exec_after_connect_commands() start=%s, start_child=%s", self.start_after_connect, self.start_child_after_connect)
        self._exec_commands(self.start_after_connect, self.start_child_after_connect)

    def exec_on_connect_commands(self):
        execlog("exec_on_connect_commands() start=%s, start_child=%s", self.start_on_connect, self.start_child_commands)
        self._exec_commands(self.start_on_connect, self.start_child_on_connect)

    def _exec_commands(self, start_list, start_child_list):
        if start_list:
            for x in start_list:
                if x:
                    self.start_child(x, x, ignore=True)
        if start_child_list:
            for x in start_child_list:
                if x:
                    self.start_child(x, x, ignore=False)

    def start_child(self, name, child_cmd, ignore=False, callback=None, use_wrapper=True, shell=None, **kwargs):
        execlog("start_child%s", (name, child_cmd, ignore, callback, use_wrapper, shell, kwargs))
        import subprocess
        env = self.get_child_env()
        try:
            if shell is None:
                shell = not use_wrapper or not self.exec_wrapper
            real_cmd = self.get_full_child_command(child_cmd, use_wrapper)
            proc = subprocess.Popen(real_cmd, stdin=subprocess.PIPE, env=env, shell=shell, cwd=self.exec_cwd, close_fds=True, **kwargs)
            self.add_process(proc, name, real_cmd, ignore=ignore, callback=callback)
            execlog("pid(%s)=%s", real_cmd, proc.pid)
            if not ignore:
                execlog.info("started command '%s' with pid %s", " ".join(real_cmd), proc.pid)
            return proc
        except OSError as e:
            execlog.error("Error spawning child '%s': %s\n" % (child_cmd, e))
            return None

    def add_process(self, process, name, command, ignore=False, callback=None):
        self.child_reaper.add_process(process, name, command, ignore, callback=callback)

    def is_child_alive(self, proc):
        return proc is not None and proc.poll() is None

    def reaper_exit(self):
        if self.exit_with_children:
            execlog.info("all children have exited and --exit-with-children was specified, exiting")
            self.idle_add(self.clean_quit)


    def do_cleanup(self, *args):
        if self.notifications_forwarder:
            thread.start_new_thread(self.notifications_forwarder.release, ())
            self.notifications_forwarder = None
        getVideoHelper().cleanup()
        reaper_cleanup()
        self.cleanup_pulseaudio()
        self.stop_virtual_webcam()
        ds = self.dbus_server
        if ds:
            ds.cleanup()
            self.dbus_server = None


    def add_listen_socket(self, socktype, socket):
        raise NotImplementedError()


    def _process_exit_server(self, proto, packet):
        log.info("Exiting in response to client request")
        self.cleanup_all_protocols(SERVER_EXIT)
        self.timeout_add(500, self.clean_quit, ServerCore.EXITING_CODE)

    def _process_shutdown_server(self, proto, packet):
        log.info("Shutting down in response to client request")
        self.cleanup_all_protocols(SERVER_SHUTDOWN)
        self.timeout_add(500, self.clean_quit)

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
        self.server_event("connection-lost", source.uuid)
        source.close()
        remaining_sources = [x for x in self._server_sources.values() if x!=source]
        netlog("cleanup_source(%s) remaining sources: %s", source, remaining_sources)
        if len(remaining_sources)==0:
            self.last_client_exited()

    def last_client_exited(self):
        if self.exit_with_client:
            netlog.info("Last client has disconnected, terminating")
            self.quit(False)
        else:
            netlog.info("xpra client disconnected.")
            self.reset_server_timeout(True)

    def get_all_protocols(self):
        return list(self._potential_protocols) + list(self._server_sources.keys())


    def is_timedout(self, protocol):
        v = ServerCore.is_timedout(self, protocol) and protocol not in self._server_sources
        netlog("is_timedout(%s)=%s", protocol, v)
        return v

    def no_more_clients(self):
        #so it is now safe to clear them:
        #(this may fail during shutdown - which is ok)
        try:
            self._clear_keys_pressed()
        except:
            pass
        self._focus(None, 0, [])


    def idle_timeout_cb(self, source):
        log("idle_timeout_cb(%s)", source)
        p = source.protocol
        if p:
            self.disconnect_client(p, IDLE_TIMEOUT)

    def idle_grace_timeout_cb(self, source):
        log("idle_grace_timeout_cb(%s)", source)
        timeout_nid = 2**16 + 2**8 + 1
        source.notify(0, timeout_nid, "xpra", 0, "", "This Xpra session will timeout soon", "Activate one of the windows to avoid this timeout", 10)
        source.go_idle()


    def _disconnect_proto_info(self, proto):
        #only log protocol info if there is more than one client:
        if len(self._server_sources)>1:
            return " %s" % proto
        return ""

    def _process_connection_lost(self, proto, packet):
        ServerCore._process_connection_lost(self, proto, packet)
        if self._clipboard_client and self._clipboard_client.protocol==proto:
            self._clipboard_client = None
        source = self.cleanup_protocol(proto)
        if len(self._server_sources)==0:
            self._clear_keys_pressed()
            self._focus(source, 0, [])
        sys.stdout.flush()


    def hello_oked(self, proto, packet, c, auth_caps):
        if c.boolget("screenshot_request"):
            self.send_screenshot(proto)
            return
        if c.boolget("info_request", False):
            flatten = not c.boolget("info-namespace", False)
            self.send_hello_info(proto, flatten)
            return

        detach_request  = c.boolget("detach_request", False)
        stop_request    = c.boolget("stop_request", False)
        exit_request    = c.boolget("exit_request", False)
        event_request   = c.boolget("event_request", False)
        print_request   = c.boolget("print_request", False)
        is_request = detach_request or stop_request or exit_request or event_request or print_request
        if not is_request:
            #"normal" connection, so log welcome message:
            log.info("Handshake complete; enabling connection")
        self.server_event("handshake-complete")

        # Things are okay, we accept this connection, and may disconnect previous one(s)
        # (but only if this is going to be a UI session - control sessions can co-exist)
        ui_client = c.boolget("ui_client", True)
        share_count = 0
        disconnected = 0
        for p,ss in self._server_sources.items():
            if detach_request and p!=proto:
                self.disconnect_client(p, DETACH_REQUEST)
                disconnected += 1
            elif ui_client and ss.ui_client:
                #check if existing sessions are willing to share:
                if not self.sharing:
                    self.disconnect_client(p, NEW_CLIENT, "this session does not allow sharing")
                    disconnected += 1
                elif not c.boolget("share"):
                    self.disconnect_client(p, NEW_CLIENT, "the new client does not wish to share")
                    disconnected += 1
                elif not ss.share:
                    self.disconnect_client(p, NEW_CLIENT, "this client had not enabled sharing")
                    disconnected += 1
                else:
                    share_count += 1

        if detach_request:
            self.disconnect_client(proto, DONE, "%i other clients have been disconnected" % disconnected)
            return

        if not is_request and ui_client:
            #a bit of explanation:
            #normally these things are synchronized using xsettings, which we handle already
            #but non-posix clients have no such thing and we don't won't to expose that as an interface (it's not very nice and very X11 specific)
            #also, clients may want to override what is in their xsettings..
            #so if the client specifies what it wants to use, we patch the xsettings with it
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
        set_socket_timeout(proto._conn, None)

        def drop_client(reason="unknown", *args):
            self.disconnect_client(proto, reason, *args)
        def get_window_id(wid):
            return self._window_to_id.get(wid)
        ServerSourceClass = self.get_server_source_class()
        ss = ServerSourceClass(proto, drop_client,
                          self.idle_add, self.timeout_add, self.source_remove,
                          self.idle_timeout, self.idle_timeout_cb, self.idle_grace_timeout_cb,
                          self._socket_dir, self.unix_socket_paths, self.dbus_control,
                          self.get_transient_for, self.get_focus, self.get_cursor_data,
                          get_window_id,
                          self.window_filters,
                          self.supports_mmap, self.av_sync,
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
            proto.max_packet_size = max(proto.max_packet_size, int(ss.file_transfer) * (1024 + ss.file_size_limit*1024*1024))
        except:
            #close it already
            ss.close()
            raise
        self._server_sources[proto] = ss
        #process ui half in ui thread:
        send_ui = ui_client and not is_request
        self.idle_add(self.parse_hello_ui, ss, c, auth_caps, send_ui, share_count)

    def accept_client(self, proto, c):
        ServerCore.accept_client(self, proto, c)
        #may need to bump file size limit for file transfers:
        proto.max_packet_size = max(proto.max_packet_size, int(self.file_transfer) * (1024 + self.file_size_limit*1024*1024))


    def get_server_source_class(self):
        from xpra.server.source import ServerSource
        return ServerSource

    def reset_window_filters(self):
        self.window_filters = []


    def parse_hello_ui(self, ss, c, auth_caps, send_ui, share_count):
        #adds try:except around parse hello ui code:
        try:
            self.do_parse_hello_ui(ss, c, auth_caps, send_ui, share_count)
        except Exception as e:
            #log exception but don't disclose internal details to the client
            p = ss.protocol
            log.error("server error processing new connection from %s: %s", p or ss, e, exc_info=True)
            if p:
                self.disconnect_client(p, SERVER_ERROR, "error accepting new connection")

    def do_parse_hello_ui(self, ss, c, auth_caps, send_ui, share_count):
        #process screen size (if needed)
        if send_ui:
            root_w, root_h = self.do_parse_screen_info(ss)
            self.parse_hello_ui_clipboard(ss, c)
            key_repeat = self.parse_hello_ui_keyboard(ss, c)
            self.parse_hello_ui_window_settings(ss, c)
        else:
            root_w, root_h = self.get_root_window_size()
            key_repeat = (0, 0)

        #send_hello will take care of sending the current and max screen resolutions
        self.send_hello(ss, root_w, root_h, key_repeat, auth_caps)

        if send_ui:
            # now we can set the modifiers to match the client
            self.send_windows_and_cursors(ss, share_count>0)
        self.client_startup_complete(ss)

    def client_startup_complete(self, ss):
        ss.startup_complete()
        self.server_event("startup-complete", ss.uuid)
        if not self.start_after_connect_done:
            self.start_after_connect_done = True
            self.exec_after_connect_commands()
        self.exec_on_connect_commands()

    def do_parse_screen_info(self, ss):
        dw, dh = None, None
        if ss.desktop_size:
            try:
                dw, dh = ss.desktop_size
                if not ss.screen_sizes:
                    screenlog.info(" client root window size is %sx%s", dw, dh)
                else:
                    screenlog.info(" client root window size is %sx%s with %s display%s:", dw, dh, len(ss.screen_sizes), engs(ss.screen_sizes))
                    log_screen_sizes(dw, dh, ss.screen_sizes)
            except:
                dw, dh = None, None
        sw, sh = self.set_best_screen_size()
        screenlog("set_best_screen_size()=%s", (sw, sh))
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
        self.calculate_desktops()
        self.calculate_workarea(w, h)
        self.set_desktop_geometry(w, h)
        return w, h

    def parse_hello_ui_clipboard(self, ss, c):
        #take the clipboard if no-one else has it yet:
        if not ss.clipboard_enabled:
            clipboardlog("client does not support clipboard")
            return
        if not self._clipboard_helper:
            clipboardlog("server does not support clipboard")
            return
        cc = self._clipboard_client
        if cc and not cc.is_closed():
            clipboardlog("another client already owns the clipboard")
            return
        self._clipboard_client = ss
        #deal with buggy win32 clipboards:
        if "clipboard.greedy" not in c:
            #old clients without the flag: take a guess based on platform:
            client_platform = c.strget("platform", "")
            greedy = client_platform.startswith("win") or client_platform.startswith("darwin")
        else:
            greedy = c.boolget("clipboard.greedy")
        self._clipboard_helper.set_greedy_client(greedy)
        want_targets = c.boolget("clipboard.want_targets")
        self._clipboard_helper.set_want_targets_client(want_targets)
        #the selections the client supports (default to all):
        from xpra.platform.features import CLIPBOARDS
        client_selections = c.strlistget("clipboard.selections", CLIPBOARDS)
        clipboardlog("client %s is the clipboard peer", ss)
        clipboardlog(" greedy=%s", greedy)
        clipboardlog(" want targets=%s", want_targets)
        clipboardlog(" server has selections: %s", csv(self._clipboards))
        clipboardlog(" client initial selections: %s", csv(client_selections))
        self._clipboard_helper.enable_selections(client_selections)

    def parse_hello_ui_keyboard(self, ss, c):
        #keyboard:
        ss.keyboard_config = self.get_keyboard_config(c)

        #so only activate this feature afterwards:
        self.keyboard_sync = c.boolget("keyboard_sync", True)
        key_repeat = c.intpair("key_repeat")
        self.set_keyboard_repeat(key_repeat)

        #always clear modifiers before setting a new keymap
        ss.make_keymask_match(c.strlistget("modifiers", []))
        self.set_keymap(ss)
        return key_repeat

    def parse_hello_ui_window_settings(self, ss, c):
        pass


    def server_event(self, *args):
        for s in self._server_sources.values():
            s.send_server_event(*args)


    def update_all_server_settings(self, reset=False):
        pass        #may be overriden in subclasses (ie: x11 server)


    def get_keyboard_config(self, props):
        return None

    def set_keyboard_repeat(self, key_repeat):
        pass

    def set_keymap(self, ss):
        pass

    def get_transient_for(self, window):
        return  None

    def send_windows_and_cursors(self, ss, sharing=False):
        pass

    def sanity_checks(self, proto, c):
        server_uuid = c.strget("server_uuid")
        if server_uuid:
            if server_uuid==self.uuid:
                self.send_disconnect(proto, "cannot connect a client running on the same display that the server it connects to is managing - this would create a loop!")
                return  False
            log.warn("This client is running within the Xpra server %s", server_uuid)
        return True

    def get_server_features(self):
        #these are flags that have been added over time with new versions
        #to expose new server features:
        return (
                #all these flags are assumed enabled in 0.17 (they are present in 0.14.x onwards):
                "window_refresh_config",
                "toggle_cursors_bell_notify",
                "toggle_keyboard_sync",
                "window_unmap",
                "xsettings-tuple",
                "encoding.generic",
                "event_request",
                "sound_sequence",
                "sound.eos-sequence",
                "notify-startup-complete",
                "suspend-resume",
                "server-events",
                "change-quality", "change-min-quality", "change-speed", "change-min-speed",
                #newer flags:
                "sound.ogg-latency-fix",
                "clipboard.enable-selections",
                "av-sync",
                "window-filters")

    def make_hello(self, source):
        capabilities = ServerCore.make_hello(self, source)
        capabilities["server_type"] = "base"
        if source.wants_display:
            capabilities.update({
                 "max_desktop_size"             : self.get_max_screen_size(),
                 })
        if source.wants_features:
            capabilities.update({
                 "clipboards"                   : self._clipboards,
                 "notifications"                : self.notifications,
                 "bell"                         : self.bell,
                 "cursors"                      : self.cursors,
                 "dbus_proxy"                   : self.supports_dbus_proxy,
                 "rpc-types"                    : self.rpc_handlers.keys(),
                 "sharing"                      : self.sharing,
                 "printer.attributes"           : ("printer-info", "device-uri"),
                 "start-new-commands"           : self.start_new_commands,
                 "exit-with-children"           : self.exit_with_children,
                 "av-sync.enabled"              : self.av_sync,
                 "webcam"                       : self.webcam_forwarding,
                 "webcam.encodings"             : self.webcam_encodings,
                 "virtual-video-devices"        : self.virtual_video_devices,
                 })
            capabilities.update(self.get_file_transfer_features())
            for x in self.get_server_features():
                capabilities[x] = True
        #this is a feature, but we would need the hello request
        #to know if it is really needed.. so always include it:
        capabilities["exit_server"] = True

        if source.wants_encodings:
            updict(capabilities, "encoding", codec_versions, "version")
        return capabilities

    def send_hello(self, server_source, root_w, root_h, key_repeat, server_cipher):
        capabilities = self.make_hello(server_source)
        if server_source.wants_encodings:
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
            clipboard = self._clipboard_helper is not None and self._clipboard_client == server_source
            capabilities["clipboard"] = clipboard
            clipboardlog("clipboard_helper=%s, clipboard_client=%s, source=%s, clipboard=%s", self._clipboard_helper, self._clipboard_client, server_source, clipboard)
            capabilities["remote-logging"] = self.remote_logging
        if self._reverse_aliases and server_source.wants_aliases:
            capabilities["aliases"] = self._reverse_aliases
        if server_cipher:
            capabilities.update(server_cipher)
        server_source.hello(capabilities)


    def _process_logging(self, proto, packet):
        assert self.remote_logging
        level, msg = packet[1:3]
        for x in msg.splitlines():
            clientlog.log(level, x)

    def _process_printers(self, proto, packet):
        ss = self._server_sources.get(proto)
        if ss is None:
            return
        printers = packet[1]
        ss.set_printers(printers, self.password_file, self.encryption, self.encryption_keyfile)


    #########################################
    # Control Commands
    #########################################

    def _process_command_request(self, proto, packet):
        """ client sent a command request through its normal channel """
        assert len(packet)>=2, "invalid command request packet (too small!)"
        #packet[0] = "control"
        #this may end up calling do_handle_command_request via the adapter
        code, msg = self.process_control_command(*packet[1:])
        commandlog("command request returned: %s (%s)", code, msg)


    def control_command_focus(self, wid):
        assert type(wid)==int, "argument should have been an int, but found %s" % type(wid)
        self._focus(None, wid, None)
        return "gave focus to window %s" % wid

    def control_command_suspend(self):
        for csource in list(self._server_sources.values()):
            csource.suspend(True, self._id_to_window)
        return "suspended %s clients" % len(self._server_sources)

    def control_command_resume(self):
        for csource in list(self._server_sources.values()):
            csource.resume(True, self._id_to_window)
        return "resumed %s clients" % len(self._server_sources)

    def control_command_ungrab(self):
        for csource in list(self._server_sources.values()):
            csource.pointer_ungrab(-1)
        return "ungrabbed %s clients" % len(self._server_sources)

    def control_command_idle_timeout(self, t):
        self.idle_timeout = t
        for csource in list(self._server_sources.values()):
            csource.idle_timeout = t
            csource.schedule_idle_timeout()
        return "idle-timeout set to %s" % t

    def control_command_server_idle_timeout(self, t):
        self.server_idle_timeout = t
        reschedule = len(self._server_sources)==0
        self.reset_server_timeout(reschedule)
        return "server-idle-timeout set to %s" % t

    def control_command_start(self, *args):
        return self.do_control_command_start(True, *args)
    def control_command_start_child(self, *args):
        return self.do_control_command_start(False, *args)
    def do_control_command_start(self, ignore, *args):
        if not self.start_new_commands:
            raise ControlError("this feature is currently disabled")
        proc = self.start_child(" ".join(args), args, ignore, shell=True)
        if not proc:
            raise ControlError("failed to start new child command %s" % str(args))
        return "new %scommand started with pid=%s" % (["child ", ""][ignore], proc.pid)

    def _control_get_sources(self, client_uuids_str, attr=None):
        #find the client uuid specified as a string:
        if client_uuids_str=="UI":
            sources = [ss for ss in self._server_sources.values() if ss.ui_client]
            client_uuids = [ss.uuid for ss in sources]
            notfound = []
        elif client_uuids_str=="*":
            sources = self._server_sources.values()
            client_uuids = [ss.uuid for ss in sources]
        else:
            client_uuids = client_uuids_str.split(",")
            sources = [ss for ss in self._server_sources.values() if ss.uuid in client_uuids]
            notfound = [x for x in client_uuids if x not in [ss.uuid for ss in sources]]
            if notfound:
                commandlog.warn("client connection not found for uuid(s): %s", notfound)
        return sources

    def control_command_send_file(self, filename, openit, client_uuids, maxbitrate=0):
        actual_filename = os.path.abspath(os.path.expanduser(filename))
        if not os.path.exists(actual_filename):
            raise ControlError("file '%s' does not exist" % filename)
        openit = str(openit).lower() in ("open", "true", "1")
        #find the client uuid specified:
        sources = self._control_get_sources(client_uuids)
        if not sources:
            raise ControlError("no clients found matching: %s" % client_uuids)
        data = load_binary_file(actual_filename)
        file_size_MB = len(data)//1024//1024
        if file_size_MB>self.file_size_limit:
            raise ControlError("file '%s' is too large: %iMB (limit is %iMB)" % (filename, file_size_MB, self.file_size_limit))
        for ss in sources:
            if ss.file_transfer:
                ss.send_file(filename, "", data, False, openit)
            else:
                log.warn("cannot send file, client %s does not support file transfers!", ss)
        return "file transfer of '%s' to %s initiated" % (filename, client_uuids)

    def control_command_print(self, filename, printer, client_uuids, maxbitrate=0, title="", *options_strs):
        actual_filename = os.path.abspath(os.path.expanduser(filename))
        try:
            stat = os.stat(actual_filename)
            printlog("os.stat(%s)=%s", actual_filename, stat)
        except os.error:
            printlog("os.stat(%s)", actual_filename, exc_info=True)
        if not os.path.exists(actual_filename):
            raise ControlError("file '%s' does not exist" % filename)
        sources = self._control_get_sources(client_uuids)
        if not sources:
            raise ControlError("no clients found matching: %s" % client_uuids)
        #parse options into a dict:
        options = {}
        for arg in options_strs:
            argp = arg.split("=", 1)
            if len(argp)==2 and len(argp[0])>0:
                options[argp[0]] = argp[1]
        data = load_binary_file(actual_filename)
        file_size_MB = len(data)//1024//1024
        if file_size_MB>self.file_size_limit:
            raise ControlError("file '%s' is too large: %iMB (limit is %iMB)" % (filename, file_size_MB, self.file_size_limit))
        for ss in sources:
            if ss.printing:
                ss.send_file(filename, "", data, True, True, options)
            else:
                printlog.warn("client %s does not support printing!", ss)
        return "printing to %s initiated" % client_uuids

    def control_command_compression(self, compression):
        c = compression.lower()
        from xpra.net import compression
        opts = compression.get_enabled_compressors()    #ie: [lz4, lzo, zlib]
        if c not in opts:
            raise ControlError("compressor argument must be one of: %s" % (", ".join(opts)))
        for cproto in list(self._server_sources.keys()):
            cproto.enable_compressor(c)
        self.all_send_client_command("enable_%s" % c)
        return "compressors set to %s" % compression

    def control_command_encoder(self, encoder):
        e = encoder.lower()
        from xpra.net import packet_encoding
        opts = packet_encoding.get_enabled_encoders()   #ie: [rencode, bencode, yaml]
        if e not in opts:
            raise ControlError("encoder argument must be one of: %s" % (", ".join(opts)))
        for cproto in list(self._server_sources.keys()):
            cproto.enable_encoder(e)
        self.all_send_client_command("enable_%s" % e)
        return "encoders set to %s" % encoder


    def all_send_client_command(self, *client_command):
        """ forwards the command to all clients """
        for source in list(self._server_sources.values()):
            """ forwards to *the* client, if there is *one* """
            if client_command[0] not in source.control_commands:
                commandlog.info("client command '%s' not forwarded to client %s (not supported)", client_command, source)
            else:
                source.send_client_command(*client_command)

    def control_command_client(self, *args):
        self.all_send_client_command(*args)
        return "client control command '%s' forwarded to clients" % str(args)

    def control_command_name(self, name):
        self.session_name = name
        commandlog.info("changed session name: %s", self.session_name)
        #self.all_send_client_command("name", name)    not supported by any clients, don't bother!
        return "session name set to %s" % name

    def _control_windowsources_from_args(self, *args):
        #converts the args to valid window ids,
        #then returns all the window sources for those wids
        if len(args)==0 or len(args)==1 and args[0]=="*":
            #default to all if unspecified:
            wids = list(self._id_to_window.keys())
        else:
            wids = []
            for x in args:
                try:
                    wid = int(x)
                except:
                    raise ControlError("invalid window id: %s" % x)
                if wid in self._id_to_window:
                    wids.append(wid)
                else:
                    commandlog("window id %s does not exist", wid)
        wss = {}
        for csource in list(self._server_sources.values()):
            for wid in wids:
                ws = csource.window_sources.get(wid)
                window = self._id_to_window.get(wid)
                if window and ws:
                    wss[ws] = window
        return wss

    def _set_encoding_property(self, name, value, *wids):
        for ws in self._control_windowsources_from_args(*wids).keys():
            fn = getattr(ws, "set_%s" % name.replace("-", "_"))   #ie: "set_quality"
            fn(value)
        #now also update the defaults:
        for csource in list(self._server_sources.values()):
            csource.default_encoding_options[name] = value
        return "%s set to %i" % (name, value)

    def control_command_quality(self, quality, *wids):
        return self._set_encoding_property("quality", quality)
    def control_command_min_quality(self, min_quality, *wids):
        return self._set_encoding_property("min-quality", min_quality)
    def control_command_speed(self, speed, *wids):
        return self._set_encoding_property("speed", speed)
    def control_command_min_speed(self, min_speed, *wids):
        return self._set_encoding_property("min-speed", min_speed)

    def control_command_auto_refresh(self, auto_refresh, *wids):
        delay = int(float(auto_refresh)*1000.0)      # ie: 0.5 -> 500 (milliseconds)
        for ws in self._control_windowsources_from_args(*wids).keys():
            ws.set_auto_refresh_delay(auto_refresh)
        return "auto-refresh delay set to %sms for windows %s" % (delay, wids)

    def control_command_refresh(self, *wids):
        for ws, window in self._control_windowsources_from_args(*wids).items():
            ws.full_quality_refresh(window, {})
        return "refreshed windows %s" % str(wids)

    def control_command_scaling_control(self, scaling_control, *wids):
        for ws, window in self._control_windowsources_from_args(*wids).items():
            ws.set_scaling_control(scaling_control)
            ws.refresh(window)
        return "scaling-control set to %s on windows %s" % (scaling_control, wids)

    def control_command_scaling(self, scaling, *wids):
        for ws, window in self._control_windowsources_from_args(*wids).items():
            ws.set_scaling(scaling)
            ws.refresh(window)
        return "scaling set to %s on windows %s" % (str(scaling), wids)

    def control_command_encoding(self, encoding, *args):
        strict = None       #means no change
        if len(args)>0 and args[0] in ("strict", "nostrict"):
            #remove "strict" marker
            strict = args[0]=="strict"
            args = args[1:]
        wids = args
        for ws, window in self._control_windowsources_from_args(*wids).items():
            ws.set_new_encoding(encoding, strict)
            ws.refresh(window, {})
        return "set encoding to %s%s for windows %s" % (encoding, ["", " (strict)"][int(strict or 0)], wids)


    def _control_video_subregions_from_wid(self, wid):
        if wid not in self._id_to_window:
            raise ControlError("invalid window %i" % wid)
        video_subregions = []
        for ws in self._control_windowsources_from_args(wid).keys():
            vs = getattr(ws, "video_subregion", None)
            if not vs:
                log.warn("Warning: cannot set video region enabled flag on window %i:", wid)
                log.warn(" no video subregion attribute found in %s", type(ws))
                continue
            video_subregions.append(vs)
        return video_subregions

    def control_command_video_region_enabled(self, wid, enabled):
        assert type(wid)==int, "window id is not an int"
        assert type(enabled)==bool, "enabled flag is not a boolean"
        for vs in self._control_video_subregions_from_wid(wid):
            vs.set_enabled(enabled)
        return "video region %s for window %i" % (["disabled", "enabled"][int(enabled)], wid)

    def control_command_video_region_detection(self, wid, detection):
        assert type(wid)==int, "window id is not an int"
        assert type(detection)==bool, "detection flag is not a boolean"
        for vs in self._control_video_subregions_from_wid(wid):
            vs.set_detection(detection)
        return "video region detection %s for window %i" % (["disabled", "enabled"][int(detection)], wid)

    def control_command_video_region(self, wid, x, y, w, h):
        for vs in self._control_video_subregions_from_wid(wid):
            vs.set_region(x, y, w, h)
        return "video region set to %s for window %i" % ((x, y, w, h), wid)

    def control_command_lock_batch_delay(self, wid, delay):
        for ws in self._control_windowsources_from_args(wid).keys():
            ws.lock_batch_delay(delay)

    def control_command_unlock_batch_delay(self, wid):
        for ws in self._control_windowsources_from_args(wid).keys():
            ws.unlock_batch_delay()


    def control_command_key(self, keycode_str, press = True):
        try:
            if keycode_str.startswith("0x"):
                keycode = int(keycode_str, 16)
            else:
                keycode = int(keycode_str)
            assert keycode>0 and keycode<=255
        except:
            raise ControlError("invalid keycode specified: '%s' (must be a number between 1 and 255)" % keycode_str)
        if press is not True:
            if press in ("1", "press"):
                press = True
            elif press in ("0", "unpress"):
                press = False
            else:
                raise ControlError("if present, the press argument must be one of: %s", ("1", "press", "0", "unpress"))
        self.fake_key(keycode, press)

    def control_command_sound_output(self, *args):
        msg = []
        for csource in list(self._server_sources.values()):
            msg.append("%s : %s" % (csource, csource.sound_control(*args)))
        return ", ".join(msg)

    def control_command_workspace(self, wid, workspace):
        window = self._id_to_window.get(wid)
        if not window:
            raise ControlError("window %s does not exist", wid)
        if "workspace" not in window.get_property_names():
            raise ControlError("cannot set workspace on window %s", window)
        if workspace<0:
            raise ControlError("invalid workspace value: %s", workspace)
        window.set_property("workspace", workspace)
        return "window %s moved to workspace %s" % (wid, workspace)


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


    def _process_send_file(self, proto, packet):
        #superclass does not take the protocol as argument:
        FileTransferHandler._process_send_file(self, packet)

    def _process_print(self, proto, packet):
        #ie: from the xpraforwarder we call this command:
        #command = ["xpra", "print", "socket:/path/tosocket", filename, mimetype, source, title, printer, no_copies, print_options]
        assert self.printing
        #printlog("_process_print(%s, %s)", proto, packet)
        if len(packet)<9:
            log.error("Error: invalid print packet, only %i arguments", len(packet))
            log.error(" %s", [repr_ellipsized(x) for x in packet])
            return
        filename, file_data, mimetype, source_uuid, title, printer, no_copies, print_options_str = packet[1:9]
        if len(mimetype)>=128:
            log.error("Error: invalid mimetype in print packet:")
            log.error(" %s", repr_ellipsized(mimetype))
            return
        printlog("process_print: %s", (filename, mimetype, "%s bytes" % len(file_data), source_uuid, title, printer, no_copies, print_options_str))
        printlog("process_print: got %s bytes for file %s", len(file_data), filename)
        #parse the print options:
        print_options = {}
        for x in print_options_str.split(" "):
            parts = x.split("=", 1)
            if len(parts)==2:
                print_options[parts[0]] = parts[1]
        u = hashlib.sha1()
        u.update(file_data)
        printlog("sha1 digest: %s", u.hexdigest())
        options = {"printer"    : printer,
                   "title"      : title,
                   "copies"     : no_copies,
                   "options"    : print_options,
                   "sha1"       : u.hexdigest()}
        printlog("parsed printer options: %s", options)

        sent = 0
        for ss in self._server_sources.values():
            if source_uuid!='*' and ss.uuid!=source_uuid:
                printlog("not sending to %s (wanted uuid=%s)", ss, source_uuid)
                continue
            if not ss.printing:
                if source_uuid!='*':
                    printlog.warn("Warning: printing is not enabled for:")
                    printlog.warn(" %s", ss)
                else:
                    printlog("printing is not enabled for %s", ss)
                continue
            if not ss.printers:
                printlog.warn("Warning: client %s does not have any printers", ss.uuid)
                continue
            if printer not in ss.printers:
                printlog.warn("Warning: client %s does not have a '%s' printer", ss.uuid, printer)
                continue
            printlog("sending file to %s for printing on %s", ss, printer)
            if ss.send_file(filename, mimetype, file_data, True, True, options):
                sent += 1
        #warn if not sent:
        if sent==0:
            l = printlog.warn
        else:
            l = printlog.info
        unit_str, v = to_std_unit(len(file_data), unit=1024)
        l("file %s (%i%sB) sent to %i client%s", filename, v, unit_str, sent, engs(sent))


    def _process_start_command(self, proto, packet):
        assert self.start_new_commands
        log("start new command: %s", packet)
        name, command, ignore = packet[1:4]
        proc = self.start_child(name, command, ignore)
        if len(packet)>=5:
            shared = packet[4]
            if proc and not shared:
                ss = self._server_sources.get(proto)
                assert ss
                log("adding filter: pid=%s for %s", proc.pid, proto)
                ss.add_window_filter("window", "pid", "=", proc.pid)
        log("process_start_command: proc=%s", proc)

    def _process_info_request(self, proto, packet):
        log("process_info_request(%s, %s)", proto, packet)
        #ignoring the list of client uuids supplied in packet[1]
        ss = self._server_sources.get(proto)
        if ss:
            def info_callback(_proto, info):
                assert proto==_proto
                ss.send_info_response(info)
            self.get_all_info(info_callback, proto, *packet[2:])

    def send_hello_info(self, proto, flatten=True):
        start = time.time()
        def cb(proto, info):
            self.do_send_info(proto, info, flatten)
            end = time.time()
            log.info("processed info request from %s in %ims", proto._conn, (end-start)*1000)
        self.get_all_info(cb, proto, self._id_to_window.keys())

    def get_ui_info(self, proto, wids=None, *args):
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
        return get_thread_info(proto, list(self._server_sources.keys()))


    def get_info(self, proto=None, client_uuids=None, wids=None, *args):
        start = time.time()
        info = ServerCore.get_info(self, proto)
        if client_uuids:
            sources = [ss for ss in self._server_sources.values() if ss.uuid in client_uuids]
        else:
            sources = self._server_sources.values()
        if not wids:
            wids = self._id_to_window.keys()
        log("info-request: sources=%s, wids=%s", sources, wids)
        info.update(self.do_get_info(proto, sources, wids))
        info.setdefault("dpi", {}).update({
                             "default"      : self.default_dpi,
                             "value"        : self.dpi,
                             "x"            : self.xdpi,
                             "y"            : self.ydpi
                             })
        info.setdefault("antialias", {}).update(self.antialias)
        info.setdefault("cursor", {}).update({"size" : self.cursor_size})
        log("ServerBase.get_info took %.1fms", 1000.0*(time.time()-start))
        return info


    def get_printing_info(self):
        d = {"enabled"   : self.printing,
             "lpadmin"   : self.lpadmin}
        if self.printing:
            from xpra.platform.printing import get_info
            d.update(get_info())
        return d

    def get_commands_info(self):
        return {
                "start"                     : self.start_commands,
                "start-child"               : self.start_child_commands,
                "start-after-connect"       : self.start_after_connect,
                "start-child-after-connect" : self.start_child_after_connect,
                "start-on-connect"          : self.start_on_connect,
                "start-child-on-connect"    : self.start_child_on_connect,
                "exit-with-children"        : self.exit_with_children,
                "start-after-connect-done"  : self.start_after_connect_done,
                "start-new"                 : self.start_new_commands,
                }

    def get_features_info(self):
        i = {
             "randr"            : self.randr,
             "cursors"          : self.cursors,
             "bell"             : self.bell,
             "notifications"    : self.notifications_forwarder is not None,
             "sharing"          : self.sharing,
             "pulseaudio"       : {
                                   ""           : self.pulseaudio,
                                   "command"    : self.pulseaudio_command,
                                   },
             "dbus_proxy"       : self.supports_dbus_proxy,
             "rpc-types"        : self.rpc_handlers.keys(),
             "clipboard"        : self.supports_clipboard,
             "idle_timeout"     : self.idle_timeout,
             "file-size-limit"  : self.file_size_limit,
             }
        for x in self.get_server_features():
            i[x] = True
        return i

    def get_encoding_info(self):
        return  {
             ""                     : self.encodings,
             "core"                 : self.core_encodings,
             "allowed"              : self.allowed_encodings,
             "lossless"             : self.lossless_encodings,
             "problematic"          : [x for x in self.core_encodings if x in PROBLEMATIC_ENCODINGS],
             "with_speed"           : list(set({"rgb32" : "rgb", "rgb24" : "rgb"}.get(x, x) for x in self.core_encodings if x in ("h264", "vp8", "vp9", "rgb24", "rgb32", "png", "png/P", "png/L"))),
             "with_quality"         : [x for x in self.core_encodings if x in ("jpeg", "webp", "h264", "vp8", "vp9")],
             "with_lossless_mode"   : self.lossless_mode_encodings}

    def get_keyboard_info(self):
        start = time.time()
        info = {
             "sync"             : self.keyboard_sync,
             "repeat"           : {
                                   "delay"      : self.key_repeat_delay,
                                   "interval"   : self.key_repeat_interval,
                                   },
             "keys_pressed"     : self.keys_pressed.values(),
             "modifiers"        : self.xkbmap_mod_meanings}
        kc = self.keyboard_config
        if kc:
            info.update(kc.get_info())
        log("ServerBase.get_keyboard_info took %ims", (time.time()-start)*1000)
        return info

    def get_clipboard_info(self):
        if self._clipboard_helper is None:
            return {}
        return self._clipboard_helper.get_info()

    def get_webcam_info(self):
        return {""                          : self.webcam_forwarding,
                "virtual-video-devices"     : self.virtual_video_devices}

    def do_get_info(self, proto, server_sources=None, window_ids=None):
        start = time.time()
        info = {"server" : {"python" : {"version" : python_platform.python_version()}}}

        def up(prefix, d):
            info[prefix] = d

        up("webcam",    self.get_webcam_info())
        up("file",      self.get_file_transfer_info())
        up("printing",  self.get_printing_info())
        up("commands",  self.get_commands_info())
        up("features",  self.get_features_info())
        up("clipboard", self.get_clipboard_info())
        up("keyboard",  self.get_keyboard_info())
        up("encodings", self.get_encoding_info())
        for k,v in codec_versions.items():
            info.setdefault("encoding", {}).setdefault(k, {})["version"] = v
        # csc and video encoders:
        up("video",     getVideoHelper().get_info())

        info.setdefault("state", {})["windows"] = len([window for window in list(self._id_to_window.values()) if window.is_managed()])
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
                sinfo.update(ss.get_window_info(window_ids))
                cinfo[i] = sinfo
            up("client", cinfo)
        log("ServerBase.do_get_info took %ims", (time.time()-start)*1000)
        return info

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
        return info


    def clipboard_progress(self, local_requests, remote_requests):
        assert self._clipboard_helper is not None
        if self._clipboard_client and self._clipboard_client.clipboard_notifications:
            log("sending clipboard-pending-requests=%s to %s", local_requests, self._clipboard_client)
            self._clipboard_client.send("clipboard-pending-requests", local_requests)

    def send_clipboard_packet(self, *parts):
        assert self._clipboard_helper is not None
        if self._clipboard_client:
            self._clipboard_client.send_clipboard(parts)

    def notify_callback(self, dbus_id, nid, app_name, replaces_nid, app_icon, summary, body, expire_timeout):
        assert self.notifications_forwarder
        log("notify_callback(%s,%s,%s,%s,%s,%s,%s,%s)", dbus_id, nid, app_name, replaces_nid, app_icon, summary, body, expire_timeout)
        for ss in self._server_sources.values():
            ss.notify(dbus_id, int(nid), str(app_name), int(replaces_nid), str(app_icon), str(summary), str(body), int(expire_timeout))

    def notify_close_callback(self, nid):
        assert self.notifications_forwarder
        log("notify_close_callback(%s)", nid)
        for ss in self._server_sources.values():
            ss.notify_close(int(nid))


    def _keys_changed(self, *args):
        if not self.keymap_changing:
            for ss in self._server_sources.values():
                ss.keys_changed()

    def _clear_keys_pressed(self):
        pass


    def _focus(self, server_source, wid, modifiers):
        focuslog("_focus(%s,%s)", wid, modifiers)

    def get_focus(self):
        #can be overriden by subclasses that do manage focus
        #(ie: not shadow servers which only have a single window)
        #default: no focus
        return -1


    def _update_metadata(self, window, pspec):
        metalog("updating metadata on %s: %s", window, pspec)
        wid = self._window_to_id[window]
        for ss in self._server_sources.values():
            ss.window_metadata(wid, window, pspec.name)


    def _add_new_window_common(self, window):
        props = window.get_dynamic_property_names()
        metalog("add_new_window_common(%s) watching for dynamic properties: %s", window, props)
        for prop in props:
            window.connect("notify::%s" % prop, self._update_metadata)
        wid = self._max_window_id
        self._max_window_id += 1
        self._window_to_id[window] = wid
        self._id_to_window[wid] = window
        return wid

    def _do_send_new_window_packet(self, ptype, window, geometry):
        wid = self._window_to_id[window]
        x, y, w, h = geometry
        for ss in self._server_sources.values():
            wprops = self.client_properties.get("%s|%s" % (wid, ss.uuid))
            ss.new_window(ptype, wid, window, x, y, w, h, wprops)


    def _screen_size_changed(self, screen):
        screenlog("_screen_size_changed(%s)", screen)
        #randr has resized the screen, tell the client (if it supports it)
        w, h = screen.get_width(), screen.get_height()
        screenlog("new screen dimensions: %s", (w, h))
        self.calculate_workarea(w, h)
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

    def set_best_screen_size(self):
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


    def _process_encoding(self, proto, packet):
        encoding = packet[1]
        ss = self._server_sources.get(proto)
        if ss is None:
            return
        if len(packet)>=3:
            #client specified which windows this is for:
            in_wids = packet[2]
            wids = []
            wid_windows = {}
            for wid in in_wids:
                if wid not in self._id_to_window:
                    continue
                wids.append(wid)
                wid_windows[wid] = self._id_to_window.get(wid)
        else:
            #apply to all windows:
            wids = None
            wid_windows = self._id_to_window
        ss.set_encoding(encoding, wids)
        self.refresh_windows(proto, wid_windows)


    def _process_rpc(self, proto, packet):
        ss = self._server_sources.get(proto)
        assert ss is not None
        rpc_type = packet[1]
        rpcid = packet[2]
        handler = self.rpc_handlers.get(rpc_type)
        if not handler:
            rpclog.error("Error: invalid rpc request of type '%s'", rpc_type)
            return
        rpclog("rpc handler for %s: %s", rpc_type, handler)
        try:
            handler(ss, *packet[2:])
        except Exception as e:
            rpclog.error("Error: cannot call %s handler %s:", rpc_type, handler, exc_info=True)
            ss.rpc_reply(rpc_type, rpcid, False, str(e))

    def _handle_dbus_rpc(self, ss, rpcid, _, bus_name, path, interface, function, args, *extra):
        assert self.supports_dbus_proxy, "server does not support dbus proxy calls"
        def native(args):
            return [self.dbus_helper.dbus_to_native(x) for x in (args or [])]
        def ok_back(*args):
            log("rpc: ok_back%s", args)
            ss.rpc_reply("dbus", rpcid, True, native(args))
        def err_back(*args):
            log("rpc: err_back%s", args)
            ss.rpc_reply("dbus", rpcid, False, native(args))
        self.dbus_helper.call_function(bus_name, path, interface, function, args, ok_back, err_back)


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

    def _process_screenshot(self, proto, packet):
        packet = self.make_screenshot_packet()
        ss = self._server_sources.get(proto)
        if packet and ss:
            ss.send(*packet)

    def make_screenshot_packet(self):
        return  None


    def _process_set_notify(self, proto, packet):
        assert self.notifications_forwarder is not None, "cannot toggle notifications: the feature is disabled"
        ss = self._server_sources.get(proto)
        if ss:
            ss.send_notifications = bool(packet[1])

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

    def _process_sound_control(self, proto, packet):
        ss = self._server_sources.get(proto)
        if ss:
            ss.sound_control(*packet[1:])

    def _process_sound_data(self, proto, packet):
        ss = self._server_sources.get(proto)
        if ss:
            ss.sound_data(*packet[1:])

    def _process_clipboard_enabled_status(self, proto, packet):
        clipboard_enabled = packet[1]
        ss = self._server_sources.get(proto)
        self.set_clipboard_enabled_status(ss, clipboard_enabled)

    def set_clipboard_enabled_status(self, ss, clipboard_enabled):
        if not self._clipboard_helper:
            log.warn("client toggled clipboard-enabled but we do not support clipboard at all! ignoring it")
            return
        assert self._clipboard_client==ss, \
                "the request to change the clipboard enabled status does not come from the clipboard owner!"
        self._clipboard_client.clipboard_enabled = clipboard_enabled
        log("toggled clipboard to %s", clipboard_enabled)

    def _process_keyboard_sync_enabled_status(self, proto, packet):
        self.keyboard_sync = bool(packet[1])
        keylog("toggled keyboard-sync to %s", self.keyboard_sync)


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
            client_properties = self.client_properties.setdefault("%s|%s" % (wid, ss.uuid), {})
            #filter out encoding properties, which are expected to be set everytime:
            ncp = {}
            for k,v in new_client_properties.items():
                if v is None:
                    log.warn("removing invalid None property for %s", k)
                    continue
                if not k.startswith("encoding"):
                    ncp[k] = v
            log("set_client_properties updating window %s with %s", wid, ncp)
            client_properties.update(ncp)


    def _process_focus(self, proto, packet):
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

    def _process_layout(self, proto, packet):
        layout, variant = packet[1:3]
        ss = self._server_sources.get(proto)
        if ss and ss.set_layout(layout, variant):
            self.set_keymap(ss, force=True)

    def _process_keymap(self, proto, packet):
        props = typedict(packet[1])
        ss = self._server_sources.get(proto)
        if ss is None:
            return
        log("received new keymap from client")
        kc = ss.keyboard_config
        if kc and kc.enabled:
            kc.parse_options(props)
            self.set_keymap(ss, True)
        modifiers = props.get("modifiers", [])
        ss.make_keymask_match(modifiers)

    def _process_key_action(self, proto, packet):
        wid, keyname, pressed, modifiers, keyval, _, client_keycode = packet[1:8]
        ss = self._server_sources.get(proto)
        if ss is None:
            return
        keycode = self.get_keycode(ss, client_keycode, keyname, modifiers)
        log("process_key_action(%s) server keycode=%s", packet, keycode)
        #currently unused: (group, is_modifier) = packet[8:10]
        self._focus(ss, wid, None)
        ss.make_keymask_match(modifiers, keycode, ignored_modifier_keynames=[keyname])
        #negative keycodes are used for key events without a real keypress/unpress
        #for example, used by win32 to send Caps_Lock/Num_Lock changes
        if keycode>0:
            self._handle_key(wid, pressed, keyname, keyval, keycode, modifiers)
        ss.user_event()

    def get_keycode(self, ss, client_keycode, keyname, modifiers):
        return ss.get_keycode(client_keycode, keyname, modifiers)

    def is_modifier(self, keyname, keycode):
        if keyname in DEFAULT_MODIFIER_MEANINGS.keys():
            return True
        #keyboard config should always exist if we are here?
        if self.keyboard_config:
            return self.keyboard_config.is_modifier(keycode)
        return False

    def fake_key(self, keycode, press):
        pass

    def _handle_key(self, wid, pressed, name, keyval, keycode, modifiers):
        """
            Does the actual press/unpress for keys
            Either from a packet (_process_key_action) or timeout (_key_repeat_timeout)
        """
        keylog("handle_key(%s,%s,%s,%s,%s,%s) keyboard_sync=%s", wid, pressed, name, keyval, keycode, modifiers, self.keyboard_sync)
        if pressed and (wid is not None) and (wid not in self._id_to_window):
            keylog("window %s is gone, ignoring key press", wid)
            return
        if keycode<0:
            keylog.warn("ignoring invalid keycode=%s", keycode)
            return
        if keycode in self.keys_timedout:
            del self.keys_timedout[keycode]
        def press():
            keylog("handle keycode pressing %s: key %s", keycode, name)
            self.keys_pressed[keycode] = name
            self.fake_key(keycode, True)
        def unpress():
            keylog("handle keycode unpressing %s: key %s", keycode, name)
            if keycode in self.keys_pressed:
                del self.keys_pressed[keycode]
            self.fake_key(keycode, False)
        is_mod = self.is_modifier(name, keycode)
        if pressed:
            if keycode not in self.keys_pressed:
                press()
                if not self.keyboard_sync and not is_mod:
                    #keyboard is not synced: client manages repeat so unpress
                    #it immediately unless this is a modifier key
                    #(as modifiers are synced via many packets: key, focus and mouse events)
                    unpress()
            else:
                keylog("handle keycode %s: key %s was already pressed, ignoring", keycode, name)
        else:
            if keycode in self.keys_pressed:
                unpress()
            else:
                keylog("handle keycode %s: key %s was already unpressed, ignoring", keycode, name)
        if not is_mod and self.keyboard_sync and self.key_repeat_delay>0 and self.key_repeat_interval>0:
            self._key_repeat(wid, pressed, name, keyval, keycode, modifiers, self.key_repeat_delay)

    def cancel_key_repeat_timer(self):
        if self.key_repeat_timer:
            self.source_remove(self.key_repeat_timer)
            self.key_repeat_timer = None

    def _key_repeat(self, wid, pressed, keyname, keyval, keycode, modifiers, delay_ms=0):
        """ Schedules/cancels the key repeat timeouts """
        self.cancel_key_repeat_timer()
        if pressed:
            delay_ms = min(1500, max(250, delay_ms))
            keylog("scheduling key repeat timer with delay %s for %s / %s", delay_ms, keyname, keycode)
            def _key_repeat_timeout(when):
                self.key_repeat_timer = None
                now = time.time()
                keylog("key repeat timeout for %s / '%s' - clearing it, now=%s, scheduled at %s with delay=%s", keyname, keycode, now, when, delay_ms)
                self._handle_key(wid, False, keyname, keyval, keycode, modifiers)
                self.keys_timedout[keycode] = now
            now = time.time()
            self.key_repeat_timer = self.timeout_add(delay_ms, _key_repeat_timeout, now)

    def _process_key_repeat(self, proto, packet):
        wid, keyname, keyval, client_keycode, modifiers = packet[1:6]
        ss = self._server_sources.get(proto)
        if ss is None:
            return
        keycode = ss.get_keycode(client_keycode, keyname, modifiers)
        #key repeat uses modifiers from a pointer event, so ignore mod_pointermissing:
        ss.make_keymask_match(modifiers)
        if not self.keyboard_sync:
            #this check should be redundant: clients should not send key-repeat without
            #having keyboard_sync enabled
            return
        if keycode not in self.keys_pressed:
            #the key is no longer pressed, has it timed out?
            when_timedout = self.keys_timedout.get(keycode, None)
            if when_timedout:
                del self.keys_timedout[keycode]
            now = time.time()
            if when_timedout and (now-when_timedout)<30:
                #not so long ago, just re-press it now:
                keylog("key %s/%s, had timed out, re-pressing it", keycode, keyname)
                self.keys_pressed[keycode] = keyname
                self.fake_key(keycode, True)
        self._key_repeat(wid, True, keyname, keyval, keycode, modifiers, self.key_repeat_interval)
        ss.user_event()


    def _move_pointer(self, wid, pos):
        raise NotImplementedError()

    def _process_mouse_common(self, proto, wid, pointer, modifiers):
        pass

    def _process_button_action(self, proto, packet):
        pass

    def _process_pointer_position(self, proto, packet):
        wid, pointer, modifiers = packet[1:4]
        ss = self._server_sources.get(proto)
        if ss is not None:
            ss.mouse_last_position = pointer
        self._process_mouse_common(proto, wid, pointer, modifiers)


    def _process_damage_sequence(self, proto, packet):
        packet_sequence = packet[1]
        if len(packet)>=6:
            wid, width, height, decode_time = packet[2:6]
            if len(packet)>=7:
                message = packet[6]
            else:
                message = ""
            ss = self._server_sources.get(proto)
            if ss:
                window = self._id_to_window.get(wid)
                ss.client_ack_damage(packet_sequence, wid, window, width, height, decode_time, message)


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
            log.warn("invalid window specified for refresh: %s", wid)
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

    def refresh_windows(self, proto, wid_windows, opts=None):
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

    def _process_quality(self, proto, packet):
        quality = packet[1]
        log("Setting quality to ", quality)
        ss = self._server_sources.get(proto)
        if ss:
            ss.set_quality(quality)
            self.refresh_windows(proto, self._id_to_window)

    def _process_min_quality(self, proto, packet):
        min_quality = packet[1]
        log("Setting min quality to ", min_quality)
        ss = self._server_sources.get(proto)
        if ss:
            ss.set_min_quality(min_quality)
            self.refresh_windows(proto, self._id_to_window)

    def _process_speed(self, proto, packet):
        speed = packet[1]
        log("Setting speed to ", speed)
        ss = self._server_sources.get(proto)
        if ss:
            ss.set_speed(speed)
            self.refresh_windows(proto, self._id_to_window)

    def _process_min_speed(self, proto, packet):
        min_speed = packet[1]
        log("Setting min speed to ", min_speed)
        ss = self._server_sources.get(proto)
        if ss:
            ss.set_min_speed(min_speed)
            self.refresh_windows(proto, self._id_to_window)


    def _process_map_window(self, proto, packet):
        log.info("_process_map_window(%s, %s)", proto, packet)

    def _process_unmap_window(self, proto, packet):
        log.info("_process_unmap_window(%s, %s)", proto, packet)

    def _process_close_window(self, proto, packet):
        log.info("_process_close_window(%s, %s)", proto, packet)

    def _process_configure_window(self, proto, packet):
        log.info("_process_configure_window(%s, %s)", proto, packet)


    def _process_webcam_start(self, proto, packet):
        assert self.webcam_forwarding
        ss = self._server_sources.get(proto)
        if not ss:
            webcamlog.warn("Warning: invalid client source for webcam start")
            return
        device, w, h = packet[1:4]
        log("starting webcam %sx%s", w, h)
        self.start_virtual_webcam(ss, device, w, h)

    def start_virtual_webcam(self, ss, device, w, h):
        assert w>0 and h>0
        from xpra.platform.xposix.webcam_util import get_virtual_video_devices
        devices = get_virtual_video_devices()
        if len(devices)==0:
            webcamlog.warn("Warning: cannot start webcam forwarding, no virtual devices found")
            ss.send_webcam_stop(device)
            return
        if self.webcam_forwarding_device:
            self.stop_virtual_webcam()
        device_str = devices.values()[0]
        try:
            from xpra.codecs.v4l2.pusher import Pusher, get_input_colorspaces    #@UnresolvedImport
            in_cs = get_input_colorspaces()
            p = Pusher()
            src_format = in_cs[0]
            p.init_context(w, h, w, src_format, device_str)
            self.webcam_forwarding_device = p
            webcamlog.info("webcam forwarding using %s", device_str)
            #this tell the client to start sending, and the size to use - which may have changed:
            ss.send_webcam_ack(device, 0, p.get_width(), p.get_height())
        except Exception as e:
            webcamlog.error("Error setting up webcam forwarding:")
            webcamlog.error(" %s", e)
            ss.send_webcam_stop(device, str(e))

    def _process_webcam_stop(self, proto, packet):
        device, message = (packet+[""])[1:3]
        webcamlog("stopping webcam device %s", ": ".join([str(x) for x in (device, message)]))
        if not self.webcam_forwarding_device:
            webcamlog.warn("Warning: cannot stop webcam device %s: no such context!", device)
            return
        self.stop_virtual_webcam()

    def stop_virtual_webcam(self):
        webcamlog("stop_virtual_webcam() webcam_forwarding_device=%s", self.webcam_forwarding_device)
        vfd = self.webcam_forwarding_device
        if vfd:
            self.webcam_forwarding_device = None
            vfd.clean()

    def _process_webcam_frame(self, proto, packet):
        device, frame_no, encoding, w, h, data = packet[1:7]
        assert encoding and w and h and data
        ss = self._server_sources.get(proto)
        if not ss:
            webcamlog.warn("Warning: invalid client source for webcam frame")
            return
        vfd = self.webcam_forwarding_device
        if not self.webcam_forwarding_device:
            webcamlog.warn("Warning: webcam forwarding is not active, dropping frame")
            ss.send_webcam_stop(device, "not started")
            return            
        try:
            from xpra.codecs.pillow.decode import get_encodings
            assert encoding in get_encodings(), "invalid encoding specified: %s (must be one of %s)" % (encoding, get_encodings())
            rgb_pixel_format = "RGBX"       #BGRX
            from PIL import Image
            buf = BytesIOClass(data)
            img = Image.open(buf)
            pixels = img.tobytes('raw', rgb_pixel_format)
            from xpra.codecs.image_wrapper import ImageWrapper
            bgrx_image = ImageWrapper(0, 0, w, h, pixels, rgb_pixel_format, 32, w*4, planes=ImageWrapper.PACKED)
            src_format = vfd.get_src_format()
            if not src_format:
                #closed / closing
                return
            #one of those two should be present
            try:
                from xpra.codecs.csc_cython.colorspace_converter import get_input_colorspaces, get_output_colorspaces, ColorspaceConverter        #@UnresolvedImport
            except ImportError:
                from xpra.codecs.csc_swscale.colorspace_converter import get_input_colorspaces, get_output_colorspaces, ColorspaceConverter        #@UnresolvedImport
            assert rgb_pixel_format in get_input_colorspaces(), "unsupported RGB pixel format %s" % rgb_pixel_format
            assert src_format in get_output_colorspaces(rgb_pixel_format), "unsupported output colourspace format %s" % src_format
            tw = vfd.get_width()
            th = vfd.get_height()
            csc = ColorspaceConverter()
            csc.init_context(w, h, rgb_pixel_format, tw, th, src_format)
            image = csc.convert_image(bgrx_image)
            vfd.push_image(image)
            #tell the client all is good:
            ss.send_webcam_ack(device, frame_no)
        except Exception as e:
            webcamlog("error on %ix%i frame %i using encoding %s", w, h, frame_no, encoding, exc_info=True)
            webcamlog.error("Error processing webcam frame:")
            if str(e):
                webcamlog.error(" %s", e)
            else:
                webcamlog.error("unknown error", exc_info=True)
            ss.send_webcam_stop(device, str(e))
            self.stop_virtual_webcam()


    def process_clipboard_packet(self, ss, packet):
        if not ss:
            #protocol has been dropped!
            return
        assert self._clipboard_client==ss, \
                "the clipboard packet '%s' does not come from the clipboard owner!" % packet[0]
        if not ss.clipboard_enabled:
            #this can happen when we disable clipboard in the middle of transfers
            #(especially when there is a clipboard loop)
            log.warn("received a clipboard packet from a source which does not have clipboard enabled!")
            return
        assert self._clipboard_helper, "received a clipboard packet but we do not support clipboard sharing"
        self.idle_add(self._clipboard_helper.process_clipboard_packet, packet)


    def process_packet(self, proto, packet):
        try:
            handler = None
            packet_type = packet[0]
            assert isinstance(packet_type, (str, unicode)), "packet_type %s is not a string: %s..." % (type(packet_type), str(packet_type)[:100])
            if packet_type.startswith("clipboard-"):
                handler = self.process_clipboard_packet
                ss = self._server_sources.get(proto)
                self.process_clipboard_packet(ss, packet)
                return
            if proto in self._server_sources:
                handlers = self._authenticated_packet_handlers
                ui_handlers = self._authenticated_ui_packet_handlers
            else:
                handlers = {}
                ui_handlers = self._default_packet_handlers
            handler = handlers.get(packet_type)
            if handler:
                netlog("process non-ui packet %s", packet_type)
                handler(proto, packet)
                return
            handler = ui_handlers.get(packet_type)
            if handler:
                netlog("will process ui packet %s", packet_type)
                self.idle_add(handler, proto, packet)
                return
            def invalid_packet():
                ss = self._server_sources.get(proto)
                if not self._closing and not proto._closed and (ss is None or not ss.is_closed()):
                    netlog.error("unknown or invalid packet type: %s from %s", packet_type, proto)
                if not ss:
                    proto.close()
            self.idle_add(invalid_packet)
        except KeyboardInterrupt:
            raise
        except:
            netlog.error("Unhandled error while processing a '%s' packet from peer using %s", packet_type, handler, exc_info=True)
