# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2013 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008, 2010 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import sys
import time
import ctypes

from xpra.log import Logger, debug_if_env
log = Logger()
soundlog = debug_if_env(log, "XPRA_SOUND_DEBUG")

from xpra.gtk_common.gobject_util import no_arg_signal
from xpra.deque import maxdeque
from xpra.client.client_base import XpraClientBase, EXIT_TIMEOUT, EXIT_MMAP_TOKEN_FAILURE
from xpra.client.client_tray import ClientTray
from xpra.client.keyboard_helper import KeyboardHelper
from xpra.platform.features import MMAP_SUPPORTED, SYSTEM_TRAY_SUPPORTED, CLIPBOARD_WANT_TARGETS, CLIPBOARD_GREEDY
from xpra.platform.gui import init as gui_init, ready as gui_ready, get_native_notifier_classes, get_native_tray_classes, get_native_system_tray_classes, get_native_tray_menu_helper_classes, ClientExtras
from xpra.scripts.config import HAS_SOUND, PREFERED_ENCODING_ORDER, get_codecs, codec_versions
from xpra.simple_stats import std_unit
from xpra.net.protocol import Compressed
from xpra.daemon_thread import make_daemon_thread
from xpra.os_util import set_application_name, thread, Queue
from xpra.util import nn
try:
    from xpra.clipboard.clipboard_base import ALL_CLIPBOARDS
except:
    ALL_CLIPBOARDS = []

DRAW_DEBUG = os.environ.get("XPRA_DRAW_DEBUG", "0")=="1"
FAKE_BROKEN_CONNECTION = os.environ.get("XPRA_FAKE_BROKEN_CONNECTION", "0")=="1"
PING_TIMEOUT = int(os.environ.get("XPRA_PING_TIMEOUT", "60"))

if sys.version > '3':
    unicode = str           #@ReservedAssignment


