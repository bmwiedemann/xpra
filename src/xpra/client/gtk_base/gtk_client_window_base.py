# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2019 Antoine Martin <antoine@xpra.org>
# Copyright (C) 2008, 2010 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import math
import os.path
from urllib.parse import unquote
import cairo
from gi.repository import Gtk, Gdk, Gio

from xpra.os_util import bytestostr, strtobytes, is_X11, monotonic_time, WIN32, OSX, POSIX
from xpra.util import (
    AdHocStruct, typedict, envint, envbool, nonl, csv, first_time,
    WORKSPACE_UNSET, WORKSPACE_ALL, WORKSPACE_NAMES, MOVERESIZE_DIRECTION_STRING, SOURCE_INDICATION_STRING,
    MOVERESIZE_CANCEL,
    MOVERESIZE_SIZE_TOPLEFT, MOVERESIZE_SIZE_TOP, MOVERESIZE_SIZE_TOPRIGHT,
    MOVERESIZE_SIZE_RIGHT,
    MOVERESIZE_SIZE_BOTTOMRIGHT,  MOVERESIZE_SIZE_BOTTOM, MOVERESIZE_SIZE_BOTTOMLEFT,
    MOVERESIZE_SIZE_LEFT, MOVERESIZE_MOVE,
    )
from xpra.gtk_common.gobject_util import no_arg_signal, one_arg_signal
from xpra.gtk_common.gtk_util import (
    get_pixbuf_from_data, get_default_root_window,
    enable_alpha,
    BUTTON_MASK,
    GRAB_STATUS_STRING,
    WINDOW_EVENT_MASK,
    )
from xpra.gtk_common.keymap import KEY_TRANSLATIONS
from xpra.client.client_window_base import ClientWindowBase
from xpra.platform.gui import set_fullscreen_monitors, set_shaded
from xpra.platform.gui import add_window_hooks, remove_window_hooks
from xpra.log import Logger

focuslog = Logger("focus", "grab")
workspacelog = Logger("workspace")
log = Logger("window")
keylog = Logger("keyboard")
iconlog = Logger("icon")
metalog = Logger("metadata")
statelog = Logger("state")
eventslog = Logger("events")
shapelog = Logger("shape")
mouselog = Logger("mouse")
geomlog = Logger("geometry")
grablog = Logger("grab")
draglog = Logger("dragndrop")
alphalog = Logger("alpha")

CAN_SET_WORKSPACE = False
HAS_X11_BINDINGS = False
USE_X11_BINDINGS = POSIX and envbool("XPRA_USE_X11_BINDINGS", is_X11())
prop_get, prop_set, prop_del = None, None, None
NotifyInferior = None
if USE_X11_BINDINGS:
    try:
        from xpra.gtk_common.error import xlog, verify_sync
        from xpra.x11.gtk_x11.prop import prop_get, prop_set, prop_del
        from xpra.x11.bindings.window_bindings import constants, X11WindowBindings, SHAPE_KIND  #@UnresolvedImport
        from xpra.x11.bindings.core_bindings import X11CoreBindings, set_context_check
        from xpra.x11.gtk_x11.send_wm import send_wm_workspace

        set_context_check(verify_sync)
        X11Window = X11WindowBindings()
        X11Core = X11CoreBindings()
        NotifyInferior = constants["NotifyInferior"]
        HAS_X11_BINDINGS = True

        SubstructureNotifyMask = constants["SubstructureNotifyMask"]
        SubstructureRedirectMask = constants["SubstructureRedirectMask"]

        def can_set_workspace():
            SET_WORKSPACE = envbool("XPRA_SET_WORKSPACE", True)
            if not SET_WORKSPACE:
                return False
            try:
                #TODO: in theory this is not a proper check, meh - that will do
                root = get_default_root_window()
                supported = prop_get(root, "_NET_SUPPORTED", ["atom"], ignore_errors=True)
                return bool(supported) and "_NET_WM_DESKTOP" in supported
            except Exception as e:
                workspacelog("x11 workspace bindings error", exc_info=True)
                workspacelog.error("Error: failed to setup workspace hooks:")
                workspacelog.error(" %s", e)
        CAN_SET_WORKSPACE = can_set_workspace()
    except ImportError as e:
        log("x11 bindings", exc_info=True)
        log.error("Error: cannot import X11 bindings:")
        log.error(" %s", e)


BREAK_MOVERESIZE = os.environ.get("XPRA_BREAK_MOVERESIZE", "Escape").split(",")
MOVERESIZE_X11 = envbool("XPRA_MOVERESIZE_X11", POSIX)
CURSOR_IDLE_TIMEOUT = envint("XPRA_CURSOR_IDLE_TIMEOUT", 6)
DISPLAY_HAS_SCREEN_INDEX = POSIX and os.environ.get("DISPLAY", "").split(":")[-1].find(".")>=0
DRAGNDROP = envbool("XPRA_DRAGNDROP", True)
CLAMP_WINDOW_TO_SCREEN = envbool("XPRA_CLAMP_WINDOW_TO_SCREEN", True)
FOCUS_RECHECK_DELAY = envint("XPRA_FOCUS_RECHECK_DELAY", 0)

WINDOW_OVERFLOW_TOP = envbool("XPRA_WINDOW_OVERFLOW_TOP", False)
AWT_RECENTER = envbool("XPRA_AWT_RECENTER", True)
SAVE_WINDOW_ICONS = envbool("XPRA_SAVE_WINDOW_ICONS", False)
UNDECORATED_TRANSIENT_IS_OR = envint("XPRA_UNDECORATED_TRANSIENT_IS_OR", 1)
XSHAPE = envbool("XPRA_XSHAPE", True)
LAZY_SHAPE = envbool("XPRA_LAZY_SHAPE", True)
def parse_padding_colors(colors_str):
    padding_colors = 0, 0, 0
    if colors_str:
        try:
            padding_colors = tuple(float(x.strip()) for x in colors_str.split(","))
            assert len(padding_colors)==3, "you must specify 3 components"
        except Exception as e:
            log.warn("Warning: invalid padding colors specified,")
            log.warn(" %s", e)
            log.warn(" using black")
            padding_colors = 0, 0, 0
    log("parse_padding_colors(%s)=%s", colors_str, padding_colors)
    return padding_colors
PADDING_COLORS = parse_padding_colors(os.environ.get("XPRA_PADDING_COLORS"))

#window types we map to POPUP rather than TOPLEVEL
POPUP_TYPE_HINTS = set((
                    #"DIALOG",
                    #"MENU",
                    #"TOOLBAR",
                    #"SPLASH",
                    #"UTILITY",
                    #"DOCK",
                    #"DESKTOP",
                    "DROPDOWN_MENU",
                    "POPUP_MENU",
                    #"TOOLTIP",
                    #"NOTIFICATION",
                    #"COMBO",
                    #"DND"
                    ))
#window types for which we skip window decorations (title bar)
UNDECORATED_TYPE_HINTS = set((
                    #"DIALOG",
                    "MENU",
                    #"TOOLBAR",
                    "SPLASH",
                    "SPLASHSCREEN",
                    "UTILITY",
                    "DOCK",
                    "DESKTOP",
                    "DROPDOWN_MENU",
                    "POPUP_MENU",
                    "TOOLTIP",
                    "NOTIFICATION",
                    "COMBO",
                    "DND"))

GDK_SCROLL_MAP = {
    Gdk.ScrollDirection.UP       : 4,
    Gdk.ScrollDirection.DOWN     : 5,
    Gdk.ScrollDirection.LEFT     : 6,
    Gdk.ScrollDirection.RIGHT    : 7,
    }


def wn(w):
    return WORKSPACE_NAMES.get(w, w)


class GTKKeyEvent(AdHocStruct):
    pass


