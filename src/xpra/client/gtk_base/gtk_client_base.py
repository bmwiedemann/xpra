# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2019 Antoine Martin <antoine@xpra.org>
# Copyright (C) 2008, 2010 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import weakref

from xpra.gtk_common.gobject_compat import import_gobject, import_gtk, import_gdk, import_pango, is_gtk3
from xpra.client.gtk_base.gtk_client_window_base import HAS_X11_BINDINGS, XSHAPE
from xpra.gtk_common.quit import gtk_main_quit_really, gtk_main_quit_on_fatal_exceptions_enable
from xpra.util import (
    updict, pver, iround, flatten_dict, envbool, repr_ellipsized, csv, first_time,
    DEFAULT_METADATA_SUPPORTED, XPRA_OPENGL_NOTIFICATION_ID,
    )
from xpra.os_util import bytestostr, strtobytes, hexstr, monotonic_time, WIN32, OSX, POSIX
from xpra.simple_stats import std_unit
from xpra.exit_codes import EXIT_PASSWORD_REQUIRED
from xpra.scripts.config import TRUE_OPTIONS, FALSE_OPTIONS
from xpra.gtk_common.cursor_names import cursor_types
from xpra.gtk_common.gtk_util import (
    get_gtk_version_info, scaled_image, get_default_cursor, color_parse, gtk_main,
    new_Cursor_for_display, new_Cursor_from_pixbuf, icon_theme_get_default,
    pixbuf_new_from_file, display_get_default, screen_get_default, get_pixbuf_from_data,
    get_default_root_window, get_root_size, get_xwindow, image_new_from_stock,
    get_screen_sizes, load_contents_async, load_contents_finish, GDKWindow,
    FILE_CHOOSER_ACTION_OPEN,
    CLASS_INPUT_ONLY,
    RESPONSE_CANCEL, RESPONSE_OK, RESPONSE_ACCEPT,
    INTERP_BILINEAR, WINDOW_TOPLEVEL, DIALOG_MODAL, DESTROY_WITH_PARENT, MESSAGE_INFO,
    BUTTONS_CLOSE, ICON_SIZE_BUTTON, GRAB_STATUS_STRING,
    BUTTON_PRESS_MASK, BUTTON_RELEASE_MASK, POINTER_MOTION_MASK, POINTER_MOTION_HINT_MASK,
    ENTER_NOTIFY_MASK, LEAVE_NOTIFY_MASK,
    )
from xpra.gtk_common.gobject_util import no_arg_signal
from xpra.client.ui_client_base import UIXpraClient
from xpra.client.gobject_client_base import GObjectXpraClient
from xpra.client.gtk_base.gtk_keyboard_helper import GTKKeyboardHelper
from xpra.client.mixins.window_manager import WindowClient
from xpra.platform.paths import get_icon_filename
from xpra.platform.gui import (
    get_window_frame_sizes, get_window_frame_size,
    system_bell, get_wm_name, get_fixed_cursor_size, get_menu_support_function,
    )
from xpra.log import Logger

gobject = import_gobject()
gtk = import_gtk()
gdk = import_gdk()

log = Logger("gtk", "client")
opengllog = Logger("gtk", "opengl")
cursorlog = Logger("gtk", "client", "cursor")
framelog = Logger("gtk", "client", "frame")
filelog = Logger("gtk", "client", "file")
clipboardlog = Logger("gtk", "client", "clipboard")
notifylog = Logger("gtk", "notify")
grablog = Logger("client", "grab")

missing_cursor_names = set()

METADATA_SUPPORTED = os.environ.get("XPRA_METADATA_SUPPORTED")
USE_LOCAL_CURSORS = envbool("XPRA_USE_LOCAL_CURSORS", True)
EXPORT_ICON_DATA = envbool("XPRA_EXPORT_ICON_DATA", True)
SAVE_CURSORS = envbool("XPRA_SAVE_CURSORS", False)
CLIPBOARD_NOTIFY = envbool("XPRA_CLIPBOARD_NOTIFY", True)