"""
Utility superclass for client classes which have a UI.
See gtk_client_base and its subclasses.
"""
class UIXpraClient(XpraClientBase):
    #NOTE: these signals aren't registered because this class
    #does not extend GObject.
    __gsignals__ = {
        "handshake-complete"        : no_arg_signal,
        "first-ui-received"         : no_arg_signal,

        "clipboard-toggled"         : no_arg_signal,
        "keyboard-sync-toggled"     : no_arg_signal,
        "speaker-changed"           : no_arg_signal,        #bitrate or pipeline state has changed
        "microphone-changed"        : no_arg_signal,        #bitrate or pipeline state has changed
        }

    def __init__(self):
        XpraClientBase.__init__(self)
        gui_init()
        self.start_time = time.time()
        self._window_to_id = {}
        self._id_to_window = {}
        self._ui_events = 0
        self.title = ""
        self.session_name = ""
        self.auto_refresh_delay = -1
        self.dpi = 96

        #draw thread:
        self._draw_queue = None
        self._draw_thread = None

        #statistics and server info:
        self.server_start_time = -1
        self.server_platform = ""
        self.server_actual_desktop_size = None
        self.server_max_desktop_size = None
        self.server_display = None
        self.server_randr = False
        self.server_auto_refresh_delay = 0
        self.pixel_counter = maxdeque(maxlen=1000)
        self.server_ping_latency = maxdeque(maxlen=1000)
        self.server_load = None
        self.client_ping_latency = maxdeque(maxlen=1000)
        self._server_ok = True
        self.last_ping_echoed_time = 0
        self.server_info_request = False
        self.server_last_info = None
        self.info_request_pending = False
        self.encoding = self.get_encodings()[0]

        #sound:
        self.speaker_allowed = HAS_SOUND
        self.speaker_enabled = False
        self.speaker_codecs = []
        if self.speaker_allowed:
            self.speaker_codecs = get_codecs(True, False)
            self.speaker_allowed = len(self.speaker_codecs)>0
        self.microphone_allowed = HAS_SOUND
        self.microphone_enabled = False
        self.microphone_codecs = []
        if self.microphone_allowed:
            self.microphone_codecs = get_codecs(False, False)
            self.microphone_allowed = len(self.microphone_codecs)>0
        if HAS_SOUND:
            soundlog("speaker_allowed=%s, speaker_codecs=%s", self.speaker_allowed, self.speaker_codecs)
            soundlog("microphone_allowed=%s, microphone_codecs=%s", self.microphone_allowed, self.microphone_codecs)
        else:
            soundlog("sound support is disabled (pygst is missing?)")
        #sound state:
        self.sink_restart_pending = False
        self.on_sink_ready = None
        self.sound_sink = None
        self.server_sound_sequence = False
        self.min_sound_sequence = 0
        self.sound_source = None
        self.server_pulseaudio_id = None
        self.server_pulseaudio_server = None
        self.server_sound_decoders = []
        self.server_sound_encoders = []
        self.server_sound_receive = False
        self.server_sound_send = False

        #mmap:
        self.mmap_enabled = False
        self.mmap = None
        self.mmap_token = None
        self.mmap_filename = None
        self.mmap_size = 0

        #features:
        self.opengl_enabled = False
        self.opengl_props = {}
        self.toggle_cursors_bell_notify = False
        self.toggle_keyboard_sync = False
        self.window_configure = False
        self.window_unmap = False
        self.server_encodings = []
        self.server_encodings_with_speed = ()
        self.server_encodings_with_quality = ()
        self.server_encodings_with_lossless = ()
        self.change_quality = False
        self.change_min_quality = False
        self.change_speed = False
        self.readonly = False
        self.windows_enabled = True
        self.pings = False

        self.client_supports_opengl = False
        self.client_supports_notifications = False
        self.client_supports_system_tray = False
        self.client_supports_clipboard = False
        self.client_supports_cursors = False
        self.client_supports_bell = False
        self.client_supports_sharing = False
        self.notifications_enabled = self.client_supports_notifications
        self.clipboard_enabled = self.client_supports_clipboard
        self.cursors_enabled = self.client_supports_cursors
        self.bell_enabled = self.client_supports_bell

        self.supports_mmap = MMAP_SUPPORTED and ("rgb24" in self.get_core_encodings())

        #helpers and associated flags:
        self.client_extras = None
        self.keyboard_helper = None
        self.clipboard_helper = None
        self.clipboard_enabled = False
        self.menu_helper = None
        self.tray = None
        self.notifier = None
        self.client_supports_notifications = False

        #state:
        self._focused = None

        self.init_packet_handlers()
        self.init_aliases()


    def init(self, opts):
        self.encoding = opts.encoding
        self.title = opts.title
        self.session_name = opts.session_name
        self.auto_refresh_delay = opts.auto_refresh_delay
        self.dpi = int(opts.dpi)

        self.speaker_allowed = bool(opts.speaker) and HAS_SOUND
        self.microphone_allowed = bool(opts.microphone) and HAS_SOUND
        self.speaker_codecs = opts.speaker_codec
        if len(self.speaker_codecs)==0 and self.speaker_allowed:
            self.speaker_codecs = get_codecs(True, False)
            self.speaker_allowed = len(self.speaker_codecs)>0
        self.microphone_codecs = opts.microphone_codec

        self.init_opengl(opts.opengl)
        self.readonly = opts.readonly
        self.windows_enabled = opts.windows
        self.pings = opts.pings

        self.client_supports_notifications = opts.notifications
        self.client_supports_system_tray = opts.system_tray and SYSTEM_TRAY_SUPPORTED
        self.client_supports_clipboard = opts.clipboard
        self.client_supports_cursors = opts.cursors
        self.client_supports_bell = opts.bell
        self.client_supports_sharing = opts.sharing

        self.supports_mmap = MMAP_SUPPORTED and opts.mmap and ("rgb24" in self.get_core_encodings())
        if self.supports_mmap:
            self.init_mmap(opts.mmap_group, self._protocol._conn.filename)

        if not self.readonly:
            self.keyboard_helper = self.make_keyboard_helper(opts.keyboard_sync, opts.key_shortcut)

        tray_icon_filename = opts.tray_icon
        if not opts.no_tray:
            self.menu_helper = self.make_tray_menu_helper()
            self.tray = self.setup_xpra_tray(opts.tray_icon)
            if self.tray:
                tray_icon_filename = self.tray.get_tray_icon_filename(tray_icon_filename)
                if opts.delay_tray:
                    self.tray.hide()
                    self.connect("first-ui-received", self.tray.show)
                else:
                    self.tray.show()

        if self.client_supports_notifications:
            self.notifier = self.make_notifier()
            log("using notifier=%s", self.notifier)
            self.client_supports_notifications = self.notifier is not None

        #audio tagging:
        if tray_icon_filename and os.path.exists(tray_icon_filename):
            try:
                from xpra.sound.pulseaudio_util import add_audio_tagging_env
                add_audio_tagging_env(tray_icon_filename)
            except ImportError, e:
                log("failed to set pulseaudio audio tagging: %s", e)

        if ClientExtras is not None:
            self.client_extras = ClientExtras(self)

        #draw thread:
        self._draw_queue = Queue()
        self._draw_thread = make_daemon_thread(self._draw_thread_loop, "draw")


    def run(self):
        XpraClientBase.run(self)    #start network threads
        self._draw_thread.start()
        self.send_hello()


    def quit(self, exit_code=0):
        raise Exception("override me!")

    def cleanup(self):
        log("UIXpraClient.cleanup()")
        XpraClientBase.cleanup(self)
        for x in (self.keyboard_helper, self.clipboard_helper, self.tray, self.notifier, self.menu_helper):
            if x is None:
                continue
            if not hasattr(x, "cleanup"):
                log.warn("missing a cleanup method on %s: %s", type(x), x)
                continue
            cleanup = getattr(x, "cleanup")
            log("UIXpraClient.cleanup() calling %s.cleanup() : %s", type(x), cleanup)
            try:
                cleanup()
            except:
                log.error("error on %s cleanup", type(x), exc_info=True)
        if self.sound_source:
            self.stop_sending_sound()
        if self.sound_sink:
            self.stop_receiving_sound()
        time.sleep(0.1)
        self.clean_mmap()
        #the protocol has been closed, it is now safe to close all the windows:
        #(cleaner and needed when we run embedded in the client launcher)
        for wid, window in self._id_to_window.items():
            try:
                self.destroy_window(wid, window)
            except:
                pass
        self._id_to_window = {}
        self._window_to_id = {}
        log("UIXpraClient.cleanup() done")

    def get_encodings(self):
        cenc = self.get_core_encodings()
        if "rgb24" in cenc and "rgb" not in cenc:
            cenc.append("rgb")
        return [x for x in PREFERED_ENCODING_ORDER if x in cenc and x not in ("rgb32",)]

    def get_core_encodings(self):
        encodings = ["rgb24"]
        from xpra.scripts.config import has_PIL, has_dec_vpx, has_dec_avcodec, has_dec_webp, has_csc_swscale
        encs = (
              (has_dec_vpx & has_csc_swscale        , ["vpx"]),
              (has_dec_avcodec & has_csc_swscale    , ["x264"]),
              (has_dec_webp                         , ["webp"]),
              (has_PIL                              , ["png", "png/L", "png/P", "jpeg"]),
               )
        log("get_core_encodings() encs=%s", encs)
        for test, formats in encs:
            if test:
                for enc in formats:
                    if enc not in encodings:
                        encodings.append(enc)
        log("get_core_encodings()=%s", encodings)
        return encodings


    def get_supported_window_layouts(self):
        return  []

    def make_keyboard_helper(self, keyboard_sync, key_shortcuts):
        return KeyboardHelper(self.send, keyboard_sync, key_shortcuts)

    def make_clipboard_helper(self):
        raise Exception("override me!")


    def make_notifier(self):
        return self.make_instance(self.get_notifier_classes())

    def get_notifier_classes(self):
        #subclasses will generally add their toolkit specific variants
        #by overriding this method
        #use the native ones first:
        return get_native_notifier_classes()


    def make_system_tray(self, *args):
        """ tray used for application systray forwarding """
        return self.make_instance(self.get_system_tray_classes(), *args)

    def get_system_tray_classes(self):
        #subclasses may add their toolkit specific variants, if any
        #by overriding this method
        #use the native ones first:
        return get_native_system_tray_classes()


    def make_tray(self, *args):
        """ tray used by our own application """
        return self.make_instance(self.get_tray_classes(), *args)

    def get_tray_classes(self):
        #subclasses may add their toolkit specific variants, if any
        #by overriding this method
        #use the native ones first:
        return get_native_tray_classes()


    def make_tray_menu_helper(self):
        """ menu helper class used by our tray (make_tray / setup_xpra_tray) """
        return self.make_instance(self.get_tray_menu_helper_classes(), self)

    def get_tray_menu_helper_classes(self):
        #subclasses may add their toolkit specific variants, if any
        #by overriding this method
        #use the native ones first:
        return get_native_tray_menu_helper_classes()


    def make_instance(self, class_options, *args):
        log("make_instance%s", [class_options]+list(args))
        for c in class_options:
            try:
                v = c(*args)
                log("make_instance(..) %s()=%s", c, v)
                if v:
                    return v
            except:
                log.error("make_instance%s failed to instantiate %s", class_options+list(args), c, exc_info=True)
        return None


    def setup_xpra_tray(self, tray_icon_filename):
        def xpra_tray_click(button, pressed, time=0):
            log("xpra_tray_click(%s, %s)", button, pressed)
            if button==1 and pressed:
                self.menu_helper.activate()
            elif button==3 and not pressed:
                self.menu_helper.popup(button, time)
        def xpra_tray_mouseover(*args):
            log("xpra_tray_mouseover(%s)", args)
        def xpra_tray_exit(*args):
            log("xpra_tray_exit(%s)", args)
            self.quit(0)
        menu = None
        if self.menu_helper:
            menu = self.menu_helper.build()
        return self.make_tray(menu, "Xpra", tray_icon_filename, None, xpra_tray_click, xpra_tray_mouseover, xpra_tray_exit)

    def setup_system_tray(self, client, wid, w, h):
        def tray_resized(*args):
            log("tray_resized(%s)", args)
            tray = self._id_to_window.get(wid)
            if tray:
                tray.reconfigure()
        def tray_click(button, pressed, time=0):
            log("tray_click(%s, %s, %s)", button, pressed, time)
            tray = self._id_to_window.get(wid)
            if tray:
                x, y = self.get_mouse_position()
                #special case for crapple where we don't have
                #the real location of the tray, so we may have to
                #move where the tray is mapped to ensure the click
                #does hit it... what a lot of ****
                tx, ty, tw, th = tray.get_geometry()
                if tray.get_tray_geometry() is None:
                    #ok so, we don't have the real location...
                    #is the click within the current bounds:
                    if x<tx or x>(tx+tw) or y<ty or y>(ty+th):
                        #no, so we have to move the tray first
                        #we'll assume the click was in the middle
                        tww, twh = tray.get_tray_size() or [24, 24]
                        tx = max(0, int(x - tww/2))
                        ty = max(0, int(y - twh/2))
                        log("moving tray to: %sx%s", tx, ty)
                        tray.move_resize(tx, ty, tww, twh)
                modifiers = self.get_current_modifiers()
                self.send_positional(["button-action", wid,
                                              button, pressed, (x, y), modifiers])
                tray.reconfigure()
        def tray_mouseover(*args):
            log("tray_mouseover(%s)", args)
        def tray_exit(*args):
            log("tray_exit(%s)", args)
        #(menu, tooltip, icon_filename, size_changed_cb, click_cb, mouseover_cb, exit_cb)
        tray_widget = self.make_system_tray(None, "Xpra", None, tray_resized, tray_click, tray_mouseover, tray_exit)
        assert tray_widget, "could not instantiate a system tray for tray id %s" % wid
        tray_widget.show()
        return ClientTray(client, wid, w, h, tray_widget)


    def get_screen_sizes(self):
        raise Exception("override me!")

    def get_root_size(self):
        raise Exception("override me!")

    def set_windows_cursor(self, client_windows, new_cursor):
        raise Exception("override me!")

    def get_mouse_position(self):
        raise Exception("override me!")

    def get_current_modifiers(self):
        raise Exception("override me!")

    def window_bell(self, window, device, percent, pitch, duration, bell_class, bell_id, bell_name):
        raise Exception("override me!")


    def init_mmap(self, mmap_group, socket_filename):
        log("init_mmap(%s, %s)", mmap_group, socket_filename)
        from xpra.os_util import get_int_uuid
        from xpra.net.mmap_pipe import init_client_mmap
        self.mmap_token = get_int_uuid()
        self.mmap_enabled, self.mmap, self.mmap_size, self.mmap_tempfile, self.mmap_filename = \
            init_client_mmap(self.mmap_token, mmap_group, socket_filename)

    def clean_mmap(self):
        log("XpraClient.clean_mmap() mmap_filename=%s", self.mmap_filename)
        if self.mmap_filename and os.path.exists(self.mmap_filename):
            os.unlink(self.mmap_filename)
            self.mmap_filename = None


    def init_opengl(self, enable_opengl):
        self.opengl_enabled = False
        self.client_supports_opengl = False
        self.opengl_props = {"info" : "not supported"}


    def send_layout(self):
        self.send("layout-changed", nn(self.keyboard_helper.xkbmap_layout), nn(self.keyboard_helper.xkbmap_variant))

    def send_keymap(self):
        self.send("keymap-changed", self.get_keymap_properties())

    def get_keymap_properties(self):
        props = self.keyboard_helper.get_keymap_properties()
        props["modifiers"] = self.get_current_modifiers()
        return  props

    def handle_key_action(self, window, key_event):
        if self.readonly or self.keyboard_helper is None:
            return
        wid = self._window_to_id[window]
        log("handle_key_action(%s, %s) wid=%s", window, key_event, wid)
        self.keyboard_helper.handle_key_action(window, wid, key_event)

    def mask_to_names(self, mask):
        if self.keyboard_helper is None:
            return []
        return self.keyboard_helper.mask_to_names(mask)


    def set_default_window_icon(self, window_icon):
        if not window_icon:
            window_icon = self.get_icon_filename("xpra.png")
        if window_icon and os.path.exists(window_icon):
            try:
                self.do_set_window_icon(window_icon)
            except Exception, e:
                log.error("failed to set window icon %s: %s", window_icon, e)


    def send_focus(self, wid):
        log("send_focus(%s)", wid)
        self.send("focus", wid, self.get_current_modifiers())

    def update_focus(self, wid, gotit):
        log("update_focus(%s, %s) _focused=%s", wid, gotit, self._focused)
        if gotit and self._focused is not wid:
            if self.keyboard_helper:
                self.keyboard_helper.clear_repeat()
            self.send_focus(wid)
            self._focused = wid
        if not gotit:
            if self._focused!=wid:
                #if this window lost focus, it must have had it!
                #(catch up - makes things like OR windows work:
                # their parent receives the focus-out event)
                self.send_focus(wid)
            if self.keyboard_helper:
                self.keyboard_helper.clear_repeat()
            self.send_focus(0)
            self._focused = None


    def make_hello(self, challenge_response=None):
        capabilities = XpraClientBase.make_hello(self, challenge_response)
        if self.readonly:
            #don't bother sending keyboard info, as it won't be used
            capabilities["keyboard"] = False
        else:
            for k,v in self.get_keymap_properties().items():
                capabilities[k] = v
            capabilities["xkbmap_layout"] = nn(self.keyboard_helper.xkbmap_layout)
            capabilities["xkbmap_variant"] = nn(self.keyboard_helper.xkbmap_variant)
        capabilities["modifiers"] = self.get_current_modifiers()
        root_w, root_h = self.get_root_size()
        capabilities["desktop_size"] = [root_w, root_h]
        capabilities["screen_sizes"] = self.get_screen_sizes()
        if self.keyboard_helper:
            key_repeat = self.keyboard_helper.keyboard.get_keyboard_repeat()
            if key_repeat:
                delay_ms,interval_ms = key_repeat
                capabilities["key_repeat"] = (delay_ms,interval_ms)
            else:
                #cannot do keyboard_sync without a key repeat value!
                #(maybe we could just choose one?)
                self.keyboard_helper.keyboard_sync = False
            capabilities["keyboard_sync"] = self.keyboard_helper.keyboard_sync
            log("keyboard capabilities: %s", [(k,v) for k,v in capabilities.items() if k.startswith("key")])
        if self.mmap_enabled:
            capabilities["mmap_file"] = self.mmap_filename
            capabilities["mmap_token"] = self.mmap_token
        #don't try to find the server uuid if this platform cannot run servers..
        #(doing so causes lockups on win32 and startup errors on osx)
        if MMAP_SUPPORTED:
            #we may be running inside another server!
            try:
                from xpra.server.server_uuid import get_uuid
                capabilities["server_uuid"] = get_uuid() or ""
            except:
                pass
        capabilities["randr_notify"] = True
        capabilities["compressible_cursors"] = True
        capabilities["dpi"] = self.dpi
        capabilities["clipboard"] = self.client_supports_clipboard
        capabilities["clipboard.notifications"] = self.client_supports_clipboard
        #buggy osx clipboards:
        capabilities["clipboard.want_targets"] = CLIPBOARD_WANT_TARGETS
        #buggy osx and win32 clipboards:
        capabilities["clipboard.greedy"] = CLIPBOARD_GREEDY
        capabilities["notifications"] = self.client_supports_notifications
        capabilities["cursors"] = self.client_supports_cursors
        capabilities["bell"] = self.client_supports_bell
        for k,v in codec_versions.items():
            capabilities["encoding.%s.version" % k] = v
        capabilities["encoding.client_options"] = True
        capabilities["encoding_client_options"] = True
        capabilities["encoding.csc_atoms"] = True
        #TODO: check for csc support (swscale only?)
        capabilities["encoding.video_scaling"] = True
        capabilities["encoding.transparency"] = self.has_transparency()
        #TODO: check for csc support (swscale only?)
        capabilities["encoding.csc_modes"] = ("YUV420P", "YUV422P", "YUV444P", "BGRA", "BGRX")
        capabilities["rgb24zlib"] = True
        capabilities["encoding.rgb24zlib"] = True
        capabilities["named_cursors"] = False
        capabilities["share"] = self.client_supports_sharing
        capabilities["auto_refresh_delay"] = int(self.auto_refresh_delay*1000)
        capabilities["windows"] = self.windows_enabled
        capabilities["raw_window_icons"] = True
        capabilities["system_tray"] = self.client_supports_system_tray
        capabilities["xsettings-tuple"] = True
        capabilities["generic_window_types"] = True
        capabilities["server-window-resize"] = True
        capabilities["notify-startup-complete"] = True
        capabilities["generic-rgb-encodings"] = True
        if self.encoding:
            capabilities["encoding"] = self.encoding
        capabilities["encodings"] = self.get_encodings()
        capabilities["encodings.core"] = self.get_core_encodings()
        capabilities["encodings.rgb_formats"] = ["RGB", "RGBA"]
        if self.quality>0:
            capabilities["jpeg"] = self.quality
            capabilities["quality"] = self.quality
            capabilities["encoding.quality"] = self.quality
        if self.min_quality>0:
            capabilities["encoding.min-quality"] = self.min_quality
        if self.speed>=0:
            capabilities["speed"] = self.speed
            capabilities["encoding.speed"] = self.speed
        if self.min_speed>=0:
            capabilities["encoding.min-speed"] = self.min_speed
        log("encoding capabilities: %s", [(k,v) for k,v in capabilities.items() if k.startswith("encoding")])
        capabilities["encoding.uses_swscale"] = True
        if "x264" in self.get_encodings():
            # some profile options: "baseline", "main", "high", "high10", ...
            # set the default to "high10" for I420/YUV420P
            # as the python client always supports all the profiles
            # whereas on the server side, the default is baseline to accomodate less capable clients.
            # I422/YUV422P requires high422, and 
            # I444/YUV444P requires high444,
            # so we don't bother specifying anything for those two.
            for old_csc_name, csc_name, default_profile in (
                        ("I420", "YUV420P", "high10"),
                        ("I422", "YUV422P", ""),
                        ("I444", "YUV444P", "")):
                profile = os.environ.get("XPRA_X264_%s_PROFILE" % old_csc_name, default_profile)
                profile = os.environ.get("XPRA_X264_%s_PROFILE" % csc_name, profile)
                if profile:
                    #send as both old and new names:
                    capabilities["encoding.x264.%s.profile" % old_csc_name] = profile
                    capabilities["encoding.x264.%s.profile" % csc_name] = profile
            log("x264 encoding options: %s", str([(k,v) for k,v in capabilities.items() if k.startswith("encoding.x264.")]))
        iq = max(self.min_quality, self.quality)
        if iq<0:
            iq = 70
        capabilities["encoding.initial_quality"] = iq
        if HAS_SOUND:
            try:
                from xpra.sound.pulseaudio_util import add_pulseaudio_capabilities
                add_pulseaudio_capabilities(capabilities)
                from xpra.sound.gstreamer_util import add_gst_capabilities
                add_gst_capabilities(capabilities, receive=self.speaker_allowed, send=self.microphone_allowed,
                                     receive_codecs=self.speaker_codecs, send_codecs=self.microphone_codecs, new_namespace=True)
                soundlog("sound capabilities: %s", [(k,v) for k,v in capabilities.items() if k.startswith("sound.")])
            except Exception, e:
                log.error("failed to setup sound: %s", e, exc_info=True)
                self.speaker_allowed = False
                self.microphone_allowed = False
        #batch options:
        for bprop in ("always", "min_delay", "max_delay", "delay", "max_events", "max_pixels", "time_unit"):
            evalue = os.environ.get("XPRA_BATCH_%s" % bprop.upper())
            if evalue:
                try:
                    capabilities["batch.%s" % bprop] = int(evalue)
                except:
                    log.error("invalid environment value for %s: %s", bprop, evalue)
        log("batch props=%s", [("%s=%s" % (k,v)) for k,v in capabilities.items() if k.startswith("batch.")])
        return capabilities

    def has_transparency(self):
        return False


    def server_ok(self):
        return self._server_ok

    def check_server_echo(self, ping_sent_time):
        last = self._server_ok
        self._server_ok = not FAKE_BROKEN_CONNECTION and self.last_ping_echoed_time>=ping_sent_time
        if last!=self._server_ok and not self._server_ok:
            log.info("server is not responding, drawing spinners over the windows")
            def timer_redraw():
                if self._protocol is None:
                    #no longer connected!
                    return False
                self.redraw_spinners()
                if self.server_ok():
                    log.info("server is OK again")
                    return False
                return True
            self.redraw_spinners()
            self.timeout_add(100, timer_redraw)
        return False

    def redraw_spinners(self):
        #draws spinner on top of the window, or not (plain repaint)
        #depending on whether the server is ok or not
        for w in self._id_to_window.values():
            if not w.is_tray():
                w.spinner(self.server_ok())

    def check_echo_timeout(self, ping_time):
        log("check_echo_timeout(%s) last_ping_echoed_time=%s", ping_time, self.last_ping_echoed_time)
        if self.last_ping_echoed_time<ping_time:
            self.warn_and_quit(EXIT_TIMEOUT, "server ping timeout - waited %s seconds without a response" % PING_TIMEOUT)

    def send_ping(self):
        now_ms = int(1000.0*time.time())
        self.send("ping", now_ms)
        self.timeout_add(PING_TIMEOUT*1000, self.check_echo_timeout, now_ms)
        wait = 2.0
        if len(self.server_ping_latency)>0:
            l = [x for _,x in list(self.server_ping_latency)]
            avg = sum(l) / len(l)
            wait = 1.0+avg*2.0
            log("average server latency=%.1f, using max wait %.2fs", 1000.0*avg, wait)
        self.timeout_add(int(1000.0*wait), self.check_server_echo, now_ms)
        return True

    def _process_ping_echo(self, packet):
        echoedtime, l1, l2, l3, cl = packet[1:6]
        self.last_ping_echoed_time = echoedtime
        self.check_server_echo(0)
        server_ping_latency = time.time()-echoedtime/1000.0
        self.server_ping_latency.append((time.time(), server_ping_latency))
        self.server_load = l1, l2, l3
        if cl>=0:
            self.client_ping_latency.append((time.time(), cl/1000.0))
        log("ping echo server load=%s, measured client latency=%sms", self.server_load, cl)

    def _process_ping(self, packet):
        echotime = packet[1]
        l1,l2,l3 = 0,0,0
        if os.name=="posix":
            try:
                (fl1, fl2, fl3) = os.getloadavg()
                l1,l2,l3 = int(fl1*1000), int(fl2*1000), int(fl3*1000)
            except (OSError, AttributeError):
                pass
        sl = -1
        if len(self.server_ping_latency)>0:
            _, sl = self.server_ping_latency[-1]
        self.send("ping_echo", echotime, l1, l2, l3, int(1000.0*sl))


    def _process_info_response(self, packet):
        self.info_request_pending = False
        self.server_last_info = packet[1]
        log("info-response: %s", packet)

    def send_info_request(self):
        assert self.server_info_request
        if not self.info_request_pending:
            self.info_request_pending = True
            self.send("info-request", [self.uuid], self._id_to_window.keys())


    def send_quality(self):
        q = self.quality
        assert q==-1 or (q>=0 and q<=100), "invalid quality: %s" % q
        if self.change_quality:
            self.send("quality", q)

    def send_min_quality(self):
        q = self.min_quality
        assert q==-1 or (q>=0 and q<=100), "invalid quality: %s" % q
        if self.change_min_quality:
            #v0.8 onwards: set min
            self.send("min-quality", q)

    def send_speed(self):
        assert self.change_speed
        s = self.speed
        assert s==-1 or (s>=0 and s<=100), "invalid speed: %s" % s
        self.send("speed", s)

    def send_min_speed(self):
        assert self.change_speed
        s = self.min_speed
        assert s==-1 or (s>=0 and s<=100), "invalid speed: %s" % s
        self.send("min-speed", s)


    def send_refresh(self, wid):
        self.send("buffer-refresh", wid, True, 95)

    def send_refresh_all(self):
        log.debug("Automatic refresh for all windows ")
        self.send_refresh(-1)


    def parse_server_capabilities(self, capabilities):
        if not XpraClientBase.parse_server_capabilities(self, capabilities):
            return
        def get(key, default=None):
            return self.capsget(capabilities, key, default)
        if not self.session_name:
            self.session_name = get("session_name", "Xpra")
        set_application_name(self.session_name)
        self.window_configure = get("window_configure", False)
        self.window_unmap = get("window_unmap", False)
        self.suspend_resume = get("suspend-resume", False)
        self.server_supports_notifications = get("notifications", False)
        self.notifications_enabled = self.server_supports_notifications and self.client_supports_notifications
        self.server_supports_cursors = get("cursors", True)    #added in 0.5, default to True!
        self.cursors_enabled = self.server_supports_cursors and self.client_supports_cursors
        self.server_supports_bell = get("bell", True)          #added in 0.5, default to True!
        self.bell_enabled = self.server_supports_bell and self.client_supports_bell
        self.server_supports_clipboard = get("clipboard", False)
        self.server_clipboards = get("clipboards", ALL_CLIPBOARDS)
        self.clipboard_enabled = self.client_supports_clipboard and self.server_supports_clipboard
        self.mmap_enabled = self.supports_mmap and self.mmap_enabled and get("mmap_enabled")
        if self.mmap_enabled:
            mmap_token = get("mmap_token")
            if mmap_token:
                from xpra.net.mmap_pipe import read_mmap_token
                token = read_mmap_token(self.mmap)
                if token!=mmap_token:
                    log.warn("mmap token verification failed!")
                    self.mmap_enabled = False
                    self.quit(EXIT_MMAP_TOKEN_FAILURE)
                    return
        self.server_auto_refresh_delay = get("auto_refresh_delay", 0)/1000.0
        self.server_encodings = get("encodings", [])
        self.server_encodings_with_speed = get("encodings.with_speed", ("x264",)) #old servers only supported x264
        self.server_encodings_with_quality = get("encodings.with_quality", ("jpeg", "webp", "x264"))
        self.server_encodings_with_lossless_mode = get("encodings.with_lossless_mode", ())
        self.change_quality = get("change-quality", False)
        self.change_min_quality = get("change-min-quality", False)
        self.change_speed = get("change-speed", False)
        self.change_min_speed = get("change-min-speed", False)
        self.xsettings_tuple = get("xsettings-tuple", False)
        if self.mmap_enabled:
            log.info("mmap is enabled using %sB area in %s", std_unit(self.mmap_size, unit=1024), self.mmap_filename)
        #the server will have a handle on the mmap file by now, safe to delete:
        self.clean_mmap()
        self.server_start_time = get("start_time", -1)
        self.server_platform = get("platform")
        self.toggle_cursors_bell_notify = get("toggle_cursors_bell_notify", False)
        self.toggle_keyboard_sync = get("toggle_keyboard_sync", False)
        self.server_max_desktop_size = get("max_desktop_size")
        self.server_display = get("display")
        self.server_actual_desktop_size = get("actual_desktop_size")
        log("server actual desktop size=%s", self.server_actual_desktop_size)
        self.server_randr = get("resize_screen", False)
        log.debug("server has randr: %s", self.server_randr)
        self.server_sound_sequence = get("sound_sequence", False)
        self.server_info_request = get("info-request", False)
        e = get("encoding")
        if e and e!=self.encoding:
            log.debug("server is using %s encoding" % e)
            self.encoding = e
        #process the rest from the UI thread:
        self.idle_add(self.process_ui_capabilities, capabilities)

    def process_ui_capabilities(self, capabilities):
        def get(key, default=None):
            return self.capsget(capabilities, key, default)
        #figure out the maximum actual desktop size and use it to
        #calculate the maximum size of a packet (a full screen update packet)
        if self.clipboard_enabled:
            self.clipboard_helper = self.make_clipboard_helper()
            self.clipboard_enabled = self.clipboard_helper is not None
        self.set_max_packet_size()
        self.send_deflate_level()
        server_desktop_size = get("desktop_size")
        log("server desktop size=%s", server_desktop_size)
        if not get("shadow", False):
            assert server_desktop_size
            avail_w, avail_h = server_desktop_size
            root_w, root_h = self.get_root_size()
            if avail_w<root_w or avail_h<root_h:
                log.warn("Server's virtual screen is too small -- "
                         "(server: %sx%s vs. client: %sx%s)\n"
                         "You may see strange behavior.\n"
                         "Please see "
                         "https://www.xpra.org/trac/ticket/10"
                         % (avail_w, avail_h, root_w, root_h))
        modifier_keycodes = get("modifier_keycodes")
        if modifier_keycodes:
            self.keyboard_helper.set_modifier_mappings(modifier_keycodes)

        #sound:
        self.server_pulseaudio_id = get("sound.pulseaudio.id")
        self.server_pulseaudio_server = get("sound.pulseaudio.server")
        self.server_sound_decoders = get("sound.decoders", [])
        self.server_sound_encoders = get("sound.encoders", [])
        self.server_sound_receive = get("sound.receive", False)
        self.server_sound_send = get("sound.send", False)
        soundlog("pulseaudio id=%s, server=%s, sound decoders=%s, sound encoders=%s, receive=%s, send=%s",
                 self.server_pulseaudio_id, self.server_pulseaudio_server, self.server_sound_decoders,
                 self.server_sound_encoders, self.server_sound_receive, self.server_sound_send)
        if self.server_sound_send and self.speaker_allowed:
            self.start_receiving_sound()
        #dont' send sound automatically, wait for user to request it:
        #if self.server_sound_receive and self.microphone_allowed:
        #    self.start_sending_sound()

        self.key_repeat_delay, self.key_repeat_interval = get("key_repeat", (-1,-1))
        self.emit("handshake-complete")
        #ui may want to know this is now set:
        self.emit("clipboard-toggled")
        if self.server_supports_clipboard:
            #from now on, we will send a message to the server whenever the clipboard flag changes:
            self.connect("clipboard-toggled", self.send_clipboard_enabled_status)
        if self.toggle_keyboard_sync:
            self.connect("keyboard-sync-toggled", self.send_keyboard_sync_enabled_status)
        self.send_ping()
        if not get("notify-startup-complete", False):
            #we won't get notified, so assume it is now:
            self._startup_complete()

    def _startup_complete(self, *args):
        log("all the existing windows and system trays have been received: %s items", len(self._id_to_window))
        gui_ready()
        if self.tray:
            self.tray.ready()


    def start_sending_sound(self):
        """ (re)start a sound source and emit client signal """
        soundlog("start_sending_sound()")
        assert self.microphone_allowed
        assert self.server_sound_receive
        if self.sound_source:
            if self.sound_source.get_state()=="active":
                log.error("already sending sound!")
                return
            self.sound_source.start()
        if not self.start_sound_source():
            return
        self.microphone_enabled = True
        self.emit("microphone-changed")
        soundlog("start_sending_sound() done")

    def start_sound_source(self):
        soundlog("start_sound_source()")
        assert self.sound_source is None
        def sound_source_state_changed(*args):
            self.emit("microphone-changed")
        def sound_source_bitrate_changed(*args):
            self.emit("microphone-changed")
        try:
            from xpra.sound.gstreamer_util import start_sending_sound
            self.sound_source = start_sending_sound(None, self.server_sound_decoders, self.microphone_codecs, self.server_pulseaudio_server, self.server_pulseaudio_id)
            if not self.sound_source:
                return False
            self.sound_source.connect("new-buffer", self.new_sound_buffer)
            self.sound_source.connect("state-changed", sound_source_state_changed)
            self.sound_source.connect("bitrate-changed", sound_source_bitrate_changed)
            self.sound_source.start()
            soundlog("start_sound_source() sound source %s started", self.sound_source)
            return True
        except Exception, e:
            log.error("error setting up sound: %s", e)
            return False

    def stop_sending_sound(self):
        """ stop the sound source and emit client signal """
        soundlog("stop_sending_sound() sound source=%s", self.sound_source)
        ss = self.sound_source
        self.microphone_enabled = False
        self.sound_source = None
        def stop_sending_sound_thread():
            soundlog("UIXpraClient.stop_sending_sound_thread()")
            if ss is None:
                log.warn("stop_sending_sound: sound not started!")
                return
            ss.cleanup()
            self.emit("microphone-changed")
            soundlog("UIXpraClient.stop_sending_sound_thread() done")
        thread.start_new_thread(stop_sending_sound_thread, ())

    def start_receiving_sound(self):
        """ ask the server to start sending sound and emit the client signal """
        soundlog("start_receiving_sound() sound sink=%s", self.sound_sink)
        if self.sound_sink is not None:
            soundlog("start_receiving_sound: we already have a sound sink")
            return
        elif not self.server_sound_send:
            log.error("cannot start receiving sound: support not enabled on the server")
            return
        #choose a codec:
        from xpra.sound.gstreamer_util import CODEC_ORDER
        matching_codecs = [x for x in self.server_sound_encoders if x in self.speaker_codecs]
        ordered_codecs = [x for x in CODEC_ORDER if x in matching_codecs]
        if len(ordered_codecs)==0:
            log.error("no matching codecs between server (%s) and client (%s)", self.server_sound_encoders, self.speaker_codecs)
            return
        codec = ordered_codecs[0]
        self.speaker_enabled = True
        self.emit("speaker-changed")
        def sink_ready(*args):
            soundlog("sink_ready(%s) codec=%s", args, codec)
            self.send("sound-control", "start", codec)
            return False
        self.on_sink_ready = sink_ready
        self.start_sound_sink(codec)

    def stop_receiving_sound(self):
        """ ask the server to stop sending sound, toggle flag so we ignore further packets and emit client signal """
        soundlog("stop_receiving_sound() sound sink=%s", self.sound_sink)
        ss = self.sound_sink
        self.speaker_enabled = False
        if ss is None:
            return
        self.sound_sink = None
        self.send("sound-control", "stop")
        def stop_receiving_sound_thread():
            soundlog("UIXpraClient.stop_receiving_sound_thread()")
            if ss is None:
                log("stop_receiving_sound: sound not started!")
                return
            ss.cleanup()
            self.emit("speaker-changed")
            soundlog("UIXpraClient.stop_receiving_sound_thread() done")
        thread.start_new_thread(stop_receiving_sound_thread, ())

    def bump_sound_sequence(self):
        if self.server_sound_sequence:
            #server supports the "sound-sequence" feature
            #tell it to use a new one:
            self.min_sound_sequence += 1
            soundlog("bump_sound_sequence() sequence is now %s", self.min_sound_sequence)
            #via idle add so this will wait for UI thread to catch up if needed:
            self.idle_add(self.send_new_sound_sequence)

    def send_new_sound_sequence(self):
        soundlog("send_new_sound_sequence() sequence=%s", self.min_sound_sequence)
        self.send("sound-control", "new-sequence", self.min_sound_sequence)


    def sound_sink_state_changed(self, sound_sink, state):
        soundlog("sound_sink_state_changed(%s, %s) on_sink_ready=%s", sound_sink, state, self.on_sink_ready)
        if state=="ready" and self.on_sink_ready:
            if not self.on_sink_ready():
                self.on_sink_ready = None
        self.emit("speaker-changed")
    def sound_sink_bitrate_changed(self, sound_sink, bitrate):
        soundlog("sound_sink_bitrate_changed(%s, %s)", sound_sink, bitrate)
        self.emit("speaker-changed")
    def sound_sink_error(self, sound_sink, error):
        log.warn("stopping speaker because of error: %s", error)
        self.stop_receiving_sound()

    def sound_sink_overrun(self, *args):
        if self.sink_restart_pending:
            soundlog("overrun re-start is already pending")
            return
        log.warn("re-starting speaker because of overrun")
        codec = self.sound_sink.codec
        self.sink_restart_pending = True
        if self.server_sound_sequence:
            self.min_sound_sequence += 1
        #Note: the next sound packet will take care of starting a new pipeline
        self.stop_receiving_sound()
        def restart():
            soundlog("restart() sound_sink=%s, codec=%s, server_sound_sequence=%s", self.sound_sink, codec, self.server_sound_sequence)
            if self.server_sound_sequence:
                self.send_new_sound_sequence()
            self.start_receiving_sound()
            self.sink_restart_pending = False
            return False
        self.timeout_add(200, restart)

    def start_sound_sink(self, codec):
        soundlog("start_sound_sink(%s)", codec)
        assert self.sound_sink is None
        try:
            soundlog("starting %s sound sink", codec)
            from xpra.sound.sink import SoundSink
            self.sound_sink = SoundSink(codec=codec)
            self.sound_sink.connect("state-changed", self.sound_sink_state_changed)
            self.sound_sink.connect("bitrate-changed", self.sound_sink_bitrate_changed)
            self.sound_sink.connect("error", self.sound_sink_error)
            self.sound_sink.connect("overrun", self.sound_sink_overrun)
            self.sound_sink.start()
            soundlog("%s sound sink started", codec)
            return True
        except:
            log.error("failed to start sound sink", exc_info=True)
            return False

    def new_sound_buffer(self, sound_source, data, metadata):
        soundlog("new_sound_buffer(%s, %s, %s) sound source=%s", sound_source, len(data or []), metadata, self.sound_source)
        if self.sound_source:
            self.send("sound-data", self.sound_source.codec, Compressed(self.sound_source.codec, data), metadata)

    def _process_sound_data(self, packet):
        if not self.speaker_enabled:
            soundlog("speaker is now disabled - dropping packet")
            return
        codec, data, metadata = packet[1:4]
        seq = metadata.get("sequence", -1)
        if self.min_sound_sequence>0 and seq<self.min_sound_sequence:
            soundlog("ignoring sound data with old sequence number %s", seq)
            return
        if self.sound_sink is not None and codec!=self.sound_sink.codec:
            log.error("sound codec change not supported! (from %s to %s)", self.sound_sink.codec, codec)
            self.sound_sink.stop()
            return
        if self.sound_sink is None:
            soundlog("no sound sink to process sound data, dropping it")
            return
        elif self.sound_sink.get_state()=="stopped":
            soundlog("sound data received, sound sink is stopped - starting it")
            self.sound_sink.start()
        self.sound_sink.add_data(data, metadata)


    def send_notify_enabled(self):
        assert self.client_supports_notifications, "cannot toggle notifications: the feature is disabled by the client"
        assert self.server_supports_notifications, "cannot toggle notifications: the feature is disabled by the server"
        assert self.toggle_cursors_bell_notify, "cannot toggle notifications: server lacks the feature"
        self.send("set-notify", self.notifications_enabled)

    def send_bell_enabled(self):
        assert self.client_supports_bell, "cannot toggle bell: the feature is disabled by the client"
        assert self.server_supports_bell, "cannot toggle bell: the feature is disabled by the server"
        assert self.toggle_cursors_bell_notify, "cannot toggle bell: server lacks the feature"
        self.send("set-bell", self.bell_enabled)

    def send_cursors_enabled(self):
        assert self.client_supports_cursors, "cannot toggle cursors: the feature is disabled by the client"
        assert self.server_supports_cursors, "cannot toggle cursors: the feature is disabled by the server"
        assert self.toggle_cursors_bell_notify, "cannot toggle cursors: server lacks the feature"
        self.send("set-cursors", self.cursors_enabled)


    def set_deflate_level(self, level):
        self.compression_level = level
        self.send_deflate_level()

    def send_deflate_level(self):
        self._protocol.set_compression_level(self.compression_level)
        self.send("set_deflate", self.compression_level)


    def send_clipboard_enabled_status(self, *args):
        self.send("set-clipboard-enabled", self.clipboard_enabled)

    def send_keyboard_sync_enabled_status(self, *args):
        self.send("set-keyboard-sync-enabled", self.keyboard_sync)


    def set_encoding(self, encoding):
        log("set_encoding(%s)", encoding)
        assert encoding in self.get_encodings(), "encoding %s is not supported!" % encoding
        assert encoding in self.server_encodings, "encoding %s is not supported by the server! (only: %s)" % (encoding, self.server_encodings)
        self.encoding = encoding
        self.send("encoding", encoding)


    def reset_cursor(self):
        self.set_windows_cursor(self._id_to_window.values(), [])

    def _ui_event(self):
        if self._ui_events==0:
            self.emit("first-ui-received")
        self._ui_events += 1

    def _process_new_common(self, packet, override_redirect):
        self._ui_event()
        wid, x, y, w, h, metadata = packet[1:7]
        assert wid not in self._id_to_window, "we already have a window %s" % wid
        if w<=0 or h<=0:
            log.error("window dimensions are wrong: %sx%s", w, h)
            w, h = 1, 1
        client_properties = {}
        if len(packet)>=8:
            client_properties = packet[7]
        if self.server_auto_refresh_delay>0:
            auto_refresh_delay = 0                          #server takes care of it
        else:
            auto_refresh_delay = self.auto_refresh_delay    #we do it
        self.make_new_window(wid, x, y, w, h, metadata, override_redirect, client_properties, auto_refresh_delay)

    def make_new_window(self, wid, x, y, w, h, metadata, override_redirect, client_properties, auto_refresh_delay):
        ClientWindowClass = self.get_client_window_class(metadata, override_redirect)
        group_leader_window = self.get_group_leader(metadata, override_redirect)
        window = ClientWindowClass(self, group_leader_window, wid, x, y, w, h, metadata, override_redirect, client_properties, auto_refresh_delay)
        self._id_to_window[wid] = window
        self._window_to_id[window] = wid
        window.show()
        return window

    def get_group_leader(self, metadata, override_redirect):
        #subclasses that wish to implement the feature may override this method
        return None


    def get_client_window_class(self, metadata, override_redirect):
        return self.ClientWindowClass

    def _process_new_window(self, packet):
        self._process_new_common(packet, False)

    def _process_new_override_redirect(self, packet):
        self._process_new_common(packet, True)

    def _process_new_tray(self, packet):
        assert SYSTEM_TRAY_SUPPORTED
        self._ui_event()
        wid, w, h = packet[1:4]
        assert wid not in self._id_to_window, "we already have a window %s" % wid
        tray = self.setup_system_tray(self, wid, w, h)
        log("process_new_tray(%s) tray=%s", packet, tray)
        self._id_to_window[wid] = tray
        self._window_to_id[tray] = wid

    def _process_window_resized(self, packet):
        (wid, w, h) = packet[1:4]
        window = self._id_to_window.get(wid)
        log("_process_window_resized resizing window %s (id=%s) to %s", window, wid, (w,h))
        if window:
            window.resize(w, h)

    def _process_draw(self, packet):
        self._draw_queue.put(packet)

    def send_damage_sequence(self, wid, packet_sequence, width, height, decode_time):
        self.send_now("damage-sequence", packet_sequence, wid, width, height, decode_time)

    def _draw_thread_loop(self):
        while self.exit_code is None:
            packet = self._draw_queue.get()
            try:
                self._do_draw(packet)
                time.sleep(0)
            except KeyboardInterrupt:
                raise
            except:
                log.error("error processing draw packet", exc_info=True)

    def _do_draw(self, packet):
        """ this runs from the draw thread above """
        wid, x, y, width, height, coding, data, packet_sequence, rowstride = packet[1:10]
        window = self._id_to_window.get(wid)
        if not window:
            #window is gone
            def draw_cleanup():
                if coding=="mmap":
                    assert self.mmap_enabled
                    def free_mmap_area():
                        #we need to ack the data to free the space!
                        data_start = ctypes.c_uint.from_buffer(self.mmap, 0)
                        offset, length = data[-1]
                        data_start.value = offset+length
                    #clear the mmap area via idle_add so any pending draw requests
                    #will get a chance to run first (preserving the order)
                self.send_damage_sequence(wid, packet_sequence, width, height, -1)
            self.idle_add(draw_cleanup)
            return
        options = {}
        if len(packet)>10:
            options = packet[10]
        if DRAW_DEBUG:
            log.info("process_draw %s bytes for window %s using %s encoding with options=%s", len(data), wid, coding, options)
        start = time.time()
        def record_decode_time(success):
            if success:
                end = time.time()
                decode_time = int(end*1000*1000-start*1000*1000)
                self.pixel_counter.append((start, end, width*height))
                if DRAW_DEBUG:
                    dms = "%sms" % (int(decode_time/100)/10.0)
                    log.info("record_decode_time(%s) wid=%s, %s: %sx%s, %s", success, wid, coding, width, height, dms)
            else:
                decode_time = -1
                if DRAW_DEBUG:
                    log.info("record_decode_time(%s) decoding error on wid=%s, %s: %sx%s", success, wid, coding, width, height)
            self.send_damage_sequence(wid, packet_sequence, width, height, decode_time)
        try:
            window.draw_region(x, y, width, height, coding, data, rowstride, packet_sequence, options, [record_decode_time])
        except KeyboardInterrupt:
            raise
        except:
            log.error("draw error", exc_info=True)
            self.idle_add(record_decode_time, False)
            raise

    def _process_cursor(self, packet):
        if not self.cursors_enabled:
            return
        if len(packet)==2:
            new_cursor = packet[1]
        elif len(packet)>=8:
            new_cursor = packet[1:]
        else:
            raise Exception("invalid cursor packet: %s items" % len(packet))
        if len(new_cursor)>0:
            pixels = new_cursor[7]
            if type(pixels)==tuple:
                #newer versions encode as a list, see "compressible_cursors" capability
                import array
                a = array.array('b', '\0'* len(pixels))
                a.fromlist(list(pixels))
                new_cursor = list(new_cursor)
                new_cursor[7] = a
        self.set_windows_cursor(self._id_to_window.values(), new_cursor)

    def _process_bell(self, packet):
        if not self.bell_enabled:
            return
        (wid, device, percent, pitch, duration, bell_class, bell_id, bell_name) = packet[1:9]
        window = self._id_to_window.get(wid)
        self.window_bell(window, device, percent, pitch, duration, bell_class, bell_id, bell_name)


    def _process_notify_show(self, packet):
        if not self.notifications_enabled:
            return
        self._ui_event()
        dbus_id, nid, app_name, replaces_nid, app_icon, summary, body, expire_timeout = packet[1:9]
        log("_process_notify_show(%s)", packet)
        assert self.notifier
        #TODO: choose more appropriate tray if we have more than one shown?
        tray = self.tray
        self.notifier.show_notify(dbus_id, tray, nid, app_name, replaces_nid, app_icon, summary, body, expire_timeout)

    def _process_notify_close(self, packet):
        if not self.notifications_enabled:
            return
        assert self.notifier
        nid = packet[1]
        log("_process_notify_close(%s)", nid)
        self.notifier.close_notify(nid)


    def _process_window_metadata(self, packet):
        wid, metadata = packet[1:3]
        window = self._id_to_window.get(wid)
        if window:
            window.update_metadata(metadata)

    def _process_window_icon(self, packet):
        log("_process_window_icon(%s,%s bytes)", packet[1:5], len(packet[5]))
        wid, w, h, pixel_format, data = packet[1:6]
        window = self._id_to_window.get(wid)
        if window:
            window.update_icon(w, h, pixel_format, data)

    def _process_configure_override_redirect(self, packet):
        wid, x, y, w, h = packet[1:6]
        window = self._id_to_window[wid]
        window.move_resize(x, y, w, h)

    def _process_lost_window(self, packet):
        wid = packet[1]
        window = self._id_to_window.get(wid)
        if window:
            del self._id_to_window[wid]
            del self._window_to_id[window]
            self.destroy_window(wid, window)
        if len(self._id_to_window)==0:
            log("last window gone, clearing key repeat")
            self.keyboard_helper.clear_repeat()

    def destroy_window(self, wid, window):
        log("destroy_window(%s, %s)", wid, window)
        window.destroy()

    def _process_desktop_size(self, packet):
        root_w, root_h, max_w, max_h = packet[1:5]
        log("server has resized the desktop to: %sx%s (max %sx%s)", root_w, root_h, max_w, max_h)
        self.server_max_desktop_size = max_w, max_h
        self.server_actual_desktop_size = root_w, root_h

    def set_max_packet_size(self):
        root_w, root_h = self.get_root_size()
        maxw, maxh = root_w, root_h
        try:
            server_w, server_h = self.server_actual_desktop_size
            maxw = max(root_w, server_w)
            maxh = max(root_h, server_h)
        except:
            pass
        assert maxw>0 and maxh>0 and maxw<32768 and maxh<32768, "problems calculating maximum desktop size: %sx%s" % (maxw, maxh)
        #full screen at 32bits times 4 for safety
        self._protocol.max_packet_size = maxw*maxh*4*4
        log("set maximum packet size to %s", self._protocol.max_packet_size)


    def init_packet_handlers(self):
        XpraClientBase.init_packet_handlers(self)
        for k,v in {
            "hello":                self._process_hello,
            "startup-complete":     self._startup_complete,
            "new-window":           self._process_new_window,
            "new-override-redirect":self._process_new_override_redirect,
            "new-tray":             self._process_new_tray,
            "window-resized":       self._process_window_resized,
            "cursor":               self._process_cursor,
            "bell":                 self._process_bell,
            "notify_show":          self._process_notify_show,
            "notify_close":         self._process_notify_close,
            "window-metadata":      self._process_window_metadata,
            "configure-override-redirect":  self._process_configure_override_redirect,
            "lost-window":          self._process_lost_window,
            "desktop_size":         self._process_desktop_size,
            "window-icon":          self._process_window_icon,
            "draw":                 self._process_draw,
            # "clipboard-*" packets are handled by a special case below.
            }.items():
            self._ui_packet_handlers[k] = v
        #these handlers can run directly from the network thread:
        for k,v in {
            "ping":                 self._process_ping,
            "ping_echo":            self._process_ping_echo,
            "info-response":        self._process_info_response,
            "sound-data":           self._process_sound_data,
            }.items():
            self._packet_handlers[k] = v


    def process_packet(self, proto, packet):
        packet_type = packet[0]
        self.check_server_echo(0)
        if type(packet_type) in (unicode, str) and packet_type.startswith("clipboard-"):
            if self.clipboard_enabled and self.clipboard_helper:
                self.idle_add(self.clipboard_helper.process_clipboard_packet, packet)
        else:
            XpraClientBase.process_packet(self, proto, packet)