class GTKClientWindowBase(ClientWindowBase, Gtk.Window):

    __common_gsignals__ = {
        "state-updated"         : no_arg_signal,
        "xpra-focus-out-event"  : one_arg_signal,
        "xpra-focus-in-event"   : one_arg_signal,
        }

    #maximum size of the actual window:
    MAX_VIEWPORT_DIMS = 16*1024, 16*1024
    #maximum size of the backing pixel buffer:
    MAX_BACKING_DIMS = 16*1024, 16*1024

    def init_window(self, metadata):
        self.init_max_window_size()
        if self._is_popup(metadata):
            window_type = Gtk.WindowType.POPUP
        else:
            window_type = Gtk.WindowType.TOPLEVEL
        self.on_realize_cb = {}
        Gtk.Window.__init__(self, type = window_type)
        self.init_drawing_area()
        self.set_decorated(self._is_decorated(metadata))
        self.set_app_paintable(True)
        self._window_state = {}
        self._resize_counter = 0
        self._can_set_workspace = HAS_X11_BINDINGS and CAN_SET_WORKSPACE
        self._current_frame_extents = None
        self._screen = -1
        self._frozen = False
        self.window_state_timer = None
        self.send_iconify_timer = None
        self.remove_pointer_overlay_timer = None
        self.show_pointer_overlay_timer = None
        self.moveresize_timer = None
        self.moveresize_event = None
        #add platform hooks
        self.connect_after("realize", self.on_realize)
        self.connect('unrealize', self.on_unrealize)
        self.add_events(WINDOW_EVENT_MASK)
        if DRAGNDROP and not self._client.readonly:
            self.init_dragndrop()
        self.init_focus()
        ClientWindowBase.init_window(self, metadata)

    def init_drawing_area(self):
        widget = Gtk.DrawingArea()
        widget.show()
        self.drawing_area = widget
        self.init_widget_events(widget)
        self.add(widget)

    def init_widget_events(self, widget):
        widget.add_events(WINDOW_EVENT_MASK)
        def motion(_w, event):
            self._do_motion_notify_event(event)
            return True
        widget.connect("motion-notify-event", motion)
        def press(_w, event):
            self._do_button_press_event(event)
            return True
        widget.connect("button-press-event", press)
        def release(_w, event):
            self._do_button_release_event(event)
            return True
        widget.connect("button-release-event", release)
        def scroll(_w, event):
            self._do_scroll_event(event)
            return True
        widget.connect("scroll-event", scroll)


    ######################################################################
    # drag and drop:
    def init_dragndrop(self):
        targets = [
            Gtk.TargetEntry.new("text/uri-list", 0, 80),
            ]
        flags = Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT
        actions = Gdk.DragAction.COPY   # | Gdk.ACTION_LINK
        self.drag_dest_set(flags, targets, actions)
        self.connect('drag_drop', self.drag_drop_cb)
        self.connect('drag_motion', self.drag_motion_cb)
        self.connect('drag_data_received', self.drag_got_data_cb)

    def drag_drop_cb(self, widget, context, x, y, time):
        targets = list(x.name() for x in context.list_targets())
        draglog("drag_drop_cb%s targets=%s", (widget, context, x, y, time), targets)
        if not targets:
            #this happens on macos, but we can still get the data..
            draglog("Warning: no targets provided, continuing anyway")
        elif "text/uri-list" not in targets:
            draglog("Warning: cannot handle targets:")
            draglog(" %s", csv(targets))
            return
        atom = Gdk.Atom.intern("text/uri-list", False)
        widget.drag_get_data(context, atom, time)

    def drag_motion_cb(self, wid, context, x, y, time):
        draglog("drag_motion_cb%s", (wid, context, x, y, time))
        Gdk.drag_status(context, Gdk.DragAction.COPY, time)
        return True #accept this data

    def drag_got_data_cb(self, wid, context, x, y, selection, info, time):
        draglog("drag_got_data_cb%s", (wid, context, x, y, selection, info, time))
        #draglog("%s: %s", type(selection), dir(selection))
        #draglog("%s: %s", type(context), dir(context))
        targets = list(x.name() for x in context.list_targets())
        actions = context.get_actions()
        def xid(w):
            #TODO: use a generic window handle function
            #this only used for debugging for now
            if w and POSIX:
                return w.get_xid()
            return 0
        dest_window = xid(context.get_dest_window())
        source_window = xid(context.get_source_window())
        suggested_action = context.get_suggested_action()
        draglog("drag_got_data_cb context: source_window=%#x, dest_window=%#x",
                source_window, dest_window)
        draglog("drag_got_data_cb context: suggested_action=%s, actions=%s, targets=%s",
                suggested_action, actions, targets)
        dtype = selection.get_data_type()
        fmt = selection.get_format()
        l = selection.get_length()
        target = selection.get_target()
        text = selection.get_text()
        uris = selection.get_uris()
        draglog("drag_got_data_cb selection: data type=%s, format=%s, length=%s, target=%s, text=%s, uris=%s",
                dtype, fmt, l, target, text, uris)
        if not uris:
            return
        filelist = []
        for uri in uris:
            if not uri:
                continue
            if not uri.startswith("file://"):
                draglog.warn("Warning: cannot handle drag-n-drop URI '%s'", uri)
                continue
            filename = unquote(uri[len("file://"):].rstrip("\n\r"))
            if WIN32:
                filename = filename.lstrip("/")
            abspath = os.path.abspath(filename)
            if not os.path.isfile(abspath):
                draglog.warn("Warning: '%s' is not a file", abspath)
                continue
            filelist.append(abspath)
        draglog("drag_got_data_cb: will try to upload: %s", csv(filelist))
        pending = set(filelist)
        #when all the files have been loaded / failed,
        #finish the drag and drop context so the source knows we're done with them:
        def file_done(filename):
            if not pending:
                return
            try:
                pending.remove(filename)
            except KeyError:
                pass
            if not pending:
                context.finish(True, False, time)
        for filename in filelist:
            def got_file_info(gfile, result, arg=None):
                draglog("got_file_info(%s, %s, %s)", gfile, result, arg)
                file_info = gfile.query_info_finish(result)
                basename = gfile.get_basename()
                ctype = file_info.get_content_type()
                size = file_info.get_size()
                draglog("file_info(%s)=%s ctype=%s, size=%s", filename, file_info, ctype, size)
                def got_file_data(gfile, result, user_data=None):
                    _, data, entity = gfile.load_contents_finish(result)
                    filesize = len(data)
                    draglog("got_file_data(%s, %s, %s) entity=%s", gfile, result, user_data, entity)
                    file_done(filename)
                    openit = self._client.remote_open_files
                    draglog.info("sending file %s (%i bytes)", basename, filesize)
                    self._client.send_file(filename, "", data, filesize=filesize, openit=openit)
                cancellable = None
                user_data = (filename, True)
                gfile.load_contents_async(cancellable, got_file_data, user_data)
            try:
                gfile = Gio.File.new_for_path(filename)
                #basename = gf.get_basename()
                FILE_QUERY_INFO_NONE = 0
                G_PRIORITY_DEFAULT = 0
                cancellable = None
                gfile.query_info_async("standard::*", FILE_QUERY_INFO_NONE, G_PRIORITY_DEFAULT, cancellable, got_file_info, None)
            except Exception as e:
                draglog("file upload for %s:", filename, exc_info=True)
                draglog.error("Error: cannot upload '%s':", filename)
                draglog.error(" %s", e)
                del e
                file_done(filename)

    ######################################################################
    # focus:
    def init_focus(self):
        self.recheck_focus_timer = 0
        self.when_realized("init-focus", self.do_init_focus)

    def do_init_focus(self):
        #hook up the X11 gdk event notifications so we can get focus-out when grabs are active:
        if POSIX and not OSX:
            try:
                from xpra.x11.gtk_x11.gdk_bindings import add_event_receiver
            except ImportError as e:
                log("do_init_focus()", exc_info=True)
                log.warn("Warning: missing gdk bindings:")
                log.warn(" %s", e)
            else:
                self._focus_latest = None
                grablog("adding event receiver so we can get FocusIn and FocusOut events whilst grabbing the keyboard")
                add_event_receiver(self.get_window(), self)
        #other platforms should bet getting regular focus events instead:
        def focus_in(_window, event):
            focuslog("focus-in-event for wid=%s", self._id)
            self.do_xpra_focus_in_event(event)
        def focus_out(_window, event):
            focuslog("focus-out-event for wid=%s", self._id)
            self.do_xpra_focus_out_event(event)
        self.connect("focus-in-event", focus_in)
        self.connect("focus-out-event", focus_out)
        if not self._override_redirect:
            self.connect("notify::has-toplevel-focus", self._focus_change)

    def _focus_change(self, *args):
        assert not self._override_redirect
        htf = self.has_toplevel_focus()
        focuslog("%s focus_change%s has-toplevel-focus=%s, _been_mapped=%s", self, args, htf, self._been_mapped)
        if self._been_mapped:
            self._client.update_focus(self._id, htf)

    def recheck_focus(self):
        self.recheck_focus_timer = 0
        #we receive pairs of FocusOut + FocusIn following a keyboard grab,
        #so we recheck the focus status via this timer to skip unnecessary churn
        focused = self._client._focused
        focuslog("recheck_focus() wid=%i, focused=%s, latest=%s", self._id, focused, self._focus_latest)
        hasfocus = focused==self._id
        if hasfocus==self._focus_latest:
            #we're already up to date
            return
        if not self._focus_latest:
            self._client.window_ungrab()
            self._client.update_focus(self._id, False)
        else:
            self._client.update_focus(self._id, True)

    def cancel_focus_timer(self):
        rft = self.recheck_focus_timer
        if rft:
            self.recheck_focus_timer = 0
            self.source_remove(rft)

    def schedule_recheck_focus(self):
        if FOCUS_RECHECK_DELAY<0:
            self.recheck_focus()
            return
        if self.recheck_focus_timer==0:
            self.recheck_focus_timer = self.timeout_add(FOCUS_RECHECK_DELAY, self.recheck_focus)
        return True

    def do_xpra_focus_out_event(self, event):
        focuslog("do_xpra_focus_out_event(%s)", event)
        if NotifyInferior is not None:
            detail = getattr(event, "detail", None)
            if detail==NotifyInferior:
                focuslog("dropped NotifyInferior focus event")
                return True
        self._focus_latest = False
        return self.schedule_recheck_focus()

    def do_xpra_focus_in_event(self, event):
        focuslog("do_xpra_focus_in_event(%s) been_mapped=%s", event, self._been_mapped)
        if self._been_mapped:
            self._focus_latest = True
            return self.schedule_recheck_focus()


    def init_max_window_size(self):
        """ used by GL windows to enforce a hard limit on window sizes """
        saved_mws = self.max_window_size
        def clamp_to(maxw, maxh):
            #don't bother if the new limit is greater than 16k:
            if maxw>=16*1024 and maxh>=16*1024:
                return
            #only take into account the current max-window-size if non zero:
            mww, mwh = self.max_window_size
            if mww>0:
                maxw = min(mww, maxw)
            if mwh>0:
                maxh = min(mwh, maxh)
            self.max_window_size = maxw, maxh
        #viewport is easy, measured in window pixels:
        clamp_to(*self.MAX_VIEWPORT_DIMS)
        #backing dimensions are harder,
        #we have to take scaling into account (if any):
        clamp_to(*self._client.sp(*self.MAX_BACKING_DIMS))
        if self.max_window_size!=saved_mws:
            log("init_max_window_size(..) max-window-size changed from %s to %s",
                saved_mws, self.max_window_size)
            log(" because of max viewport dims %s and max backing dims %s",
                self.MAX_VIEWPORT_DIMS, self.MAX_BACKING_DIMS)


    def is_awt(self, metadata) -> bool:
        wm_class = metadata.get("class-instance")
        return wm_class and len(wm_class)==2 and wm_class[0].startswith("sun-awt-X11")

    def _is_popup(self, metadata) -> bool:
        #decide if the window type is POPUP or NORMAL
        if self._override_redirect:
            return True
        if UNDECORATED_TRANSIENT_IS_OR>0:
            transient_for = metadata.get("transient-for", -1)
            decorations = metadata.get("decorations", 0)
            if transient_for>0 and decorations<=0:
                if UNDECORATED_TRANSIENT_IS_OR>1:
                    metalog("forcing POPUP type for window transient-for=%s", transient_for)
                    return True
                if metadata.get("skip-taskbar") and self.is_awt(metadata):
                    metalog("forcing POPUP type for Java AWT skip-taskbar window, transient-for=%s", transient_for)
                    return True
        window_types = metadata.strtupleget("window-type")
        popup_types = tuple(POPUP_TYPE_HINTS.intersection(window_types))
        metalog("popup_types(%s)=%s", window_types, popup_types)
        if popup_types:
            metalog("forcing POPUP window type for %s", popup_types)
            return True
        return False

    def _is_decorated(self, metadata) -> bool:
        #decide if the window type is POPUP or NORMAL
        #(show window decorations or not)
        if self._override_redirect:
            return False
        return metadata.boolget("decorations", True)

    def set_decorated(self, decorated : bool):
        was_decorated = self.get_decorated()
        if self._fullscreen and was_decorated and not decorated:
            #fullscreen windows aren't decorated anyway!
            #calling set_decorated(False) would cause it to get unmapped! (why?)
            pass
        else:
            Gtk.Window.set_decorated(self, decorated)
        if WIN32:
            #workaround for new window offsets:
            #keep the window contents where they were and adjust the frame
            #this generates a configure event which ensures the server has the correct window position
            wfs = self._client.get_window_frame_sizes()
            if wfs and decorated and not was_decorated:
                geomlog("set_decorated(%s) re-adjusting window location using %s", decorated, wfs)
                normal = wfs.get("normal")
                fixed = wfs.get("fixed")
                if normal and fixed:
                    nx, ny = normal
                    fx, fy = fixed
                    x, y = self.get_position()
                    Gtk.Window.move(self, max(0, x-nx+fx), max(0, y-ny+fy))


    def setup_window(self, *args):
        log("setup_window%s", args)
        self.set_alpha()

        if self._override_redirect:
            transient_for = self.get_transient_for()
            type_hint = self.get_type_hint()
            if transient_for is not None and type_hint in self.OR_TYPE_HINTS:
                transient_for._override_redirect_windows.append(self)

        self.connect("property-notify-event", self.property_changed)
        self.connect("window-state-event", self.window_state_updated)

        #this will create the backing:
        ClientWindowBase.setup_window(self, *args)

        #try to honour the initial position
        geomlog("setup_window() position=%s, set_initial_position=%s, OR=%s, decorated=%s",
                self._pos, self._set_initial_position, self.is_OR(), self.get_decorated())
        if self._pos!=(0, 0) or self._set_initial_position or self.is_OR():
            x, y = self.adjusted_position(*self._pos)
            if self.is_OR():
                #make sure OR windows are mapped on screen
                if self._client._current_screen_sizes:
                    w, h = self._size
                    self.window_offset = self.calculate_window_offset(x, y, w, h)
                    geomlog("OR offsets=%s", self.window_offset)
                    if self.window_offset:
                        x += self.window_offset[0]
                        y += self.window_offset[1]
            if not self.is_OR() and self.get_decorated():
                #try to adjust for window frame size if we can figure it out:
                #Note: we cannot just call self.get_window_frame_size() here because
                #the window is not realized yet, and it may take a while for the window manager
                #to set the frame-extents property anyway
                wfs = self._client.get_window_frame_sizes()
                dx, dy = 0, 0
                if wfs:
                    geomlog("setup_window() window frame sizes=%s", wfs)
                    v = wfs.get("offset")
                    if v:
                        dx, dy = v
                        x = max(0, x-dx)
                        y = max(0, y-dy)
                        self._pos = x, y
                        geomlog("setup_window() adjusted initial position=%s", self._pos)
            self.move(x, y)
        self.set_default_size(*self._size)

    def new_backing(self, bw, bh):
        b = ClientWindowBase.new_backing(self, bw, bh)
        #call via idle_add so that the backing has time to be realized too:
        self.when_realized("cursor", self.idle_add, self._backing.set_cursor_data, self.cursor_data)
        return b

    def set_cursor_data(self, cursor_data):
        self.cursor_data = cursor_data
        b = self._backing
        if b:
            self.when_realized("cursor", b.set_cursor_data, cursor_data)

    def adjusted_position(self, ox, oy):
        if AWT_RECENTER and self.is_awt(self._metadata):
            ss = self._client._current_screen_sizes
            if ss and len(ss)==1:
                screen0 = ss[0]
                monitors = screen0[5]
                if monitors and len(monitors)>1:
                    monitor = monitors[0]
                    mw = monitor[3]
                    mh = monitor[4]
                    w, h = self._size
                    #adjust for window centering on monitor instead of screen java
                    screen = self.get_screen()
                    sw = screen.get_width()
                    sh = screen.get_height()
                    #re-center on first monitor if the window is within
                    #$tolerance of the center of the screen:
                    tolerance = 10
                    #center of the window:
                    cx = ox + w//2
                    cy = oy + h//2
                    if abs(sw//2 - cx) <= tolerance:
                        x = mw//2 - w//2
                    else:
                        x = ox
                    if abs(sh//2 - cy) <= tolerance:
                        y = mh//2 - h//2
                    else:
                        y = oy
                    geomlog("adjusted_position(%i, %i)=%i, %i", ox, oy, x, y)
                    return x, y
        return ox, oy


    def calculate_window_offset(self, wx, wy, ww, wh):
        ss = self._client._current_screen_sizes
        if not ss:
            return None
        if len(ss)!=1:
            geomlog("cannot handle more than one screen for OR offset")
            return None
        screen0 = ss[0]
        monitors = screen0[5]
        if not monitors:
            geomlog("screen %s lacks monitors information: %s", screen0)
            return None
        from xpra.rectangle import rectangle #@UnresolvedImport
        wrect = rectangle(wx, wy, ww, wh)
        rects = [wrect]
        pixels_in_monitor = {}
        for i, monitor in enumerate(monitors):
            plug_name, x, y, w, h = monitor[:5]
            new_rects = []
            for rect in rects:
                new_rects += rect.substract(x, y, w, h)
            geomlog("after removing areas visible on %s from %s: %s", plug_name, rects, new_rects)
            rects = new_rects
            if not rects:
                #the whole window is visible
                return None
            #keep track of how many pixels would be on this monitor:
            inter = wrect.intersection(x, y, w, h)
            if inter:
                pixels_in_monitor[inter.width*inter.height] = i
        #if we're here, then some of the window would land on an area
        #not show on any monitors
        #choose the monitor that had most of the pixels and make it fit:
        geomlog("pixels in monitor=%s", pixels_in_monitor)
        if not pixels_in_monitor:
            i = 0
        else:
            best = max(pixels_in_monitor.keys())
            i = pixels_in_monitor[best]
        monitor = monitors[i]
        plug_name, x, y, w, h = monitor[:5]
        geomlog("calculating OR offset for monitor %i: %s", i, plug_name)
        if ww>w or wh>=h:
            geomlog("window %ix%i is bigger than the monitor %i: %s %ix%i, not adjusting it",
                    ww, wh, i, plug_name, w, h)
            return None
        dx = 0
        dy = 0
        if wx<x:
            dx = x-wx
        elif wx+ww>x+w:
            dx = (x+w) - (wx+ww)
        if wy<y:
            dy = y-wy
        elif wy+wh>y+h:
            dy = (y+h) - (wy+wh)
        assert dx!=0 or dy!=0
        geomlog("calculate_window_offset%s=%s", (wx, wy, ww, wh), (dx, dy))
        return dx, dy

    def when_realized(self, identifier, callback, *args):
        if self.get_realized():
            callback(*args)
        else:
            self.on_realize_cb[identifier] = callback, args

    def on_realize(self, widget):
        eventslog("on_realize(%s) gdk window=%s", widget, self.get_window())
        add_window_hooks(self)
        cb = self.on_realize_cb
        self.on_realize_cb = {}
        for x, args in cb.values():
            try:
                x(*args)
            except Exception:
                log.error("Error on realize callback %s for window %i", x, self._id, exc_info=True)
        if HAS_X11_BINDINGS:
            #request frame extents if the window manager supports it
            self._client.request_frame_extents(self)
            if self.watcher_pid:
                log("using watcher pid=%i for wid=%i", self.watcher_pid, self._id)
                prop_set(self.get_window(), "_NET_WM_PID", "u32", self.watcher_pid)
        if self.group_leader:
            self.get_window().set_group(self.group_leader)

    def on_unrealize(self, widget):
        eventslog("on_unrealize(%s)", widget)
        remove_window_hooks(self)


    def set_alpha(self):
        #try to enable alpha on this window if needed,
        #and if the backing class can support it:
        bc = self.get_backing_class()
        alphalog("set_alpha() has_alpha=%s, %s.HAS_ALPHA=%s, realized=%s",
                self._has_alpha, bc, bc.HAS_ALPHA, self.get_realized())
        #by default, only RGB (no transparency):
        #rgb_formats = tuple(BACKING_CLASS.RGB_MODES)
        self._client_properties["encodings.rgb_formats"] = ["RGB", "RGBX"]
        if not self._has_alpha or not bc.HAS_ALPHA:
            self._client_properties["encoding.transparency"] = False
            return
        if self._has_alpha and not self.get_realized():
            if enable_alpha(self):
                self._client_properties["encodings.rgb_formats"] = ["RGBA", "RGB", "RGBX"]
                self._window_alpha = True
            else:
                alphalog("enable_alpha()=False")
                self._has_alpha = False
                self._client_properties["encoding.transparency"] = False


    def freeze(self):
        #the OpenGL subclasses override this method to also free their GL context
        self._frozen = True
        self.iconify()

    def unfreeze(self):
        if not self._frozen or not self._iconified:
            return
        log("unfreeze() wid=%i, frozen=%s, iconified=%s", self._id, self._frozen, self._iconified)
        if not self._frozen or not self._iconified:
            #has been deiconified already
            return
        self._frozen = False
        self.deiconify()


    def show(self):
        Gtk.Window.show(self)


    def window_state_updated(self, widget, event):
        statelog("%s.window_state_updated(%s, %s) changed_mask=%s, new_window_state=%s",
                 self, widget, repr(event), event.changed_mask, event.new_window_state)
        state_updates = {}
        if event.changed_mask & Gdk.WindowState.FULLSCREEN:
            state_updates["fullscreen"] = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)
        if event.changed_mask & Gdk.WindowState.ABOVE:
            state_updates["above"] = bool(event.new_window_state & Gdk.WindowState.ABOVE)
        if event.changed_mask & Gdk.WindowState.BELOW:
            state_updates["below"] = bool(event.new_window_state & Gdk.WindowState.BELOW)
        if event.changed_mask & Gdk.WindowState.STICKY:
            state_updates["sticky"] = bool(event.new_window_state & Gdk.WindowState.STICKY)
        if event.changed_mask & Gdk.WindowState.ICONIFIED:
            state_updates["iconified"] = bool(event.new_window_state & Gdk.WindowState.ICONIFIED)
        if event.changed_mask & Gdk.WindowState.MAXIMIZED:
            #this may get sent now as part of map_event code below (and it is irrelevant for the unmap case),
            #or when we get the configure event - which should come straight after
            #if we're changing the maximized state
            state_updates["maximized"] = bool(event.new_window_state & Gdk.WindowState.MAXIMIZED)
        if event.changed_mask & Gdk.WindowState.FOCUSED:
            state_updates["focused"] = bool(event.new_window_state & Gdk.WindowState.FOCUSED)
        self.update_window_state(state_updates)

    def update_window_state(self, state_updates):
        if self._client.readonly:
            log("update_window_state(%s) ignored in readonly mode", state_updates)
            return
        if state_updates.get("maximized") is False or state_updates.get("fullscreen") is False:
            #if we unfullscreen or unmaximize, re-calculate offsets if we have any:
            w, h = self._backing.render_size
            ww, wh = self.get_size()
            log("update_window_state(%s) unmax or unfullscreen", state_updates)
            log("window_offset=%s, backing render_size=%s, window size=%s",
                self.window_offset, (w, h), (ww, wh))
            if self._backing.offsets!=(0, 0, 0, 0):
                self.center_backing(w, h)
                self.queue_draw_area(0, 0, ww, wh)
        #decide if this is really an update by comparing with our local state vars:
        #(could just be a notification of a state change we already know about)
        actual_updates = {}
        for state,value in state_updates.items():
            var = "_" + state.replace("-", "_")     #ie: "skip-pager" -> "_skip_pager"
            cur = getattr(self, var)                #ie: self._maximized
            if cur!=value:
                setattr(self, var, value)           #ie: self._maximized = True
                actual_updates[state] = value
                statelog("%s=%s (was %s)", var, value, cur)
        server_updates = dict((k,v) for k,v in actual_updates.items() if k in self._client.server_window_states)
        #iconification is handled a bit differently...
        try:
            iconified = server_updates.pop("iconified")
        except KeyError:
            iconified = None
        else:
            statelog("iconified=%s", iconified)
            #handle iconification as map events:
            if iconified:
                #usually means it is unmapped
                self._unfocus()
                if not self._override_redirect and not self.send_iconify_timer:
                    #tell server, but wait a bit to try to prevent races:
                    self.schedule_send_iconify()
            else:
                self.cancel_send_iconifiy_timer()
                self._frozen = False
                self.process_map_event()
        statelog("window_state_updated(..) state updates: %s, actual updates: %s, server updates: %s",
                 state_updates, actual_updates, server_updates)
        self._window_state.update(server_updates)
        self.emit("state-updated")
        #if we have state updates, send them back to the server using a configure window packet:
        if self._window_state and not self.window_state_timer:
            self.window_state_timer = self.timeout_add(25, self.send_updated_window_state)

    def send_updated_window_state(self):
        self.window_state_timer = None
        if self._window_state and self.get_window():
            self.send_configure_event(True)

    def cancel_window_state_timer(self):
        wst = self.window_state_timer
        if wst:
            self.window_state_timer = None
            self.source_remove(wst)


    def schedule_send_iconify(self):
        #calculate a good delay to prevent races causing minimize/unminimize loops:
        if self._client.readonly:
            return
        delay = 150
        spl = tuple(self._client.server_ping_latency)
        if spl:
            worst = max(x[1] for x in self._client.server_ping_latency)
            delay += int(1000*worst)
            delay = min(1000, delay)
        statelog("telling server about iconification with %sms delay", delay)
        self.send_iconify_timer = self.timeout_add(delay, self.send_iconify)

    def send_iconify(self):
        self.send_iconify_timer = None
        if self._iconified:
            self.send("unmap-window", self._id, True, self._window_state)
            #we have sent the window-state already:
            self._window_state = {}
            self.cancel_window_state_timer()

    def cancel_send_iconifiy_timer(self):
        sit = self.send_iconify_timer
        if sit:
            self.send_iconify_timer = None
            self.source_remove(sit)


    def set_command(self, command):
        if not HAS_X11_BINDINGS:
            return
        v = command
        if not isinstance(command, str):
            try:
                v = v.decode("utf8")
            except UnicodeDecodeError:
                v = bytestostr(command)
        def do_set_command():
            metalog("do_set_command() str(%s)='%s' (type=%s)", command, nonl(v), type(command))
            prop_set(self.get_window(), "WM_COMMAND", "latin1", v)
        self.when_realized("command", do_set_command)


    def set_x11_property(self, prop_name, dtype, dformat, value):
        metalog("set_x11_property%s", (prop_name, dtype, dformat, value))
        dtype = bytestostr(dtype)
        if dtype=="latin1":
            value = bytestostr(value)
        if isinstance(value, (list, tuple)):
            dtype = (dtype, )
        def do_set_prop():
            gdk_window = self.get_window()
            if not dtype and not dformat:
                #remove prop
                prop_del(gdk_window, prop_name)
            else:
                prop_set(gdk_window, prop_name, dtype, value)
        self.when_realized("x11-prop-%s" % prop_name, do_set_prop)

    def set_class_instance(self, wmclass_name, wmclass_class):
        if not self.get_realized():
            #Warning: window managers may ignore the icons we try to set
            #if the wm_class value is set and matches something somewhere undocumented
            #(if the default is used, you cannot override the window icon)
            self.set_wmclass(wmclass_name, wmclass_class)
        elif HAS_X11_BINDINGS:
            xid = self.get_window().get_xid()
            with xlog:
                X11Window.setClassHint(xid, strtobytes(wmclass_class), strtobytes(wmclass_name))
                log("XSetClassHint(%s, %s) done", wmclass_class, wmclass_name)

    def set_shape(self, shape):
        shapelog("set_shape(%s)", shape)
        if not HAS_X11_BINDINGS or not XSHAPE:
            return
        def do_set_shape():
            xid = self.get_window().get_xid()
            x_off, y_off = shape.get("x", 0), shape.get("y", 0)
            for kind, name in SHAPE_KIND.items():       #@UndefinedVariable
                rectangles = shape.get("%s.rectangles" % name)      #ie: Bounding.rectangles = [(0, 0, 150, 100)]
                if rectangles:
                    #adjust for scaling:
                    if self._client.xscale!=1 or self._client.yscale!=1:
                        x_off, y_off = self._client.sp(x_off, y_off)
                        rectangles = self.scale_shape_rectangles(name, rectangles)
                    #too expensive to log with actual rectangles:
                    shapelog("XShapeCombineRectangles(%#x, %s, %i, %i, %i rects)",
                             xid, name, x_off, y_off, len(rectangles))
                    with xlog:
                        X11Window.XShapeCombineRectangles(xid, kind, x_off, y_off, rectangles)
        self.when_realized("shape", do_set_shape)

    def scale_shape_rectangles(self, kind_name, rectangles):
        if LAZY_SHAPE or len(rectangles)<2:
            #scale the rectangles without a bitmap...
            #results aren't so good! (but better than nothing?)
            srect = self._client.srect
            return [srect(*x) for x in rectangles]
        from PIL import Image, ImageDraw        #@UnresolvedImport
        ww, wh = self._size
        sw, sh = self._client.cp(ww, wh)
        img = Image.new('1', (sw, sh), color=0)
        shapelog("drawing %s on bitmap(%s,%s)=%s", kind_name, sw, sh, img)
        d = ImageDraw.Draw(img)
        for x,y,w,h in rectangles:
            d.rectangle([x, y, x+w, y+h], fill=1)
        img = img.resize((ww, wh))
        shapelog("resized %s bitmap to window size %sx%s: %s", kind_name, ww, wh, img)
        #now convert back to rectangles...
        rectangles = []
        for y in range(wh):
            #for debugging, this is very useful, but costly!
            #shapelog("pixels[%3i]=%s", y, "".join([str(img.getpixel((x, y))) for x in range(ww)]))
            x = 0
            start = None
            while x<ww:
                #find first white pixel:
                while x<ww and img.getpixel((x, y))==0:
                    x += 1
                start = x
                #find next black pixel:
                while x<ww and img.getpixel((x, y))!=0:
                    x += 1
                end = x
                if start<end:
                    rectangles.append((start, y, end-start, 1))
        return rectangles

    def set_bypass_compositor(self, v):
        if not HAS_X11_BINDINGS:
            return
        if v not in (0, 1, 2):
            v = 0
        def do_set_bypass_compositor():
            prop_set(self.get_window(), "_NET_WM_BYPASS_COMPOSITOR", "u32", v)
        self.when_realized("bypass-compositor", do_set_bypass_compositor)


    def set_strut(self, strut):
        if not HAS_X11_BINDINGS:
            return
        log("strut=%s", strut)
        d = typedict(strut)
        values = []
        for x in ("left", "right", "top", "bottom"):
            v = d.intget(x, 0)
            #handle scaling:
            if x in ("left", "right"):
                v = self._client.sx(v)
            else:
                v = self._client.sy(v)
            values.append(v)
        has_partial = False
        for x in ("left_start_y", "left_end_y",
                  "right_start_y", "right_end_y",
                  "top_start_x", "top_end_x",
                  "bottom_start_x", "bottom_end_x"):
            if x in d:
                has_partial = True
            v = d.intget(x, 0)
            if x.find("_x"):
                v = self._client.sx(v)
            elif x.find("_y"):
                v = self._client.sy(v)
            values.append(v)
        log("setting strut=%s, has partial=%s", values, has_partial)
        def do_set_strut():
            if has_partial:
                prop_set(self.get_window(), "_NET_WM_STRUT_PARTIAL", ["u32"], values)
            prop_set(self.get_window(), "_NET_WM_STRUT", ["u32"], values[:4])
        self.when_realized("strut", do_set_strut)


    def set_modal(self, modal):
        #with gtk2 setting the window as modal would prevent
        #all other windows we manage from receiving input
        #including other unrelated applications
        #what we want is "window-modal"
        #so we can turn this off using the "modal_windows" feature,
        #from the command line and the system tray:
        mw = self._client.modal_windows
        log("set_modal(%s) modal_windows=%s", modal, mw)
        Gtk.Window.set_modal(self, modal and mw)


    def set_fullscreen_monitors(self, fsm):
        #platform specific code:
        log("set_fullscreen_monitors(%s)", fsm)
        def do_set_fullscreen_monitors():
            set_fullscreen_monitors(self.get_window(), fsm)
        self.when_realized("fullscreen-monitors", do_set_fullscreen_monitors)


    def set_shaded(self, shaded):
        #platform specific code:
        log("set_shaded(%s)", shaded)
        def do_set_shaded():
            set_shaded(self.get_window(), shaded)
        self.when_realized("shaded", do_set_shaded)


    def set_fullscreen(self, fullscreen):
        statelog("%s.set_fullscreen(%s)", self, fullscreen)
        def do_set_fullscreen():
            if fullscreen:
                #we may need to temporarily remove the max-window-size restrictions
                #to be able to honour the fullscreen request:
                w, h = self.max_window_size
                if w>0 and h>0:
                    self.set_size_constraints(self.size_constraints, (0, 0))
                self.fullscreen()
            else:
                self.unfullscreen()
                #re-apply size restrictions:
                w, h = self.max_window_size
                if w>0 and h>0:
                    self.set_size_constraints(self.size_constraints, self.max_window_size)
        self.when_realized("fullscreen", do_set_fullscreen)

    def set_xid(self, xid):
        if not HAS_X11_BINDINGS:
            return
        if xid.startswith("0x") and xid.endswith("L"):
            xid = xid[:-1]
        try:
            iid = int(xid, 16)
        except Exception as e:
            log("%s.set_xid(%s) error parsing/setting xid: %s", self, xid, e)
            return
        def do_set_xid():
            self.xset_u32_property(self.get_window(), "XID", iid)
        self.when_realized("xid", do_set_xid)

    def xget_u32_property(self, target, name):
        if prop_get:
            v = prop_get(target, name, "u32", ignore_errors=True)
            log("%s.xget_u32_property(%s, %s)=%s", self, target, name, v)
            if isinstance(v, int):
                return v
        return None

    def xset_u32_property(self, target, name, value):
        prop_set(target, name, "u32", value)


    def property_changed(self, widget, event):
        atom = str(event.atom)
        statelog("property_changed(%s, %s) : %s", widget, event, atom)
        if atom=="_NET_WM_DESKTOP":
            if self._been_mapped and not self._override_redirect and self._can_set_workspace:
                self.do_workspace_changed(event)
        elif atom=="_NET_FRAME_EXTENTS":
            if prop_get:
                v = prop_get(self.get_window(), "_NET_FRAME_EXTENTS", ["u32"], ignore_errors=False)
                statelog("_NET_FRAME_EXTENTS: %s", v)
                if v:
                    if v==self._current_frame_extents:
                        #unchanged
                        return
                    if not self._been_mapped:
                        #map event will take care of sending it
                        return
                    if self.is_OR() or self.is_tray():
                        #we can't do it: the server can't handle configure packets for OR windows!
                        return
                    if not self._client.server_window_frame_extents:
                        #can't send cheap "skip-geometry" packets or frame-extents feature not supported:
                        return
                    #tell server about new value:
                    self._current_frame_extents = v
                    statelog("sending configure event to update _NET_FRAME_EXTENTS to %s", v)
                    self._window_state["frame"] = self._client.crect(*v)
                    self.send_configure_event(True)
        elif atom=="XKLAVIER_STATE":
            if prop_get:
                #unused for now, but log it:
                xklavier_state = prop_get(self.get_window(), "XKLAVIER_STATE", ["integer"], ignore_errors=False)
                keylog("XKLAVIER_STATE=%s", [hex(x) for x in (xklavier_state or [])])
        elif atom=="_NET_WM_STATE":
            if prop_get:
                wm_state_atoms = prop_get(self.get_window(), "_NET_WM_STATE", ["atom"], ignore_errors=False)
                #code mostly duplicated from gtk_x11/window.py:
                WM_STATE_NAME = {
                    "fullscreen"    : ("_NET_WM_STATE_FULLSCREEN", ),
                    "maximized"     : ("_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ"),
                    "shaded"        : ("_NET_WM_STATE_SHADED", ),
                    "sticky"        : ("_NET_WM_STATE_STICKY", ),
                    "skip-pager"    : ("_NET_WM_STATE_SKIP_PAGER", ),
                    "skip-taskbar"  : ("_NET_WM_STATE_SKIP_TASKBAR", ),
                    "above"         : ("_NET_WM_STATE_ABOVE", ),
                    "below"         : ("_NET_WM_STATE_BELOW", ),
                    "focused"       : ("_NET_WM_STATE_FOCUSED", ),
                    }
                state_atoms = set(wm_state_atoms or [])
                state_updates = {}
                for state, atoms in WM_STATE_NAME.items():
                    var = "_" + state.replace("-", "_")           #ie: "skip-pager" -> "_skip_pager"
                    cur_state = getattr(self, var)
                    wm_state_is_set = set(atoms).issubset(state_atoms)
                    if wm_state_is_set and not cur_state:
                        state_updates[state] = True
                    elif cur_state and not wm_state_is_set:
                        state_updates[state] = False
                log("_NET_WM_STATE=%s, state_updates=%s", wm_state_atoms, state_updates)
                if state_updates:
                    self.update_window_state(state_updates)


    ######################################################################
    # workspace
    def workspace_changed(self):
        #on X11 clients, this fires from the root window property watcher
        ClientWindowBase.workspace_changed(self)
        if self._can_set_workspace:
            self.do_workspace_changed("desktop workspace changed")

    def do_workspace_changed(self, info):
        #call this method whenever something workspace related may have changed
        window_workspace = self.get_window_workspace()
        desktop_workspace = self.get_desktop_workspace()
        workspacelog("do_workspace_changed(%s) for window %i (window, desktop): from %s to %s",
                     info, self._id,
                     (wn(self._window_workspace), wn(self._desktop_workspace)),
                     (wn(window_workspace), wn(desktop_workspace)))
        if self._window_workspace==window_workspace and self._desktop_workspace==desktop_workspace:
            #no change
            return
        suspend_resume = None
        if desktop_workspace<0 or window_workspace is None:
            #maybe the property has been cleared? maybe the window is being scrubbed?
            workspacelog("not sure if the window is shown or not: %s vs %s, resuming to be safe",
                         wn(desktop_workspace), wn(window_workspace))
            suspend_resume = False
        elif window_workspace==WORKSPACE_UNSET:
            workspacelog("workspace unset: assume current")
            suspend_resume = False
        elif window_workspace==WORKSPACE_ALL:
            workspacelog("window is on all workspaces")
            suspend_resume = False
        elif desktop_workspace!=window_workspace:
            workspacelog("window is on a different workspace, increasing its batch delay")
            workspacelog(" desktop: %s, window: %s", wn(desktop_workspace), wn(window_workspace))
            suspend_resume = True
        elif self._window_workspace!=self._desktop_workspace:
            assert desktop_workspace==window_workspace
            workspacelog("window was on a different workspace, resetting its batch delay")
            workspacelog(" (was desktop: %s, window: %s, now both on %s)",
                         wn(self._window_workspace), wn(self._desktop_workspace), wn(desktop_workspace))
            suspend_resume = False
        self._window_workspace = window_workspace
        self._desktop_workspace = desktop_workspace
        client_properties = {}
        if window_workspace is not None:
            client_properties["workspace"] = window_workspace
        self.send_control_refresh(suspend_resume, client_properties)

    def send_control_refresh(self, suspend_resume, client_properties={}, refresh=False):
        statelog("send_control_refresh%s", (suspend_resume, client_properties, refresh))
        #we can tell the server using a "buffer-refresh" packet instead
        #and also take care of tweaking the batch config
        options = {"refresh-now" : refresh}            #no need to refresh it
        self._client.control_refresh(self._id, suspend_resume, refresh=refresh, options=options, client_properties=client_properties)

    def get_workspace_count(self):
        if not self._can_set_workspace:
            return None
        root = get_default_root_window()
        return self.xget_u32_property(root, "_NET_NUMBER_OF_DESKTOPS")

    def set_workspace(self, workspace):
        workspacelog("set_workspace(%s)", workspace)
        if not self._can_set_workspace:
            return
        if not self._been_mapped:
            #will be dealt with in the map event handler
            #which will look at the window metadata again
            workspacelog("workspace=%s will be set when the window is mapped", wn(workspace))
            return
        desktop = self.get_desktop_workspace()
        ndesktops = self.get_workspace_count()
        current = self.get_window_workspace()
        workspacelog("set_workspace(%s) realized=%s", wn(workspace), self.get_realized())
        workspacelog(" current workspace=%s, detected=%s, desktop workspace=%s, ndesktops=%s",
                     wn(self._window_workspace), wn(current), wn(desktop), ndesktops)
        if not self._can_set_workspace or ndesktops is None:
            return
        if workspace==desktop or workspace==WORKSPACE_ALL or desktop is None:
            #window is back in view
            self._client.control_refresh(self._id, False, False)
        if (workspace<0 or workspace>=ndesktops) and workspace not in(WORKSPACE_UNSET, WORKSPACE_ALL):
            #this should not happen, workspace is unsigned (CARDINAL)
            #and the server should have the same list of desktops that we have here
            workspacelog.warn("Warning: invalid workspace number: %s", wn(workspace))
            workspace = WORKSPACE_UNSET
        if workspace==WORKSPACE_UNSET:
            #we cannot unset via send_wm_workspace, so we have to choose one:
            workspace = self.get_desktop_workspace()
        if workspace in (None, WORKSPACE_UNSET):
            workspacelog.warn("workspace=%s (doing nothing)", wn(workspace))
            return
        #we will need the gdk window:
        if current==workspace:
            workspacelog("window workspace unchanged: %s", wn(workspace))
            return
        gdkwin = self.get_window()
        workspacelog("do_set_workspace: gdkwindow: %#x, mapped=%s, visible=%s",
                     gdkwin.get_xid(), self.is_mapped(), gdkwin.is_visible())
        root = get_default_root_window()
        with xlog:
            send_wm_workspace(root, gdkwin, workspace)

    def get_desktop_workspace(self):
        window = self.get_window()
        if window:
            root = window.get_screen().get_root_window()
        else:
            #if we are called during init.. we don't have a window
            root = get_default_root_window()
        return self.do_get_workspace(root, "_NET_CURRENT_DESKTOP")

    def get_window_workspace(self):
        return self.do_get_workspace(self.get_window(), "_NET_WM_DESKTOP", WORKSPACE_UNSET)

    def do_get_workspace(self, target, prop, default_value=None):
        if not self._can_set_workspace:
            workspacelog("do_get_workspace: not supported, returning %s", wn(default_value))
            return default_value        #windows and OSX do not have workspaces
        if target is None:
            workspacelog("do_get_workspace: target is None, returning %s", wn(default_value))
            return default_value        #window is not realized yet
        value = self.xget_u32_property(target, prop)
        if value is not None:
            workspacelog("do_get_workspace %s=%s on window %i: %#x",
                         prop, wn(value), self._id, target.get_xid())
            return value
        workspacelog("do_get_workspace %s unset on window %i: %#x, returning default value=%s",
                     prop, self._id, target.get_xid(), wn(default_value))
        return  default_value


    def keyboard_ungrab(self, *args):
        grablog("keyboard_ungrab%s", args)
        self._client.keyboard_grabbed = False
        gdkwin = self.get_window()
        if gdkwin:
            d = gdkwin.get_display()
            if d:
                d.keyboard_ungrab(0)
        return True

    def keyboard_grab(self, *args):
        grablog("keyboard_grab%s", args)
        r = Gdk.keyboard_grab(self.get_window(), True, 0)
        self._client.keyboard_grabbed = r==Gdk.GrabStatus.SUCCESS
        grablog("keyboard_grab%s Gdk.keyboard_grab(%s, True)=%s, keyboard_grabbed=%s",
                args, self.get_window(), GRAB_STATUS_STRING.get(r), self._client.keyboard_grabbed)

    def toggle_keyboard_grab(self):
        grabbed = self._client.keyboard_grabbed
        grablog("toggle_keyboard_grab() grabbed=%s", grabbed)
        if grabbed:
            self.keyboard_ungrab()
        else:
            self.keyboard_grab()

    def pointer_grab(self, *args):
        gdkwin = self.get_window()
        em = Gdk.EventMask
        event_mask = (em.BUTTON_PRESS_MASK |
                      em.BUTTON_RELEASE_MASK |
                      em.POINTER_MOTION_MASK  |
                      em.POINTER_MOTION_HINT_MASK |
                      em.ENTER_NOTIFY_MASK |
                      em.LEAVE_NOTIFY_MASK)
        r = Gdk.pointer_grab(gdkwin, True, event_mask, gdkwin, None, 0)
        self._client.pointer_grabbed = r==Gdk.GrabStatus.SUCCESS
        grablog("pointer_grab%s Gdk.pointer_grab(%s, True)=%s, pointer_grabbed=%s",
                args, self.get_window(), GRAB_STATUS_STRING.get(r), self._client.pointer_grabbed)

    def pointer_ungrab(self, *args):
        grablog("pointer_ungrab%s pointer_grabbed=%s",
                args, self._client.pointer_grabbed)
        self._client.pointer_grabbed = False
        gdkwin = self.get_window()
        if gdkwin:
            d = gdkwin.get_display()
            if d:
                d.pointer_ungrab(0)
        return True

    def toggle_pointer_grab(self):
        pg = self._client.pointer_grabbed
        grablog("toggle_pointer_grab() pointer_grabbed=%s", pg)
        if pg:
            self.pointer_ungrab()
        else:
            self.pointer_grab()


    def toggle_fullscreen(self):
        geomlog("toggle_fullscreen()")
        if self._fullscreen:
            self.unfullscreen()
        else:
            self.fullscreen()


    ######################################################################
    # pointer overlay handling
    def cancel_remove_pointer_overlay_timer(self):
        rpot = self.remove_pointer_overlay_timer
        if rpot:
            self.remove_pointer_overlay_timer = None
            self.source_remove(rpot)

    def cancel_show_pointer_overlay_timer(self):
        rsot = self.show_pointer_overlay_timer
        if rsot:
            self.show_pointer_overlay_timer = None
            self.source_remove(rsot)

    def show_pointer_overlay(self, pos):
        #schedule do_show_pointer_overlay if needed
        b = self._backing
        if not b:
            return
        prev = b.pointer_overlay
        if pos is None:
            if prev is None:
                return
            value = None
        else:
            if prev and prev[:2]==pos[:2]:
                return
            #store both scaled and unscaled value:
            #(the opengl client uses the raw value)
            value = pos[:2]+self._client.sp(*pos[:2])+pos[2:]
        mouselog("show_pointer_overlay(%s) previous value=%s, new value=%s", pos, prev, value)
        b.pointer_overlay = value
        if not self.show_pointer_overlay_timer:
            self.show_pointer_overlay_timer = self.timeout_add(10, self.do_show_pointer_overlay, prev)

    def do_show_pointer_overlay(self, prev):
        #queue a draw event at the previous and current position of the pointer
        #(so the backend will repaint / overlay the cursor image there)
        self.show_pointer_overlay_timer = None
        b = self._backing
        if not b:
            return
        cursor_data = b.cursor_data
        def abs_coords(x, y, size):
            if self.window_offset:
                x += self.window_offset[0]
                y += self.window_offset[1]
            w, h = size, size
            if cursor_data:
                w = cursor_data[3]
                h = cursor_data[4]
                xhot = cursor_data[5]
                yhot = cursor_data[6]
                x = x-xhot
                y = y-yhot
            return x, y, w, h
        value = b.pointer_overlay
        if value:
            #repaint the scale value (in window coordinates):
            x, y, w, h = abs_coords(*value[2:5])
            self.queue_draw_area(x, y, w, h)
            #clear it shortly after:
            self.cancel_remove_pointer_overlay_timer()
            def remove_pointer_overlay():
                self.remove_pointer_overlay_timer = None
                self.show_pointer_overlay(None)
            self.remove_pointer_overlay_timer = self.timeout_add(CURSOR_IDLE_TIMEOUT*1000, remove_pointer_overlay)
        if prev:
            x, y, w, h = abs_coords(*prev[2:5])
            self.queue_draw_area(x, y, w, h)


    def _do_button_press_event(self, event):
        #Gtk.Window.do_button_press_event(self, event)
        self._button_action(event.button, event, True)

    def _do_button_release_event(self, event):
        #Gtk.Window.do_button_release_event(self, event)
        self._button_action(event.button, event, False)

    ######################################################################
    # pointer motion

    def _do_motion_notify_event(self, event):
        #Gtk.Window.do_motion_notify_event(self, event)
        if self.moveresize_event:
            self.motion_moveresize(event)
        ClientWindowBase._do_motion_notify_event(self, event)

    def motion_moveresize(self, event):
        x_root, y_root, direction, button, start_buttons, wx, wy, ww, wh = self.moveresize_event
        dirstr = MOVERESIZE_DIRECTION_STRING.get(direction, direction)
        buttons = self._event_buttons(event)
        if start_buttons is None:
            #first time around, store the buttons
            start_buttons = buttons
            self.moveresize_event[4] = buttons
        if (button>0 and button not in buttons) or (button==0 and start_buttons!=buttons):
            geomlog("%s for window button %i is no longer pressed (buttons=%s) cancelling moveresize",
                    dirstr, button, buttons)
            self.moveresize_event = None
        else:
            x = event.x_root
            y = event.y_root
            dx = x-x_root
            dy = y-y_root
            #clamp resizing using size hints,
            #or sane defaults: minimum of (1x1) and maximum of (2*15x2*25)
            minw = self.geometry_hints.get("min_width", 1)
            minh = self.geometry_hints.get("min_height", 1)
            maxw = self.geometry_hints.get("max_width", 2**15)
            maxh = self.geometry_hints.get("max_height", 2**15)
            geomlog("%s: min=%ix%i, max=%ix%i, window=%ix%i, delta=%ix%i",
                    dirstr, minw, minh, maxw, maxh, ww, wh, dx, dy)
            if direction in (MOVERESIZE_SIZE_BOTTOMRIGHT, MOVERESIZE_SIZE_BOTTOM, MOVERESIZE_SIZE_BOTTOMLEFT):
                #height will be set to: wh+dy
                dy = max(minh-wh, dy)
                dy = min(maxh-wh, dy)
            elif direction in (MOVERESIZE_SIZE_TOPRIGHT, MOVERESIZE_SIZE_TOP, MOVERESIZE_SIZE_TOPLEFT):
                #height will be set to: wh-dy
                dy = min(wh-minh, dy)
                dy = max(wh-maxh, dy)
            if direction in (MOVERESIZE_SIZE_BOTTOMRIGHT, MOVERESIZE_SIZE_RIGHT, MOVERESIZE_SIZE_TOPRIGHT):
                #width will be set to: ww+dx
                dx = max(minw-ww, dx)
                dx = min(maxw-ww, dx)
            elif direction in (MOVERESIZE_SIZE_BOTTOMLEFT, MOVERESIZE_SIZE_LEFT, MOVERESIZE_SIZE_TOPLEFT):
                #width will be set to: ww-dx
                dx = min(ww-minw, dx)
                dx = max(ww-maxw, dx)
            #calculate move + resize:
            if direction==MOVERESIZE_MOVE:
                data = (wx+dx, wy+dy), None
            elif direction==MOVERESIZE_SIZE_BOTTOMRIGHT:
                data = None, (ww+dx, wh+dy)
            elif direction==MOVERESIZE_SIZE_BOTTOM:
                data = None, (ww, wh+dy)
            elif direction==MOVERESIZE_SIZE_BOTTOMLEFT:
                data = (wx+dx, wy), (ww-dx, wh+dy)
            elif direction==MOVERESIZE_SIZE_RIGHT:
                data = None, (ww+dx, wh)
            elif direction==MOVERESIZE_SIZE_LEFT:
                data = (wx+dx, wy), (ww-dx, wh)
            elif direction==MOVERESIZE_SIZE_TOPRIGHT:
                data = (wx, wy+dy), (ww+dx, wh-dy)
            elif direction==MOVERESIZE_SIZE_TOP:
                data = (wx, wy+dy), (ww, wh-dy)
            elif direction==MOVERESIZE_SIZE_TOPLEFT:
                data = (wx+dx, wy+dy), (ww-dx, wh-dy)
            else:
                #not handled yet!
                data = None
            geomlog("%s for window %ix%i: started at %s, now at %s, delta=%s, button=%s, buttons=%s, data=%s",
                    dirstr, ww, wh, (x_root, y_root), (x, y), (dx, dy), button, buttons, data)
            if data:
                #modifying the window is slower than moving the pointer,
                #do it via a timer to batch things together
                self.moveresize_data = data
                if self.moveresize_timer is None:
                    self.moveresize_timer = self.timeout_add(20, self.do_moveresize)

    def do_moveresize(self):
        self.moveresize_timer = None
        mrd = self.moveresize_data
        geomlog("do_moveresize() data=%s", mrd)
        if not mrd:
            return
        move, resize = mrd
        if move:
            x, y = int(move[0]), int(move[1])
        if resize:
            w, h = int(resize[0]), int(resize[1])
            if self._client.readonly:
                #change size-constraints first,
                #so the resize can be honoured:
                sc = self._force_size_constraint(w, h)
                self._metadata.update(sc)
                self.set_metadata(sc)
        if move and resize:
            self.get_window().move_resize(x, y, w, h)
        elif move:
            self.get_window().move(x, y)
        elif resize:
            self.get_window().resize(w, h)


    def initiate_moveresize(self, x_root, y_root, direction, button, source_indication):
        statelog("initiate_moveresize%s",
                 (x_root, y_root, MOVERESIZE_DIRECTION_STRING.get(direction, direction),
                  button, SOURCE_INDICATION_STRING.get(source_indication, source_indication)))
        if MOVERESIZE_X11 and HAS_X11_BINDINGS:
            self.initiate_moveresize_X11(x_root, y_root, direction, button, source_indication)
            return
        if direction==MOVERESIZE_CANCEL:
            self.moveresize_event = None
            self.moveresize_data = None
        else:
            #use window coordinates (which include decorations)
            wx, wy = self.get_window().get_root_origin()
            ww, wh = self.get_size()
            self.moveresize_event = [x_root, y_root, direction, button, None, wx, wy, ww, wh]

    def initiate_moveresize_X11(self, x_root, y_root, direction, button, source_indication):
        statelog("initiate_moveresize_X11%s",
                 (x_root, y_root, MOVERESIZE_DIRECTION_STRING.get(direction, direction),
                  button, SOURCE_INDICATION_STRING.get(source_indication, source_indication)))
        event_mask = SubstructureNotifyMask | SubstructureRedirectMask
        root = self.get_window().get_screen().get_root_window()
        root_xid = root.get_xid()
        xwin = self.get_window().get_xid()
        with xlog:
            X11Core.UngrabPointer()
            X11Window.sendClientMessage(root_xid, xwin, False, event_mask, "_NET_WM_MOVERESIZE",
                  x_root, y_root, direction, button, source_indication)


    def apply_transient_for(self, wid):
        if wid==-1:
            def set_root_transient():
                #root is a gdk window, so we need to ensure we have one
                #backing our gtk window to be able to call set_transient_for on it
                log("%s.apply_transient_for(%s) gdkwindow=%s, mapped=%s",
                    self, wid, self.get_window(), self.is_mapped())
                self.get_window().set_transient_for(get_default_root_window())
            self.when_realized("transient-for-root", set_root_transient)
        else:
            #gtk window is easier:
            window = self._client._id_to_window.get(wid)
            log("%s.apply_transient_for(%s) window=%s", self, wid, window)
            if window:
                self.set_transient_for(window)

    def cairo_paint_border(self, context, clip_area=None):
        log("cairo_paint_border(%s, %s)", context, clip_area)
        b = self.border
        if b is None or not b.shown:
            return
        s = b.size
        ww, wh = self.get_size()
        borders = []
        #window is wide enough, add borders on the side:
        borders.append((0, 0, s, wh))           #left
        borders.append((ww-s, 0, s, wh))        #right
        #window is tall enough, add borders on top and bottom:
        borders.append((0, 0, ww, s))           #top
        borders.append((0, wh-s, ww, s))        #bottom
        for x, y, w, h in borders:
            if w<=0 or h<=0:
                continue
            r = Gdk.Rectangle()
            r.x = x
            r.y = y
            r.width = w
            r.height = h
            rect = r
            if clip_area:
                rect = clip_area.intersect(r)
            if rect.width==0 or rect.height==0:
                continue
            context.save()
            context.rectangle(x, y, w, h)
            context.clip()
            context.set_source_rgba(self.border.red, self.border.green, self.border.blue, self.border.alpha)
            context.fill()
            context.paint()
            context.restore()


    def paint_spinner(self, context, area=None):
        log("%s.paint_spinner(%s, %s)", self, context, area)
        c = self._client
        if not c:
            return
        ww, wh = self.get_size()
        w = c.cx(ww)
        h = c.cy(wh)
        #add grey semi-opaque layer on top:
        context.set_operator(cairo.OPERATOR_OVER)
        context.set_source_rgba(0.2, 0.2, 0.2, 0.4)
        #we can't use the area as rectangle with:
        #context.rectangle(area)
        #because those would be unscaled dimensions
        #it's easier and safer to repaint the whole window:
        context.rectangle(0, 0, w, h)
        context.fill()
        #add spinner:
        dim = min(w/3.0, h/3.0, 100.0)
        context.set_line_width(dim/10.0)
        context.set_line_cap(cairo.LINE_CAP_ROUND)
        context.translate(w/2, h/2)
        from xpra.client.spinner import cv
        count = int(monotonic_time()*4.0)
        for i in range(8):      #8 lines
            context.set_source_rgba(0, 0, 0, cv.trs[count%8][i])
            context.move_to(0.0, -dim/4.0)
            context.line_to(0.0, -dim)
            context.rotate(math.pi/4)
            context.stroke()

    def spinner(self, _ok):
        c = self._client
        if not self.can_have_spinner() or not c:
            return
        #with normal windows, we just queue a draw request
        #and let the expose event paint the spinner
        w, h = self.get_size()
        self.queue_draw_area(0, 0, w, h)


    def do_map_event(self, event):
        log("%s.do_map_event(%s) OR=%s", self, event, self._override_redirect)
        Gtk.Window.do_map_event(self, event)
        if not self._override_redirect:
            #we can get a map event for an iconified window on win32:
            if self._iconified:
                self.deiconify()
            self.process_map_event()

    def process_map_event(self):
        x, y, w, h = self.get_drawing_area_geometry()
        state = self._window_state
        props = self._client_properties
        self._client_properties = {}
        self._window_state = {}
        self.cancel_window_state_timer()
        workspace = self.get_window_workspace()
        if self._been_mapped:
            if workspace is None:
                #not set, so assume it is on the current workspace:
                workspace = self.get_desktop_workspace()
        else:
            self._been_mapped = True
            workspace = self._metadata.intget("workspace", WORKSPACE_UNSET)
            if workspace!=WORKSPACE_UNSET:
                log("map event set workspace %s", wn(workspace))
                self.set_workspace(workspace)
        if self._window_workspace!=workspace and workspace is not None:
            workspacelog("map event: been_mapped=%s, changed workspace from %s to %s",
                         self._been_mapped, wn(self._window_workspace), wn(workspace))
            self._window_workspace = workspace
        if workspace is not None:
            props["workspace"] = workspace
        if self._client.server_window_frame_extents and "frame" not in state:
            wfs = self.get_window_frame_size()
            if wfs and len(wfs)==4:
                state["frame"] = self._client.crect(*wfs)
                self._current_frame_extents = wfs
        geomlog("map-window wid=%s, geometry=%s, client props=%s, state=%s", self._id, (x, y, w, h), props, state)
        cx = self._client.cx
        cy = self._client.cy
        sx, sy, sw, sh = cx(x), cy(y), cx(w), cy(h)
        packet = ["map-window", self._id, sx, sy, sw, sh, props, state]
        self.send(*packet)
        self._pos = (x, y)
        self._size = (w, h)
        if self._backing is None:
            #we may have cleared the backing, so we must re-create one:
            self._set_backing_size(w, h)
        if not self._override_redirect:
            htf = self.has_toplevel_focus()
            focuslog("mapped: has-toplevel-focus=%s", htf)
            if htf:
                self._client.update_focus(self._id, htf)

    def get_window_frame_size(self):
        frame = self._client.get_frame_extents(self)
        if not frame:
            #default to global value we may have:
            wfs = self._client.get_window_frame_sizes()
            if wfs:
                frame = wfs.get("frame")
        return frame


    def send_configure(self):
        self.send_configure_event()

    def do_configure_event(self, event):
        eventslog("%s.do_configure_event(%s) OR=%s, iconified=%s",
                  self, event, self._override_redirect, self._iconified)
        Gtk.Window.do_configure_event(self, event)
        if not self._override_redirect and not self._iconified:
            self.process_configure_event()

    def process_configure_event(self, skip_geometry=False):
        assert skip_geometry or not self.is_OR()
        x, y, w, h = self.get_drawing_area_geometry()
        w = max(1, w)
        h = max(1, h)
        ox, oy = self._pos
        dx, dy = x-ox, y-oy
        self._pos = (x, y)
        self.send_configure_event(skip_geometry)
        if dx!=0 or dy!=0:
            #window has moved, also move any child OR window:
            for window in self._override_redirect_windows:
                x, y = window.get_position()
                window.move(x+dx, y+dy)
        geomlog("configure event: current size=%s, new size=%s, backing=%s, iconified=%s",
                self._size, (w, h), self._backing, self._iconified)
        if (w, h) != self._size or (self._backing is None and not self._iconified):
            self._size = (w, h)
            self._set_backing_size(w, h)
        elif self._backing and not self._iconified:
            geomlog("configure event: size unchanged, queueing redraw")
            self.queue_draw_area(0, 0, w, h)

    def send_configure_event(self, skip_geometry=False):
        assert skip_geometry or not self.is_OR()
        x, y, w, h = self.get_drawing_area_geometry()
        w = max(1, w)
        h = max(1, h)
        state = self._window_state
        props = self._client_properties
        self._client_properties = {}
        self._window_state = {}
        self.cancel_window_state_timer()
        if self._been_mapped:
            #if the window has been mapped already, the workspace should be set:
            workspace = self.get_window_workspace()
            if self._window_workspace!=workspace and workspace is not None:
                workspacelog("send_configure_event: changed workspace from %s to %s",
                             wn(self._window_workspace), wn(workspace))
                self._window_workspace = workspace
                props["workspace"] = workspace
        cx = self._client.cx
        cy = self._client.cy
        sx, sy, sw, sh = cx(x), cy(y), cx(w), cy(h)
        packet = ["configure-window", self._id, sx, sy, sw, sh, props, self._resize_counter, state, skip_geometry]
        pwid = self._id
        if self.is_OR():
            pwid = -1
        packet.append(pwid)
        packet.append(self._client.get_mouse_position())
        packet.append(self._client.get_current_modifiers())
        geomlog("%s", packet)
        self.send(*packet)

    def _set_backing_size(self, ww, wh):
        b = self._backing
        if b:
            b.init(ww, wh, self._client.cx(ww), self._client.cy(wh))
        else:
            self.new_backing(self._client.cx(ww), self._client.cy(wh))

    def resize(self, w, h, resize_counter=0):
        ww, wh = self.get_size()
        geomlog("resize(%s, %s, %s) current size=%s, fullscreen=%s, maximized=%s",
                w, h, resize_counter, (ww, wh), self._fullscreen, self._maximized)
        self._resize_counter = resize_counter
        if (w, h)==(ww, wh):
            self._backing.offsets = 0, 0, 0, 0
            self.queue_draw_area(0, 0, w, h)
            return
        if not self._fullscreen and not self._maximized:
            Gtk.Window.resize(self, w, h)
            ww, wh = w, h
            self._backing.offsets = 0, 0, 0, 0
        else:
            self.center_backing(w, h)
        geomlog("backing offsets=%s, window offset=%s", self._backing.offsets, self.window_offset)
        self._set_backing_size(w, h)
        self.queue_draw_area(0, 0, ww, wh)

    def center_backing(self, w, h):
        ww, wh = self.get_size()
        #align in the middle:
        dw = max(0, ww-w)
        dh = max(0, wh-h)
        ox = dw//2
        oy = dh//2
        geomlog("using window offset values %i,%i", ox, oy)
        #some backings use top,left values,
        #(opengl uses left and botton since the viewport starts at the bottom)
        self._backing.offsets = ox, oy, ox+(dw&0x1), oy+(dh&0x1)
        geomlog("center_backing(%i, %i) window size=%ix%i, backing offsets=%s", w, h, ww, wh, self._backing.offsets)
        #adjust pointer coordinates:
        self.window_offset = ox, oy

    def paint_backing_offset_border(self, backing, context):
        w,h = self.get_size()
        left, top, right, bottom = backing.offsets
        if left!=0 or top!=0 or right!=0 or bottom!=0:
            context.save()
            context.set_source_rgb(*PADDING_COLORS)
            coords = (
                (0, 0, left, h),            #left hand side padding
                (0, 0, w, top),             #top padding
                (w-right, 0, right, h),     #RHS
                (0, h-bottom, w, bottom),   #bottom
                )
            geomlog("paint_backing_offset_border(%s, %s) offsets=%s, size=%s, rgb=%s, coords=%s",
                    backing, context, backing.offsets, (w,h), PADDING_COLORS, coords)
            for rx, ry, rw, rh in coords:
                if rw>0 and rh>0:
                    context.rectangle(rx, ry, rw, rh)
            context.fill()
            context.restore()

    def clip_to_backing(self, backing, context):
        w,h = self.get_size()
        left, top, right, bottom = backing.offsets
        clip_rect = (left, top, w-left-right, h-top-bottom)
        context.rectangle(*clip_rect)
        geomlog("clip_to_backing%s rectangle=%s", (backing, context), clip_rect)
        context.clip()

    def move_resize(self, x, y, w, h, resize_counter=0):
        geomlog("window %i move_resize%s", self._id, (x, y, w, h, resize_counter))
        x, y = self.adjusted_position(x, y)
        w = max(1, w)
        h = max(1, h)
        if self.window_offset:
            x += self.window_offset[0]
            y += self.window_offset[1]
            #TODO: check this doesn't move it off-screen!
        self._resize_counter = resize_counter
        wx, wy = self.get_drawing_area_geometry()[:2]
        if (wx, wy)==(x, y):
            #same location, just resize:
            if self._size==(w, h):
                geomlog("window unchanged")
            else:
                geomlog("unchanged position %ix%i, using resize(%i, %i)", x, y, w, h)
                self.resize(w, h)
            return
        #we have to move:
        if not self.get_realized():
            geomlog("window was not realized yet")
            self.realize()
        #adjust for window frame:
        window = self.get_window()
        ox, oy = window.get_origin()[-2:]
        rx, ry = window.get_root_origin()
        ax = x - (ox - rx)
        ay = y - (oy - ry)
        geomlog("window origin=%ix%i, root origin=%ix%i, actual position=%ix%i", ox, oy, rx, ry, ax, ay)
        #validate against edge of screen (ensure window is shown):
        if CLAMP_WINDOW_TO_SCREEN:
            mw, mh = self._client.get_root_size()
            if (ax + w)<=0:
                ax = -w + 1
            elif ax >= mw:
                ax = mw - 1
            if not WINDOW_OVERFLOW_TOP and ay<=0:
                ay = 0
            elif (ay + h)<=0:
                ay = -y + 1
            elif ay >= mh:
                ay = mh -1
            geomlog("validated window position for total screen area %ix%i : %ix%i", mw, mh, ax, ay)
        if self._size==(w, h):
            #just move:
            geomlog("window size unchanged: %ix%i, using move(%i, %i)", w, h, ax, ay)
            window.move(ax, ay)
            return
        #resize:
        self._size = (w, h)
        geomlog("%s.move_resize%s", window, (ax, ay, w, h))
        window.move_resize(ax, ay, w, h)
        #re-init the backing with the new size
        self._set_backing_size(w, h)


    def noop_destroy(self):
        log.warn("Warning: window destroy called twice!")

    def destroy(self):      #pylint: disable=method-hidden
        self.cancel_window_state_timer()
        self.cancel_send_iconifiy_timer()
        self.cancel_show_pointer_overlay_timer()
        self.cancel_remove_pointer_overlay_timer()
        self.cancel_focus_timer()
        mrt = self.moveresize_timer
        if mrt:
            self.moveresize_timer = None
            self.source_remove(mrt)
        self.on_realize_cb = {}
        ClientWindowBase.destroy(self)
        Gtk.Window.destroy(self)
        self._unfocus()
        self.destroy = self.noop_destroy


    def do_unmap_event(self, event):
        eventslog("do_unmap_event(%s)", event)
        self._unfocus()
        if not self._override_redirect:
            self.send("unmap-window", self._id, False)

    def do_delete_event(self, event):
        #Gtk.Window.do_delete_event(self, event)
        eventslog("do_delete_event(%s)", event)
        self._client.window_close_event(self._id)
        return True


    def _offset_pointer(self, x, y):
        if self.window_offset:
            x -= self.window_offset[0]
            y -= self.window_offset[1]
        return self._client.cp(x, y)

    def _get_pointer(self, event):
        return event.x_root, event.y_root

    def _get_relative_pointer(self, event):
        return event.x, event.y

    def _pointer_modifiers(self, event):
        x, y = self._get_pointer(event)
        rx, ry = self._get_relative_pointer(event)
        #adjust for window offset:
        pointer = self._offset_pointer(x, y)
        relative_pointer = self._client.cp(rx, ry)
        #FIXME: state is used for both mods and buttons??
        modifiers = self._client.mask_to_names(event.state)
        buttons = self._event_buttons(event)
        v = pointer, relative_pointer, modifiers, buttons
        mouselog("pointer_modifiers(%s)=%s (x_root=%s, y_root=%s, window_offset=%s)",
                 event, v, event.x_root, event.y_root, self.window_offset)
        return v

    def _event_buttons(self, event):
        return [button for mask, button in BUTTON_MASK.items() if event.state & mask]

    def parse_key_event(self, event, pressed):
        keyval = event.keyval
        keycode = event.hardware_keycode
        keyname = Gdk.keyval_name(keyval)
        keyname = KEY_TRANSLATIONS.get((keyname, keyval, keycode), keyname)
        key_event = GTKKeyEvent()
        key_event.modifiers = self._client.mask_to_names(event.state)
        key_event.keyname = keyname or ""
        key_event.keyval = keyval or 0
        key_event.keycode = keycode
        key_event.group = event.group
        try:
            key_event.string = event.string or ""
        except UnicodeDecodeError as e:
            keylog("parse_key_event(%s, %s)", event, pressed, exc_info=True)
            if first_time("key-%s-%s" % (keycode, keyname)):
                keylog.warn("Warning: failed to parse string for key")
                keylog.warn(" keyname=%s, keycode=%s", keyname, keycode)
                keylog.warn(" %s", e)
            key_event.string = ""
        key_event.pressed = pressed
        keylog("parse_key_event(%s, %s)=%s", event, pressed, key_event)
        return key_event

    def do_key_press_event(self, event):
        key_event = self.parse_key_event(event, True)
        if self.moveresize_event and key_event.keyname in BREAK_MOVERESIZE:
            #cancel move resize if there is one:
            self.moveresize_event = None
        self._client.handle_key_action(self, key_event)

    def do_key_release_event(self, event):
        key_event = self.parse_key_event(event, False)
        self._client.handle_key_action(self, key_event)


    def _do_scroll_event(self, event):
        if self._client.readonly:
            return
        button_mapping = GDK_SCROLL_MAP.get(event.direction, -1)
        mouselog("do_scroll_event device=%s, direction=%s, button_mapping=%s",
                 self._device_info(event), event.direction, button_mapping)
        if button_mapping>=0:
            self._button_action(button_mapping, event, True)
            self._button_action(button_mapping, event, False)


    def update_icon(self, img):
        self._current_icon = img
        has_alpha = img.mode=="RGBA"
        width, height = img.size
        rowstride = width * (3+int(has_alpha))
        pixbuf = get_pixbuf_from_data(img.tobytes(), has_alpha, width, height, rowstride)
        iconlog("%s.set_icon(%s)", self, pixbuf)
        self.set_icon(pixbuf)