class GTKXpraClient(GObjectXpraClient, UIXpraClient):
    __gsignals__ = {}
    #add signals from super classes (all no-arg signals)
    for signal_name in UIXpraClient.__signals__:
        __gsignals__[signal_name] = no_arg_signal

    ClientWindowClass = None
    GLClientWindowClass = None

    def __init__(self):
        GObjectXpraClient.__init__(self)
        UIXpraClient.__init__(self)
        self.session_info = None
        self.bug_report = None
        self.file_size_dialog = None
        self.file_ask_dialog = None
        self.file_dialog = None
        self.start_new_command = None
        self.server_commands = None
        self.keyboard_helper_class = GTKKeyboardHelper
        self.border = None
        self.data_send_requests = {}
        #clipboard bits:
        self.local_clipboard_requests = 0
        self.remote_clipboard_requests = 0
        self.clipboard_notification_timer = None
        self.last_clipboard_notification = 0
        #only used with the translated clipboard class:
        self.local_clipboard = ""
        self.remote_clipboard = ""
        #opengl bits:
        self.client_supports_opengl = False
        self.opengl_enabled = False
        self.opengl_props = {}
        self.gl_max_viewport_dims = 0, 0
        self.gl_texture_size_limit = 0
        self._cursors = weakref.WeakKeyDictionary()
        #frame request hidden window:
        self.frame_request_window = None
        #group leader bits:
        self._ref_to_group_leader = {}
        self._group_leader_wids = {}
        self._set_window_menu = get_menu_support_function()
        try:
            self.connect("scaling-changed", self.reset_windows_cursors)
        except TypeError:
            log("no 'scaling-changed' signal")
        #detect when the UI thread isn't responding:
        self.UI_watcher = None
        self.connect("first-ui-received", self.start_UI_watcher)


    def init(self, opts):
        GObjectXpraClient.init(self, opts)
        UIXpraClient.init(self, opts)
        self.remote_clipboard = opts.remote_clipboard
        self.local_clipboard = opts.local_clipboard


    def setup_frame_request_windows(self):
        #query the window manager to get the frame size:
        from xpra.gtk_common.error import xsync
        from xpra.x11.gtk_x11.send_wm import send_wm_request_frame_extents
        self.frame_request_window = gtk.Window(type=WINDOW_TOPLEVEL)
        self.frame_request_window.set_title("Xpra-FRAME_EXTENTS")
        root = self.get_root_window()
        self.frame_request_window.realize()
        with xsync:
            win = self.frame_request_window.get_window()
            framelog("setup_frame_request_windows() window=%#x", get_xwindow(win))
            send_wm_request_frame_extents(root, win)

    def run(self):
        log("run() HAS_X11_BINDINGS=%s", HAS_X11_BINDINGS)
        if HAS_X11_BINDINGS:
            self.setup_frame_request_windows()
        UIXpraClient.run(self)
        gtk_main_quit_on_fatal_exceptions_enable()
        self.gtk_main()
        log("GTKXpraClient.run_main_loop() main loop ended, returning exit_code=%s", self.exit_code)
        return  self.exit_code

    def gtk_main(self):
        log("GTKXpraClient.gtk_main() calling %s", gtk_main)
        gtk_main()
        log("GTKXpraClient.gtk_main() ended")


    def quit(self, exit_code=0):
        log("GTKXpraClient.quit(%s) current exit_code=%s", exit_code, self.exit_code)
        if self.exit_code is None:
            self.exit_code = exit_code
        if gtk.main_level()>0:
            #if for some reason cleanup() hangs, maybe this will fire...
            self.timeout_add(4*1000, self.exit)
            #try harder!:
            self.timeout_add(5*1000, self.force_quit)
        self.cleanup()
        log("GTKXpraClient.quit(%s) cleanup done, main_level=%s",
            exit_code, gtk.main_level())
        if gtk.main_level()>0:
            log("GTKXpraClient.quit(%s) main loop at level %s, calling gtk quit via timeout",
                exit_code, gtk.main_level())
            self.timeout_add(500, self.exit)

    def force_quit(self):
        from xpra.os_util import force_quit
        log("GTKXpraClient.force_quit() calling %s", force_quit)
        force_quit()

    def exit(self):
        log("GTKXpraClient.exit() calling %s", gtk_main_quit_really)
        gtk_main_quit_really()

    def cleanup(self):
        log("GTKXpraClient.cleanup()")
        if self.session_info:
            self.session_info.destroy()
            self.session_info = None
        if self.bug_report:
            self.bug_report.destroy()
            self.bug_report = None
        self.close_file_size_warning()
        self.close_file_upload_dialog()
        self.close_ask_data_dialog()
        self.cancel_clipboard_notification_timer()
        if self.start_new_command:
            self.start_new_command.destroy()
            self.start_new_command = None
        if self.server_commands:
            self.server_commands.destroy()
            self.server_commands = None
        uw = self.UI_watcher
        if uw:
            self.UI_watcher = None
            uw.stop()
        UIXpraClient.cleanup(self)

    def start_UI_watcher(self, _client):
        from xpra.platform.ui_thread_watcher import get_UI_watcher
        self.UI_watcher = get_UI_watcher(self.timeout_add)
        self.UI_watcher.start()
        #if server supports it, enable UI thread monitoring workaround when needed:
        def UI_resumed():
            self.send("resume", True, tuple(self._id_to_window.keys()))
        def UI_failed():
            self.send("suspend", True, tuple(self._id_to_window.keys()))
        self.UI_watcher.add_resume_callback(UI_resumed)
        self.UI_watcher.add_fail_callback(UI_failed)


    def get_notifier_classes(self):
        #subclasses may add their toolkit specific variants
        #by overriding this method
        #use the native ones first:
        from xpra.client import mixin_features
        assert mixin_features.notifications
        from xpra.client.mixins.notifications import NotificationClient
        assert isinstance(self, NotificationClient)
        ncs = NotificationClient.get_notifier_classes(self)
        try:
            from xpra.gtk_common.gtk_notifier import GTK_Notifier
            ncs.append(GTK_Notifier)
        except Exception as e:
            notifylog("get_notifier_classes()", exc_info=True)
            notifylog.warn("Warning: cannot load GTK notifier:")
            notifylog.warn(" %s", e)
        return ncs


    def _process_startup_complete(self, *args):
        UIXpraClient._process_startup_complete(self, *args)
        gdk.notify_startup_complete()


    def do_process_challenge_prompt(self, packet, prompt="password"):
        dialog = gtk.Dialog("Server Authentication",
               None,
               DIALOG_MODAL | DESTROY_WITH_PARENT)
        RESPONSE_REJECT = 1
        RESPONSE_ACCEPT = 2
        dialog.add_button(gtk.STOCK_CANCEL, RESPONSE_REJECT)
        dialog.add_button(gtk.STOCK_OK,     RESPONSE_ACCEPT)
        def add(widget, padding=0):
            a = gtk.Alignment()
            a.set(0.5, 0.5, 1, 1)
            a.add(widget)
            a.set_padding(padding, padding, padding, padding)
            dialog.vbox.pack_start(a)
        pango = import_pango()
        title = gtk.Label("Server Authentication")
        title.modify_font(pango.FontDescription("sans 14"))
        add(title, 16)
        add(gtk.Label(self.get_challenge_prompt(prompt)), 10)
        password_input = gtk.Entry()
        password_input.set_max_length(255)
        password_input.set_width_chars(32)
        password_input.set_visibility(False)
        add(password_input, 10)
        dialog.vbox.show_all()
        dialog.password_input = password_input
        def handle_response(dialog, response):
            if OSX:
                from xpra.platform.darwin.gui import disable_focus_workaround
                disable_focus_workaround()
            password = dialog.password_input.get_text()
            dialog.hide()
            dialog.destroy()
            if response!=RESPONSE_ACCEPT or not password:
                self.quit(EXIT_PASSWORD_REQUIRED)
                return
            self.send_challenge_reply(packet, password)
        def password_activate(*_args):
            handle_response(dialog, RESPONSE_ACCEPT)
        password_input.connect("activate", password_activate)
        dialog.connect("response", handle_response)
        if OSX:
            from xpra.platform.darwin.gui import enable_focus_workaround
            enable_focus_workaround()
        dialog.show()
        return True


    def parse_border(self, border_str, extra_args):
        enabled = not border_str.endswith(":off")
        parts = [x.strip() for x in border_str.replace(":off", "").split(",")]
        color_str = parts[0]
        def border_help():
            log.info(" border format: color[,size][:off]")
            log.info("  eg: red,10")
            log.info("  eg: ,5")
            log.info("  eg: auto,5")
            log.info("  eg: blue")
        if color_str.lower() in ("none", "no", "off", "0"):
            return
        if color_str.lower()=="help":
            border_help()
            return
        color_str = color_str.replace(":off", "")
        if color_str in ("auto", ""):
            from hashlib import md5
            m = md5()
            for x in extra_args:
                m.update(strtobytes(x))
            color_str = "#%s" % m.hexdigest()[:6]
            log("border color derived from %s: %s", extra_args, color_str)
        try:
            color = color_parse(color_str)
        except Exception as e:
            log.warn("invalid border color specified: '%s' (%s)", color_str, e)
            border_help()
            color = color_parse("red")
        alpha = 0.6
        size = 4
        if len(parts)==2:
            size_str = parts[1]
            try:
                size = int(size_str)
            except Exception as e:
                log.warn("Warning: invalid size specified: %s (%s)", size_str, e)
            if size<=0:
                log("border size is %s, disabling it", size)
                return
            if size>=45:
                log.warn("border size is too high: %s, clipping it", size)
                size = 45
        from xpra.client.window_border import WindowBorder
        self.border = WindowBorder(enabled, color.red/65536.0, color.green/65536.0, color.blue/65536.0, alpha, size)
        log("parse_border(%s)=%s", border_str, self.border)


    def show_server_commands(self, *_args):
        if not self.server_commands_info:
            log.warn("Warning: cannot show server commands")
            log.warn(" the feature is not available on the server")
            return
        if self.server_commands is None:
            from xpra.client.gtk_base.server_commands import getServerCommandsWindow
            self.server_commands = getServerCommandsWindow(self)
        self.server_commands.show()

    def show_start_new_command(self, *args):
        if not self.server_start_new_commands:
            log.warn("Warning: cannot start new commands")
            log.warn(" the feature is currently disabled on the server")
            return
        log("show_start_new_command%s current start_new_command=%s, flag=%s",
            args, self.start_new_command, self.server_start_new_commands)
        if self.start_new_command is None:
            from xpra.client.gtk_base.start_new_command import getStartNewCommand
            def run_command_cb(command, sharing=True):
                self.send_start_command(command, command, False, sharing)
            self.start_new_command = getStartNewCommand(run_command_cb, self.server_sharing and self.server_window_filters, self.xdg_menu)
        self.start_new_command.show()


    ################################
    # file handling
    def ask_data_request(self, cb_answer, send_id, dtype, url, filesize, printit, openit):
        self.idle_add(self.do_ask_data_request, cb_answer, send_id, dtype, url, filesize, printit, openit)

    def do_ask_data_request(self, cb_answer, send_id, dtype, url, filesize, printit, openit):
        from xpra.client.gtk_base.open_requests import getOpenRequestsWindow
        timeout = self.remote_file_ask_timeout
        def rec_answer(accept):
            if int(accept)==1:
                #record our response, so we will accept the file
                self.data_send_requests[send_id] = (dtype, url, printit, openit)
            cb_answer(accept)
        self.file_ask_dialog = getOpenRequestsWindow()
        self.file_ask_dialog.add_request(rec_answer, send_id, dtype, url, filesize, printit, openit, timeout)
        self.file_ask_dialog.show()

    def close_ask_data_dialog(self):
        fad = self.file_ask_dialog
        if fad:
            self.file_ask_dialog = None
            fad.destroy()

    def show_ask_data_dialog(self, *_args):
        from xpra.client.gtk_base.open_requests import getOpenRequestsWindow
        self.file_ask_dialog = getOpenRequestsWindow(self.show_file_upload)
        self.file_ask_dialog.show()


    def accept_data(self, send_id, dtype, url, printit, openit):
        #check if we have accepted this file via the GUI:
        r = self.data_send_requests.get(send_id)
        if r:
            del self.data_send_requests[send_id]
            if r!=(dtype, url, printit, openit):
                filelog.warn("Warning: the file attributes are different")
                filelog.warn(" from the ones that were used to accept the transfer")
                filelog.warn(" expected data type=%s, url=%s, print=%s, open=%s", *r)
                filelog.warn(" received data type=%s, url=%s, print=%s, open=%s",
                             dtype, url, printit, openit)
                return False
            return True
        from xpra.net.file_transfer import FileTransferHandler
        return FileTransferHandler.accept_data(self, send_id, dtype, url, printit, openit)

    def file_size_warning(self, action, location, basefilename, filesize, limit):
        if self.file_size_dialog:
            #close previous warning
            self.file_size_dialog.destroy()
            self.file_size_dialog = None
        parent = None
        msgs = (
                "Warning: cannot %s the file '%s'" % (action, basefilename),
                "this file is too large: %sB" % std_unit(filesize, unit=1024),
                "the %s file size limit is %iMB" % (location, limit),
                )
        self.file_size_dialog = gtk.MessageDialog(parent, DESTROY_WITH_PARENT, MESSAGE_INFO,
                                                  BUTTONS_CLOSE, "\n".join(msgs))
        try:
            image = image_new_from_stock(gtk.STOCK_DIALOG_WARNING, ICON_SIZE_BUTTON)
            self.file_size_dialog.set_image(image)
        except Exception as e:
            log.warn("Warning: failed to set dialog image: %s", e)
        self.file_size_dialog.connect("response", self.close_file_size_warning)
        self.file_size_dialog.show()

    def close_file_size_warning(self, *_args):
        fsd = self.file_size_dialog
        if fsd:
            self.file_size_dialog = None
            fsd.destroy()

    def show_file_upload(self, *args):
        if self.file_dialog:
            self.file_dialog.present()
            return
        filelog("show_file_upload%s can open=%s", args, self.remote_open_files)
        buttons = [gtk.STOCK_CANCEL,    RESPONSE_CANCEL]
        if self.remote_open_files:
            buttons += [gtk.STOCK_OPEN,      RESPONSE_ACCEPT]
        buttons += [gtk.STOCK_OK,        RESPONSE_OK]
        self.file_dialog = gtk.FileChooserDialog(
            "File to upload",
            parent=None,
            action=FILE_CHOOSER_ACTION_OPEN,
            buttons=tuple(buttons))
        self.file_dialog.set_default_response(RESPONSE_OK)
        self.file_dialog.connect("response", self.file_upload_dialog_response)
        self.file_dialog.show()

    def close_file_upload_dialog(self):
        fd = self.file_dialog
        if fd:
            fd.destroy()
            self.file_dialog = None

    def file_upload_dialog_response(self, dialog, v):
        if v not in (RESPONSE_OK, RESPONSE_ACCEPT):
            filelog("dialog response code %s", v)
            self.close_file_upload_dialog()
            return
        filename = dialog.get_filename()
        filelog("file_upload_dialog_response: filename=%s", filename)
        try:
            filesize = os.stat(filename).st_size
        except (OSError, IOError):
            pass
        else:
            if not self.check_file_size("upload", filename, filesize):
                self.close_file_upload_dialog()
                return
        gfile = dialog.get_file()
        self.close_file_upload_dialog()
        filelog("load_contents: filename=%s, response=%s", filename, v)
        load_contents_async(gfile, self.file_upload_ready, user_data=(filename, v==RESPONSE_ACCEPT))

    def file_upload_ready(self, gfile, result, user_data):
        filelog("file_upload_ready%s", (gfile, result, user_data))
        filename, openit = user_data
        data, filesize, entity = load_contents_finish(gfile, result)
        filelog("load_contents_finish(%s)=%s", result, (type(data), filesize, entity))
        if not data:
            log.warn("Warning: failed to load file '%s'", filename)
            return
        filelog("load_contents: filename=%s, %i bytes, entity=%s, openit=%s",
                filename, filesize, entity, openit)
        self.send_file(filename, "", data, filesize=filesize, openit=openit)


    def show_about(self, *_args):
        from xpra.gtk_common.about import about
        about()

    def show_session_info(self, *args):
        if self.session_info and not self.session_info.is_closed:
            #exists already: just raise its window:
            self.session_info.set_args(*args)
            self.session_info.present()
            return
        pixbuf = self.get_pixbuf("statistics.png")
        if not pixbuf:
            pixbuf = self.get_pixbuf("xpra.png")
        p = self._protocol
        conn = p._conn if p else None
        from xpra.client.gtk_base.session_info import SessionInfo
        self.session_info = SessionInfo(self, self.session_name, pixbuf, conn, self.get_pixbuf)
        self.session_info.set_args(*args)
        self.session_info.show_all()

    def show_bug_report(self, *_args):
        self.send_info_request()
        if self.bug_report:
            self.bug_report.show()
            return
        from xpra.client.gtk_base.bug_report import BugReport
        self.bug_report = BugReport()
        def init_bug_report():
            #skip things we aren't using:
            includes ={
                       "keyboard"       : bool(self.keyboard_helper),
                       "opengl"         : self.opengl_enabled,
                       }
            def get_server_info():
                return self.server_last_info
            self.bug_report.init(show_about=False,
                                 get_server_info=get_server_info,
                                 opengl_info=self.opengl_props,
                                 includes=includes)
            self.bug_report.show()
        #gives the server time to send an info response..
        #(by the time the user clicks on copy, it should have arrived, we hope!)
        self.timeout_add(200, init_bug_report)


    def get_pixbuf(self, icon_name):
        try:
            if not icon_name:
                log("get_pixbuf(%s)=None", icon_name)
                return None
            icon_filename = get_icon_filename(icon_name)
            log("get_pixbuf(%s) icon_filename=%s", icon_name, icon_filename)
            if icon_filename:
                return pixbuf_new_from_file(icon_filename)
        except:
            log.error("get_pixbuf(%s)", icon_name, exc_info=True)
        return None


    def get_image(self, icon_name, size=None):
        try:
            pixbuf = self.get_pixbuf(icon_name)
            log("get_image(%s, %s) pixbuf=%s", icon_name, size, pixbuf)
            if not pixbuf:
                return  None
            return scaled_image(pixbuf, size)
        except:
            log.error("get_image(%s, %s)", icon_name, size, exc_info=True)
            return None


    def request_frame_extents(self, window):
        from xpra.x11.gtk_x11.send_wm import send_wm_request_frame_extents
        from xpra.gtk_common.error import xsync
        root = self.get_root_window()
        with xsync:
            win = window.get_window()
            framelog("request_frame_extents(%s) xid=%#x", window, get_xwindow(win))
            send_wm_request_frame_extents(root, win)

    def get_frame_extents(self, window):
        #try native platform code first:
        x, y = window.get_position()
        w, h = window.get_size()
        v = get_window_frame_size(x, y, w, h)
        framelog("get_window_frame_size%s=%s", (x, y, w, h), v)
        if v:
            #(OSX does give us these values via Quartz API)
            return v
        if not HAS_X11_BINDINGS:
            #nothing more we can do!
            return None
        from xpra.x11.gtk_x11.prop import prop_get
        gdkwin = window.get_window()
        assert gdkwin
        v = prop_get(gdkwin, "_NET_FRAME_EXTENTS", ["u32"], ignore_errors=False)
        framelog("get_frame_extents(%s)=%s", window.get_title(), v)
        return v

    def get_window_frame_sizes(self):
        wfs = get_window_frame_sizes()
        if self.frame_request_window:
            v = self.get_frame_extents(self.frame_request_window)
            if v:
                try:
                    wm_name = get_wm_name()
                except:
                    wm_name = None
                try:
                    if len(v)==8:
                        if first_time("invalid-frame-extents"):
                            framelog.warn("Warning: invalid frame extents value '%s'", v)
                            if wm_name:
                                framelog.warn(" this is probably a bug in '%s'", wm_name)
                            framelog.warn(" using '%s' instead", v[4:])
                        v = v[4:]
                    l, r, t, b = v
                    wfs["frame"] = (l, r, t, b)
                    wfs["offset"] = (l, t)
                except Exception as e:
                    framelog.warn("Warning: invalid frame extents value '%s'", v)
                    framelog.warn(" %s", e)
                    framelog.warn(" this is probably a bug in '%s'", wm_name)
        framelog("get_window_frame_sizes()=%s", wfs)
        return wfs


    def _add_statusicon_tray(self, tray_list):
        #add gtk.StatusIcon tray:
        try:
            from xpra.client.gtk_base.statusicon_tray import GTKStatusIconTray
            tray_list.append(GTKStatusIconTray)
        except Exception as e:
            log.warn("failed to load StatusIcon tray: %s" % e)
        return tray_list

    def get_tray_classes(self):
        from xpra.client.mixins.tray import TrayClient
        return self._add_statusicon_tray(TrayClient.get_tray_classes(self))

    def get_system_tray_classes(self):
        return self._add_statusicon_tray(WindowClient.get_system_tray_classes(self))


    def supports_system_tray(self):
        #always True: we can always use gtk.StatusIcon as fallback
        return True


    def cook_metadata(self, new_window, metadata):
        metadata = UIXpraClient.cook_metadata(self, new_window, metadata)
        #ensures we will call set_window_menu for this window when we create it:
        if new_window and self._set_window_menu:
            menu = metadata.dictget("menu")
            if menu is None:
                metadata["menu"] = {}
        return metadata


    def set_window_menu(self, add, wid, menus, application_action_callback=None, window_action_callback=None):
        assert self._set_window_menu
        model = self._id_to_window.get(wid)
        window = None
        if model:
            window = model.get_window()
        self._set_window_menu(add, wid, window, menus, application_action_callback, window_action_callback)


    def get_root_window(self):
        return get_default_root_window()

    def get_root_size(self):
        return get_root_size()


    def get_mouse_position(self):
        p = self.get_root_window().get_pointer()[-3:-1]
        return self.cp(p[0], p[1])

    def get_current_modifiers(self):
        modifiers_mask = self.get_root_window().get_pointer()[-1]
        return self.mask_to_names(modifiers_mask)


    def make_hello(self):
        capabilities = UIXpraClient.make_hello(self)
        capabilities["named_cursors"] = len(cursor_types)>0
        capabilities.update(flatten_dict(get_gtk_version_info()))
        if EXPORT_ICON_DATA:
            #tell the server which icons GTK can use
            #so it knows when it should supply one as fallback
            it = icon_theme_get_default()
            #this would add our bundled icon directory
            #to the search path, but I don't think we have
            #any extra icons that matter in there:
            #from xpra.platform.paths import get_icon_dir
            #d = get_icon_dir()
            #if d not in it.get_search_path():
            #    it.append_search_path(d)
            #    it.rescan_if_needed()
            log("default icon theme: %s", it)
            log("icon search path: %s", it.get_search_path())
            log("contexts: %s", it.list_contexts())
            icons = []
            for context in it.list_contexts():
                icons += it.list_icons(context)
            log("icons: %s", icons)
            capabilities["theme.default.icons"] = tuple(set(icons))
        if METADATA_SUPPORTED:
            ms = [x.strip() for x in METADATA_SUPPORTED.split(",")]
        else:
            #this is currently unused, and slightly redundant because of metadata.supported below:
            capabilities["window.states"] = [
                "fullscreen", "maximized",
                "sticky", "above", "below",
                "shaded", "iconified",
                "skip-taskbar", "skip-pager",
                ]
            ms = list(DEFAULT_METADATA_SUPPORTED)
            #added in 0.15:
            ms += ["command", "workspace", "above", "below", "sticky",
                   "set-initial-position"]  #0.17
        if POSIX:
            #this is only really supported on X11, but posix is easier to check for..
            #"strut" and maybe even "fullscreen-monitors" could also be supported on other platforms I guess
            ms += ["shaded", "bypass-compositor", "strut", "fullscreen-monitors"]
        if HAS_X11_BINDINGS and XSHAPE:
            ms += ["shape"]
        if self._set_window_menu:
            ms += ["menu"]
        #figure out if we can handle the "global menu" stuff:
        if POSIX and not OSX:
            try:
                from xpra.dbus.helper import DBusHelper
                assert DBusHelper
            except ImportError:
                pass
        log("metadata.supported: %s", ms)
        capabilities["metadata.supported"] = ms
        updict(capabilities, "pointer", {
            "grabs" : True,
            "relative" : True,
            })
        updict(capabilities, "window", {
               "initiate-moveresize"    : True,
               "configure.pointer"      : True,
               "frame_sizes"            : self.get_window_frame_sizes()
               })
        updict(capabilities, "encoding", {
                    "icons.greedy"      : True,         #we don't set a default window icon any more
                    "icons.size"        : (64, 64),     #size we want
                    "icons.max_size"    : (128, 128),   #limit
                    })
        from xpra.client import window_backing_base
        if self._protocol._conn.socktype=="udp":
            #lossy protocol means we can't use delta regions:
            log("no delta buckets with udp, since we can drop paint packets")
            window_backing_base.DELTA_BUCKETS = 0
        updict(capabilities, "encoding", {
                    "delta_buckets"     : window_backing_base.DELTA_BUCKETS,
                    })
        return capabilities


    def has_transparency(self):
        return screen_get_default().get_rgba_visual() is not None


    def get_screen_sizes(self, xscale=1, yscale=1):
        return get_screen_sizes(xscale, yscale)


    def reset_windows_cursors(self, *_args):
        cursorlog("reset_windows_cursors() resetting cursors for: %s", tuple(self._cursors.keys()))
        for w,cursor_data in tuple(self._cursors.items()):
            self.set_windows_cursor([w], cursor_data)


    def set_windows_cursor(self, windows, cursor_data):
        cursorlog("set_windows_cursor(%s, args[%i])", windows, len(cursor_data))
        cursor = None
        if cursor_data:
            try:
                cursor = self.make_cursor(cursor_data)
                cursorlog("make_cursor(..)=%s", cursor)
            except Exception as e:
                log.warn("error creating cursor: %s (using default)", e, exc_info=True)
            if cursor is None:
                #use default:
                cursor = get_default_cursor()
        for w in windows:
            w.set_cursor_data(cursor_data)
            gdkwin = w.get_window()
            #trays don't have a gdk window
            if gdkwin:
                self._cursors[w] = cursor_data
                gdkwin.set_cursor(cursor)

    def make_cursor(self, cursor_data):
        #if present, try cursor ny name:
        display = display_get_default()
        cursorlog("make_cursor: has-name=%s, has-cursor-types=%s, xscale=%s, yscale=%s, USE_LOCAL_CURSORS=%s",
                  len(cursor_data)>=10, bool(cursor_types), self.xscale, self.yscale, USE_LOCAL_CURSORS)
        #named cursors cannot be scaled
        #(round to 10 to compare so 0.95 and 1.05 are considered the same as 1.0, no scaling):
        if len(cursor_data)>=10 and cursor_types and iround(self.xscale*10)==10 and iround(self.yscale*10)==10:
            cursor_name = bytestostr(cursor_data[9])
            if cursor_name and USE_LOCAL_CURSORS:
                gdk_cursor = cursor_types.get(cursor_name.upper())
                if gdk_cursor is not None:
                    cursorlog("setting new cursor by name: %s=%s", cursor_name, gdk_cursor)
                    return new_Cursor_for_display(display, gdk_cursor)
                global missing_cursor_names
                if cursor_name not in missing_cursor_names:
                    cursorlog("cursor name '%s' not found", cursor_name)
                    missing_cursor_names.add(cursor_name)
        #create cursor from the pixel data:
        encoding, _, _, w, h, xhot, yhot, serial, pixels = cursor_data[0:9]
        encoding = strtobytes(encoding)
        if encoding!=b"raw":
            cursorlog.warn("Warning: invalid cursor encoding: %s", encoding)
            return None
        if len(pixels)<w*h*4:
            cursorlog.warn("Warning: not enough pixels provided in cursor data")
            cursorlog.warn(" %s needed and only %s bytes found:", w*h*4, len(pixels))
            cursorlog.warn(" '%s')", repr_ellipsized(hexstr(pixels)))
            return None
        pixbuf = get_pixbuf_from_data(pixels, True, w, h, w*4)
        x = max(0, min(xhot, w-1))
        y = max(0, min(yhot, h-1))
        csize = display.get_default_cursor_size()
        cmaxw, cmaxh = display.get_maximal_cursor_size()
        if len(cursor_data)>=12:
            ssize = cursor_data[10]
            smax = cursor_data[11]
            cursorlog("server cursor sizes: default=%s, max=%s", ssize, smax)
        cursorlog("new %s cursor at %s,%s with serial=%#x, dimensions: %sx%s, len(pixels)=%s",
                  encoding, xhot, yhot, serial, w, h, len(pixels))
        cursorlog("default cursor size is %s, maximum=%s", csize, (cmaxw, cmaxh))
        fw, fh = get_fixed_cursor_size()
        if fw>0 and fh>0 and (w!=fw or h!=fh):
            #OS wants a fixed cursor size! (win32 does, and GTK doesn't do this for us)
            if w<=fw and h<=fh:
                from PIL import Image
                img = Image.frombytes("RGBA", (w, h), pixels, "raw", "BGRA", w*4, 1)
                target = Image.new("RGBA", (fw, fh))
                target.paste(img, (0, 0, w, h))
                pixels = img.tobytes("raw", "BGRA")
                cursor_pixbuf = get_pixbuf_from_data(pixels, True, w, h, w*4)
            else:
                cursorlog("scaling cursor from %ix%i to fixed OS size %ix%i", w, h, fw, fh)
                cursor_pixbuf = pixbuf.scale_simple(fw, fh, INTERP_BILINEAR)
                xratio, yratio = float(w)/fw, float(h)/fh
                x, y = iround(x/xratio), iround(y/yratio)
        else:
            sx, sy, sw, sh = x, y, w, h
            #scale the cursors:
            if self.xscale!=1 or self.yscale!=1:
                sx, sy, sw, sh = self.srect(x, y, w, h)
            sw = max(1, sw)
            sh = max(1, sh)
            #ensure we honour the max size if there is one:
            if 0<cmaxw<sw or 0<cmaxh<sh:
                ratio = 1.0
                if cmaxw>0:
                    ratio = max(ratio, float(w)/cmaxw)
                if cmaxh>0:
                    ratio = max(ratio, float(h)/cmaxh)
                cursorlog("clamping cursor size to %ix%i using ratio=%s", cmaxw, cmaxh, ratio)
                sx, sy = iround(x/ratio), iround(y/ratio)
                sw, sh = min(cmaxw, iround(w/ratio)), min(cmaxh, iround(h/ratio))
            if sw!=w or sh!=h:
                cursorlog("scaling cursor from %ix%i hotspot at %ix%i to %ix%i hotspot at %ix%i",
                          w, h, x, y, sw, sh, sx, sy)
                cursor_pixbuf = pixbuf.scale_simple(sw, sh, INTERP_BILINEAR)
                x, y = sx, sy
            else:
                cursor_pixbuf = pixbuf
        if SAVE_CURSORS:
            cursor_pixbuf.save("cursor-%#x.png" % serial, "png")
        #clamp to pixbuf size:
        w = cursor_pixbuf.get_width()
        h = cursor_pixbuf.get_height()
        x = max(0, min(x, w-1))
        y = max(0, min(y, h-1))
        try:
            c = new_Cursor_from_pixbuf(display, cursor_pixbuf, x, y)
        except RuntimeError as e:
            log.error("Error: failed to create cursor:")
            log.error(" %s", e)
            log.error(" using %s of size %ix%i with hotspot at %ix%i", cursor_pixbuf, w, h, x, y)
            c = None
        return c


    def process_ui_capabilities(self):
        UIXpraClient.process_ui_capabilities(self)
        #this requires the "DisplayClient" mixin:
        if not hasattr(self, "screen_size_changed"):
            return
        display = display_get_default()
        if is_gtk3():
            #always one screen per display:
            screen = gdk.Screen.get_default()
            screen.connect("size-changed", self.screen_size_changed)
        else:
            i=0
            while i<display.get_n_screens():
                screen = display.get_screen(i)
                screen.connect("size-changed", self.screen_size_changed)
                i += 1


    def window_grab(self, window):
        event_mask = (BUTTON_PRESS_MASK |
                      BUTTON_RELEASE_MASK |
                      POINTER_MOTION_MASK  |
                      POINTER_MOTION_HINT_MASK |
                      ENTER_NOTIFY_MASK |
                      LEAVE_NOTIFY_MASK)
        confine_to = None
        cursor = None
        r = gdk.pointer_grab(window.get_window(), True, event_mask, confine_to, cursor, 0)
        grablog("pointer_grab(..)=%s", GRAB_STATUS_STRING.get(r, r))
        #also grab the keyboard so the user won't Alt-Tab away:
        r = gdk.keyboard_grab(window.get_window(), False, 0)
        grablog("keyboard_grab(..)=%s", GRAB_STATUS_STRING.get(r, r))

    def window_ungrab(self):
        grablog("window_ungrab()")
        gdk.pointer_ungrab(0)
        gdk.keyboard_ungrab(0)


    def window_bell(self, window, device, percent, pitch, duration, bell_class, bell_id, bell_name):
        gdkwindow = None
        if window:
            gdkwindow = window.get_window()
        if gdkwindow is None:
            gdkwindow = self.get_root_window()
        log("window_bell(..) gdkwindow=%s", gdkwindow)
        if not system_bell(gdkwindow, device, percent, pitch, duration, bell_class, bell_id, bell_name):
            #fallback to simple beep:
            gdk.beep()


    def _process_raise_window(self, packet):
        wid = packet[1]
        window = self._id_to_window.get(wid)
        log("going to raise window %s - %s", wid, window)
        if window:
            if window.has_toplevel_focus():
                log("window already has top level focus")
                return
            window.present()


    def opengl_setup_failure(self, summary = "Xpra OpenGL Acceleration Failure", body=""):
        def delayed_notify():
            if self.exit_code is None:
                self.may_notify(XPRA_OPENGL_NOTIFICATION_ID, summary, body, icon_name="opengl")
        #wait for the main loop to run:
        self.timeout_add(2*1000, delayed_notify)

    #OpenGL bits:
    def init_opengl(self, enable_opengl):
        opengllog("init_opengl(%s)", enable_opengl)
        #enable_opengl can be True, False, probe-failed, probe-success, or None (auto-detect)
        #ie: "on:native,gtk", "auto", "no"
        #ie: "probe-failed:SIGSEGV"
        #ie: "probe-success"
        enable_opengl = (enable_opengl or "").lower()
        parts = enable_opengl.split(":", 1)
        enable_option = parts[0]            #ie: "on"
        opengllog("init_opengl: enable_option=%s", enable_option)
        if enable_option=="probe-failed":
            msg = "probe failed: %s" % csv(parts[1:])
            self.opengl_props["info"] = "disabled, %s" % msg
            self.opengl_setup_failure(body=msg)
            return
        if enable_option in FALSE_OPTIONS:
            self.opengl_props["info"] = "disabled by configuration"
            return
        from xpra.scripts.config import OpenGL_safety_check
        from xpra.platform.gui import gl_check as platform_gl_check
        warnings = []
        for check in (OpenGL_safety_check, platform_gl_check):
            opengllog("checking with %s", check)
            warning = check()
            opengllog("%s()=%s", check, warning)
            if warning:
                warnings.append(warning)
        self.opengl_props["info"] = ""

        def err(msg, e):
            opengllog("OpenGL initialization error", exc_info=True)
            self.GLClientWindowClass = None
            self.client_supports_opengl = False
            opengllog.error("%s", msg)
            for x in str(e).split("\n"):
                opengllog.error(" %s", x)
            self.opengl_props["info"] = str(e)
            self.opengl_setup_failure(body=str(e))

        if warnings:
            if enable_option in ("", "auto"):
                opengllog.warn("OpenGL disabled:")
                for warning in warnings:
                    opengllog.warn(" %s", warning)
                self.opengl_props["info"] = "disabled: %s" % csv(warnings)
                return
            if enable_option=="probe-success":
                opengllog.warn("OpenGL enabled, despite some warnings:")
            else:
                opengllog.warn("OpenGL safety warning (enabled at your own risk):")
            for warning in warnings:
                opengllog.warn(" %s", warning)
            self.opengl_props["info"] = "enabled despite: %s" % csv(warnings)
        try:
            opengllog("init_opengl: going to import xpra.client.gl")
            __import__("xpra.client.gl", {}, {}, [])
            from xpra.client.gl.window_backend import (
                get_opengl_backends,
                get_gl_client_window_module,
                test_gl_client_window,
                )
            backends = get_opengl_backends(enable_opengl)
            opengllog("init_opengl: backend options: %s", backends)
            force_enable = enable_option in TRUE_OPTIONS
            self.opengl_props, gl_client_window_module = get_gl_client_window_module(backends, force_enable)
            if not gl_client_window_module:
                opengllog.warn("Warning: no OpenGL backends found")
                self.client_supports_opengl = False
                self.opengl_props["info"] = "disabled: no supported backends - tried: %s" % csv(backends)
                return
            opengllog("init_opengl: found props %s", self.opengl_props)
            self.GLClientWindowClass = gl_client_window_module.GLClientWindow
            self.client_supports_opengl = True
            #only enable opengl by default if force-enabled or if safe to do so:
            self.opengl_enabled = enable_option in (list(TRUE_OPTIONS)+["auto"]) or self.opengl_props.get("safe", False)
            self.gl_texture_size_limit = self.opengl_props.get("texture-size-limit", 16*1024)
            self.gl_max_viewport_dims = self.opengl_props.get("max-viewport-dims",
                                                              (self.gl_texture_size_limit, self.gl_texture_size_limit))
            if min(self.gl_max_viewport_dims)<4*1024:
                opengllog.warn("Warning: OpenGL is disabled:")
                opengllog.warn(" the maximum viewport size is too low: %s", self.gl_max_viewport_dims)
                self.opengl_enabled = False
            elif self.gl_texture_size_limit<4*1024:
                opengllog.warn("Warning: OpenGL is disabled:")
                opengllog.warn(" the texture size limit is too low: %s", self.gl_texture_size_limit)
                self.opengl_enabled = False
            self.GLClientWindowClass.MAX_VIEWPORT_DIMS = self.gl_max_viewport_dims
            self.GLClientWindowClass.MAX_BACKING_DIMS = self.gl_texture_size_limit, self.gl_texture_size_limit
            mww, mwh = self.max_window_size
            opengllog("OpenGL: enabled=%s, texture-size-limit=%s, max-window-size=%s",
                      self.opengl_enabled, self.gl_texture_size_limit, self.max_window_size)
            if self.opengl_enabled and self.gl_texture_size_limit<16*1024 and (mww==0 or mwh==0 or self.gl_texture_size_limit<mww or self.gl_texture_size_limit<mwh):
                #log at warn level if the limit is low:
                #(if we're likely to hit it - if the screen is as big or bigger)
                w, h = self.get_root_size()
                l = opengllog.info
                if w*2<=self.gl_texture_size_limit and h*2<=self.gl_texture_size_limit:
                    l = opengllog
                if w>=self.gl_texture_size_limit or h>=self.gl_texture_size_limit:
                    l = opengllog.warn
                l("Warning: OpenGL windows will be clamped to the maximum texture size %ix%i",
                  self.gl_texture_size_limit, self.gl_texture_size_limit)
                l(" for OpenGL %s renderer '%s'", pver(self.opengl_props.get("opengl", "")), self.opengl_props.get("renderer", "unknown"))
            if self.opengl_enabled:
                draw_result = test_gl_client_window(self.GLClientWindowClass, max_window_size=self.max_window_size, pixel_depth=self.pixel_depth)
                if not draw_result.get("success", False):
                    err("OpenGL test rendering failed:", draw_result.get("message", "unknown error"))
                    return
                log("OpenGL test rendering succeeded")
            driver_info = self.opengl_props.get("renderer") or self.opengl_props.get("vendor") or "unknown card"
            if self.opengl_enabled:
                opengllog.info("OpenGL enabled with %s", driver_info)
                #don't try to handle video dimensions bigger than this:
                mvs = min(8192, self.gl_texture_size_limit)
                self.video_max_size = (mvs, mvs)
            elif self.client_supports_opengl:
                opengllog("OpenGL supported with %s, but not enabled", driver_info)
        except ImportError as e:
            err("OpenGL accelerated rendering is not available:", e)
        except RuntimeError as e:
            err("OpenGL support could not be enabled on this hardware:", e)
        except Exception as e:
            err("Error loading OpenGL support:", e)
            opengllog("init_opengl(%s)", enable_opengl, exc_info=True)

    def get_client_window_classes(self, w, h, metadata, override_redirect):
        log("get_client_window_class%s GLClientWindowClass=%s, opengl_enabled=%s, mmap_enabled=%s, encoding=%s",
            (w, h, metadata, override_redirect),
            self.GLClientWindowClass,self.opengl_enabled, self.mmap_enabled, self.encoding)
        if self.GLClientWindowClass is None or not self.opengl_enabled:
            return (self.ClientWindowClass,)
        #verify texture limits:
        ms = min(self.sx(self.gl_texture_size_limit), *self.gl_max_viewport_dims)
        if w>ms or h>ms:
            return (self.ClientWindowClass,)
        if WIN32:
            #win32 opengl doesn't do alpha (not sure why):
            if override_redirect:
                return (self.ClientWindowClass,)
            if metadata.boolget("has-alpha", False):
                return (self.ClientWindowClass,)
        if OSX:
            #OSX doesn't do alpha:
            if metadata.boolget("has-alpha", False):
                return (self.ClientWindowClass,)
        return (self.GLClientWindowClass, self.ClientWindowClass)

    def toggle_opengl(self, *_args):
        self.opengl_enabled = not self.opengl_enabled
        opengllog("opengl_toggled: %s", self.opengl_enabled)
        #now replace all the windows with new ones:
        for wid, window in self._id_to_window.items():
            self.reinit_window(wid, window)
        opengllog("replaced all the windows with opengl=%s: %s", self.opengl_enabled, self._id_to_window)
        self.reinit_window_icons()


    def get_group_leader(self, wid, metadata, _override_redirect):
        transient_for = metadata.intget("transient-for", -1)
        log("get_group_leader: transient_for=%s", transient_for)
        if transient_for>0:
            client_window = self._id_to_window.get(transient_for)
            if client_window:
                gdk_window = client_window.get_window()
                if gdk_window:
                    return gdk_window
        pid = metadata.intget("pid", -1)
        leader_xid = metadata.intget("group-leader-xid", -1)
        leader_wid = metadata.intget("group-leader-wid", -1)
        group_leader_window = self._id_to_window.get(leader_wid)
        if group_leader_window:
            #leader is another managed window
            log("found group leader window %s for wid=%s", group_leader_window, leader_wid)
            return group_leader_window
        log("get_group_leader: leader pid=%s, xid=%s, wid=%s", pid, leader_xid, leader_wid)
        reftype = "xid"
        ref = leader_xid
        if ref<0:
            reftype = "leader-wid"
            ref = leader_wid
        if ref<0:
            ci = metadata.strlistget("class-instance")
            if ci:
                reftype = "class"
                ref = "|".join(ci)
            elif pid>0:
                reftype = "pid"
                ref = pid
            elif transient_for>0:
                #this should have matched a client window above..
                #but try to use it anyway:
                reftype = "transient-for"
                ref = transient_for
            else:
                #no reference to use
                return None
        refkey = "%s:%s" % (reftype, ref)
        group_leader_window = self._ref_to_group_leader.get(refkey)
        if group_leader_window:
            log("found existing group leader window %s using ref=%s", group_leader_window, refkey)
            return group_leader_window
        #we need to create one:
        title = u"%s group leader for %s" % (self.session_name or u"Xpra", pid)
        #group_leader_window = gdk.Window(None, 1, 1, gdk.WINDOW_TOPLEVEL, 0, gdk.INPUT_ONLY, title)
        #static new(parent, attributes, attributes_mask)
        group_leader_window = GDKWindow(wclass=CLASS_INPUT_ONLY, title=title)
        self._ref_to_group_leader[refkey] = group_leader_window
        #avoid warning on win32...
        if not WIN32:
            #X11 spec says window should point to itself:
            group_leader_window.set_group(group_leader_window)
        log("new hidden group leader window %s for ref=%s", group_leader_window, refkey)
        self._group_leader_wids.setdefault(group_leader_window, []).append(wid)
        return group_leader_window

    def destroy_window(self, wid, window):
        #override so we can cleanup the group-leader if needed,
        WindowClient.destroy_window(self, wid, window)
        group_leader = window.group_leader
        if group_leader is None or not self._group_leader_wids:
            return
        wids = self._group_leader_wids.get(group_leader)
        if wids is None:
            #not recorded any window ids on this group leader
            #means it is another managed window, leave it alone
            return
        if wid in wids:
            wids.remove(wid)
        if wids:
            #still has another window pointing to it
            return
        #the last window has gone, we can remove the group leader,
        #find all the references to this group leader:
        del self._group_leader_wids[group_leader]
        refs = []
        for ref, gl in self._ref_to_group_leader.items():
            if gl==group_leader:
                refs.append(ref)
        for ref in refs:
            del self._ref_to_group_leader[ref]
        log("last window for refs %s is gone, destroying the group leader %s", refs, group_leader)
        group_leader.destroy()


    def get_clipboard_helper_classes(self):
        ct = self.client_clipboard_type
        if ct and ct.lower() in FALSE_OPTIONS:
            return []
        from xpra.platform.features import CLIPBOARD_NATIVE_CLASS
        from xpra.scripts.main import CLIPBOARD_CLASS
        #first add the platform specific one, (may be None):
        clipboard_options = []
        if CLIPBOARD_CLASS:
            clipboard_options.append(CLIPBOARD_CLASS)
        if CLIPBOARD_NATIVE_CLASS:
            clipboard_options.append(CLIPBOARD_NATIVE_CLASS)
        clipboard_options.append("xpra.clipboard.gdk_clipboard.GDKClipboardProtocolHelper")
        clipboard_options.append("xpra.clipboard.clipboard_base.DefaultClipboardProtocolHelper")
        clipboard_options.append("xpra.clipboard.translated_clipboard.TranslatedClipboardProtocolHelper")
        clipboardlog("get_clipboard_helper_classes() unfiltered list=%s", clipboard_options)
        if ct and ct.lower()!="auto" and ct.lower() not in TRUE_OPTIONS:
            #try to match the string specified:
            filtered = [x for x in clipboard_options if x.lower().find(self.client_clipboard_type)>=0]
            if not filtered:
                clipboardlog.warn("Warning: no clipboard types matching '%s'", self.client_clipboard_type)
                clipboardlog.warn(" clipboard synchronization is disabled")
                return []
            clipboardlog(" found %i clipboard types matching '%s'", len(filtered), self.client_clipboard_type)
            clipboard_options = filtered
        #now try to load them:
        clipboardlog("get_clipboard_helper_classes() options=%s", clipboard_options)
        loadable = []
        for co in clipboard_options:
            try:
                parts = co.split(".")
                mod = ".".join(parts[:-1])
                module = __import__(mod, {}, {}, [parts[-1]])
                helperclass = getattr(module, parts[-1])
                loadable.append(helperclass)
            except ImportError:
                clipboardlog("cannot load %s", co, exc_info=True)
                continue
        clipboardlog("get_clipboard_helper_classes()=%s", loadable)
        return loadable

    def process_clipboard_packet(self, packet):
        clipboardlog("process_clipboard_packet(%s) level=%s", packet, gtk.main_level())
        #check for clipboard loops:
        from xpra.clipboard.clipboard_base import nesting_check
        if not nesting_check():
            self.clipboard_enabled = False
            self.emit("clipboard-toggled")
            return
        self.idle_add(self.clipboard_helper.process_clipboard_packet, packet)


    def setup_clipboard_helper(self, helperClass):
        clipboardlog("setup_clipboard_helper(%s)", helperClass)
        #first add the platform specific one, (may be None):
        from xpra.platform.features import CLIPBOARDS
        kwargs= {
                #all the local clipboards supported:
                 "clipboards.local"     : CLIPBOARDS,
                 #all the remote clipboards supported:
                 "clipboards.remote"    : self.server_clipboards,
                 "can-send"             : self.client_clipboard_direction in ("to-server", "both"),
                 "can-receive"          : self.client_clipboard_direction in ("to-client", "both"),
                 "remote-loop-uuids"    : self.server_clipboard_loop_uuids,
                 }
        #only allow translation overrides if we have a way of telling the server about them:
        if self.server_clipboard_enable_selections:
            kwargs.update({
                #the local clipboard we want to sync to (with the translated clipboard only):
                 "clipboard.local"      : self.local_clipboard,
                 #the remote clipboard we want to we sync to (with the translated clipboard only):
                 "clipboard.remote"     : self.remote_clipboard
                 })
        clipboardlog("setup_clipboard_helper() kwargs=%s", kwargs)
        def clipboard_send(*parts):
            clipboardlog("clipboard_send: %s", parts[0])
            if not self.clipboard_enabled:
                clipboardlog("clipboard is disabled, not sending clipboard packet")
                return
            #handle clipboard compression if needed:
            from xpra.net.compression import Compressible
            packet = list(parts)
            for v in packet:
                if isinstance(v, Compressible):
                    register_clipboard_compress_cb(v)
            self.send_now(*packet)
        def register_clipboard_compress_cb(compressible):
            #register the compressor which will fire in protocol.encode:
            def compress_clipboard():
                clipboardlog("compress_clipboard() compressing %s", compressible)
                return self.compressed_wrapper(compressible.datatype, compressible.data)
            compressible.compress = compress_clipboard
        def clipboard_progress(local_requests, remote_requests):
            clipboardlog("clipboard_progress(%s, %s)", local_requests, remote_requests)
            if local_requests is not None:
                self.local_clipboard_requests = local_requests
            if remote_requests is not None:
                self.remote_clipboard_requests = remote_requests
            n = self.local_clipboard_requests+self.remote_clipboard_requests
            self.clipboard_notify(n)
        def register_clipboard_toggled(*_args):
            def clipboard_toggled(*_args):
                #reset tray icon:
                self.local_clipboard_requests = 0
                self.remote_clipboard_requests = 0
                self.clipboard_notify(0)
            self.connect("clipboard-toggled", clipboard_toggled)
            def loop_disabled_notify():
                ch = self.clipboard_helper
                if ch and ch.disabled_by_loop and self.notifier:
                    icon = None
                    try:
                        from xpra.notifications.common import parse_image_path
                        icon = parse_image_path(get_icon_filename("clipboard"))
                    except ImportError:
                        pass
                    summary = "Clipboard Synchronization Error"
                    body = "A synchronization loop has been detected,\n" + \
                            "to prevent further issues clipboard synchronization has been disabled."
                    self.notifier.show_notify("", self.tray, 0, "Xpra", 0, "", summary, body, [], {}, 10*10000, icon)
                return False
            self.timeout_add(5*1000, loop_disabled_notify)
        self.after_handshake(register_clipboard_toggled)
        return helperClass(clipboard_send, clipboard_progress, **kwargs)

    def cancel_clipboard_notification_timer(self):
        cnt = self.clipboard_notification_timer
        if cnt:
            self.clipboard_notification_timer = None
            self.source_remove(cnt)

    def clipboard_notify(self, n):
        tray = self.tray
        if not tray or not CLIPBOARD_NOTIFY:
            return
        clipboardlog("clipboard_notify(%s) notification timer=%s", n, self.clipboard_notification_timer)
        self.cancel_clipboard_notification_timer()
        if n>0 and self.clipboard_enabled:
            self.last_clipboard_notification = monotonic_time()
            tray.set_icon("clipboard")
            tray.set_tooltip("%s clipboard requests in progress" % n)
            tray.set_blinking(True)
        else:
            #no more pending clipboard transfers,
            #reset the tray icon,
            #but wait at least N seconds after the last clipboard transfer:
            N = 1
            delay = int(max(0, 1000*(self.last_clipboard_notification+N-monotonic_time())))
            def reset_tray_icon():
                self.clipboard_notification_timer = None
                tray = self.tray
                if not tray:
                    return
                tray.set_icon(None)    #None means back to default icon
                tray.set_tooltip(self.get_tray_title())
                tray.set_blinking(False)
            self.clipboard_notification_timer = self.timeout_add(delay, reset_tray_icon)
