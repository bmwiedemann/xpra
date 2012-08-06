# This file is part of Parti.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2012 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008, 2010 Nathaniel Smith <njs@pobox.com>
# Parti is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

#pygtk3 vs pygtk2 (sigh)
from wimpiggy.gobject_compat import import_gobject, import_gtk, import_gdk, is_gtk3
gobject = import_gobject()
gtk = import_gtk()
gdk = import_gdk()
if is_gtk3():
    def init_window(win, wintype):
        #TODO: no idea how to do this with gtk3
        #maybe not even possible..
        gtk.Window.__init__(win)
    def is_mapped(win):
        return win.get_mapped()
    def get_window_geometry(gtkwindow):
        x, y = gtkwindow.get_position()
        w, h = gtkwindow.get_size()
        return (x, y, w, h)
    def set_geometry_hints(window, hints):
        """ we convert the hints as a dict into a gdk.Geometry + gdk.WindowHints """
        wh = gdk.WindowHints
        name_to_hint = {"maximum-size"  : wh.MAX_SIZE,
                        "max_width"     : wh.MAX_SIZE,
                        "max_height"    : wh.MAX_SIZE,
                        "minimum-size"  : wh.MIN_SIZE,
                        "min_width"     : wh.MIN_SIZE,
                        "min_height"    : wh.MIN_SIZE,
                        "base-size"     : wh.BASE_SIZE,
                        "base_width"    : wh.BASE_SIZE,
                        "base_height"   : wh.BASE_SIZE,
                        "increment"     : wh.RESIZE_INC,
                        "width_inc"     : wh.RESIZE_INC,
                        "height_inc"    : wh.RESIZE_INC,
                        "min_aspect_ratio"  : wh.ASPECT,
                        "max_aspect_ratio"  : wh.ASPECT,
                        }
        #these fields can be copied directly to the gdk.Geometry as ints:
        INT_FIELDS= ["min_width",    "min_height",
                        "max_width",    "max_height",
                        "base_width",   "base_height",
                        "width_inc",    "height_inc"]
        ASPECT_FIELDS = {
                        "min_aspect_ratio"  : "min_aspect",
                        "max_aspect_ratio"  : "max_aspect",
                         }
        geom = gdk.Geometry()
        mask = 0
        for k,v in hints.items():
            if k in INT_FIELDS:
                setattr(geom, k, int(v))
                mask |= int(name_to_hint.get(k, 0))
            elif k in ASPECT_FIELDS:
                field = ASPECT_FIELDS.get(k)
                setattr(geom, field, float(v))
                mask |= int(name_to_hint.get(k, 0))
        hints = gdk.WindowHints(mask)
        window.set_geometry_hints(None, geom, hints)

    def queue_draw(window, x, y, width, height):
        window.queue_draw_area(x, y, width, height)
    WINDOW_POPUP = gtk.WindowType.POPUP
    WINDOW_TOPLEVEL = gtk.WindowType.TOPLEVEL
    WINDOW_EVENT_MASK = 0
    OR_TYPE_HINTS = []
    NAME_TO_HINT = { }
    SCROLL_MAP = {}
else:
    def init_window(gtkwindow, wintype):
        gtk.Window.__init__(gtkwindow, wintype)
    def is_mapped(win):
        return win.window is not None and win.window.is_visible()
    def get_window_geometry(gtkwindow):
        gdkwindow = gtkwindow.get_window()
        x, y = gdkwindow.get_origin()
        _, _, w, h, _ = gdkwindow.get_geometry()
        return (x, y, w, h)
    def set_geometry_hints(gtkwindow, hints):
        gtkwindow.set_geometry_hints(None, **hints)

    def queue_draw(gtkwindow, x, y, width, height):
        gtkwindow.get_window().invalidate_rect(gdk.Rectangle(x, y, width, height), False)
    WINDOW_POPUP = gtk.WINDOW_POPUP
    WINDOW_TOPLEVEL = gtk.WINDOW_TOPLEVEL
    WINDOW_EVENT_MASK = gdk.STRUCTURE_MASK | gdk.KEY_PRESS_MASK | gdk.KEY_RELEASE_MASK | gdk.POINTER_MOTION_MASK | gdk.BUTTON_PRESS_MASK | gdk.BUTTON_RELEASE_MASK
    OR_TYPE_HINTS = [gdk.WINDOW_TYPE_HINT_DIALOG,
                gdk.WINDOW_TYPE_HINT_MENU, gdk.WINDOW_TYPE_HINT_TOOLBAR,
                #gdk.WINDOW_TYPE_HINT_SPLASHSCREEN, gdk.WINDOW_TYPE_HINT_UTILITY,
                #gdk.WINDOW_TYPE_HINT_DOCK, gdk.WINDOW_TYPE_HINT_DESKTOP,
                gdk.WINDOW_TYPE_HINT_DROPDOWN_MENU, gdk.WINDOW_TYPE_HINT_POPUP_MENU,
                gdk.WINDOW_TYPE_HINT_TOOLTIP,
                #gdk.WINDOW_TYPE_HINT_NOTIFICATION,
                gdk.WINDOW_TYPE_HINT_COMBO,gdk.WINDOW_TYPE_HINT_DND]
    NAME_TO_HINT = {
                "_NET_WM_WINDOW_TYPE_NORMAL"    : gdk.WINDOW_TYPE_HINT_NORMAL,
                "_NET_WM_WINDOW_TYPE_DIALOG"    : gdk.WINDOW_TYPE_HINT_DIALOG,
                "_NET_WM_WINDOW_TYPE_MENU"      : gdk.WINDOW_TYPE_HINT_MENU,
                "_NET_WM_WINDOW_TYPE_TOOLBAR"   : gdk.WINDOW_TYPE_HINT_TOOLBAR,
                "_NET_WM_WINDOW_TYPE_SPLASH"    : gdk.WINDOW_TYPE_HINT_SPLASHSCREEN,
                "_NET_WM_WINDOW_TYPE_UTILITY"   : gdk.WINDOW_TYPE_HINT_UTILITY,
                "_NET_WM_WINDOW_TYPE_DOCK"      : gdk.WINDOW_TYPE_HINT_DOCK,
                "_NET_WM_WINDOW_TYPE_DESKTOP"   : gdk.WINDOW_TYPE_HINT_DESKTOP,
                "_NET_WM_WINDOW_TYPE_DROPDOWN_MENU" : gdk.WINDOW_TYPE_HINT_DROPDOWN_MENU,
                "_NET_WM_WINDOW_TYPE_POPUP_MENU": gdk.WINDOW_TYPE_HINT_POPUP_MENU,
                "_NET_WM_WINDOW_TYPE_TOOLTIP"   : gdk.WINDOW_TYPE_HINT_TOOLTIP,
                "_NET_WM_WINDOW_TYPE_NOTIFICATION" : gdk.WINDOW_TYPE_HINT_NOTIFICATION,
                "_NET_WM_WINDOW_TYPE_COMBO"     : gdk.WINDOW_TYPE_HINT_COMBO,
                "_NET_WM_WINDOW_TYPE_DND"       : gdk.WINDOW_TYPE_HINT_DND
                }
    # Map scroll directions back to mouse buttons.  Mapping is taken from
    # gdk/x11/gdkevents-x11.c.
    SCROLL_MAP = {gdk.SCROLL_UP: 4,
                  gdk.SCROLL_DOWN: 5,
                  gdk.SCROLL_LEFT: 6,
                  gdk.SCROLL_RIGHT: 7,
                  }


import cairo
import re
import sys

from wimpiggy.log import Logger
log = Logger()

from xpra.window_backing import new_backing

if sys.version < '3':
    import codecs
    def u(x):
        return codecs.unicode_escape_decode(x)[0]
else:
    def u(x):
        return x



class ClientWindow(gtk.Window):
    def __init__(self, client, wid, x, y, w, h, metadata, override_redirect):
        if override_redirect:
            init_window(self, WINDOW_POPUP)
        else:
            init_window(self, WINDOW_TOPLEVEL)
        self._client = client
        self._id = wid
        self._pos = (-1, -1)
        self._size = (1, 1)
        self._backing = None
        self.new_backing(w, h)
        self._metadata = {}
        self._override_redirect = override_redirect
        self._refresh_timer = None
        self._refresh_ignore_sequence = -1
        # used for only sending focus events *after* the window is mapped:
        self._been_mapped = False
        self._override_redirect_windows = []
        # tell KDE/oxygen not to intercept clicks
        # see: https://bugs.kde.org/show_bug.cgi?id=274485
        self.set_data("_kde_no_window_grab", 1)

        self.update_metadata(metadata)

        self.set_app_paintable(True)
        self.add_events(WINDOW_EVENT_MASK)
        self.move(x, y)
        self.set_default_size(w, h)
        if override_redirect:
            transient_for = self.get_transient_for()
            type_hint = self.get_type_hint()
            if transient_for is not None and transient_for.window is not None and type_hint in OR_TYPE_HINTS:
                transient_for._override_redirect_windows.append(self)
        self.connect("notify::has-toplevel-focus", self._focus_change)

    def new_backing(self, w, h):
        self._backing = new_backing(self._id, w, h, self._backing, self._client.supports_mmap, self._client.mmap)

    def update_metadata(self, metadata):
        self._metadata.update(metadata)

        title = u(self._client.title)
        if title.find("@")>=0:
            #perform metadata variable substitutions:
            default_values = {"title" : u("<untitled window>"),
                              "client-machine" : u("<unknown machine>")}
            def metadata_replace(match):
                atvar = match.group(0)          #ie: '@title@'
                var = atvar[1:len(atvar)-1]     #ie: 'title'
                default_value = default_values.get(var, u("<unknown %s>") % var)
                value = self._metadata.get(var, default_value)
                if sys.version<'3':
                    value = value.decode("utf-8")
                return value
            title = re.sub("@[\w\-]*@", metadata_replace, title)
        self.set_title(title)

        if "size-constraints" in self._metadata:
            size_metadata = self._metadata["size-constraints"]
            hints = {}
            for (a, h1, h2) in [
                ("maximum-size", "max_width", "max_height"),
                ("minimum-size", "min_width", "min_height"),
                ("base-size", "base_width", "base_height"),
                ("increment", "width_inc", "height_inc"),
                ]:
                if a in self._metadata["size-constraints"]:
                    hints[h1], hints[h2] = size_metadata[a]
            for (a, h) in [
                ("minimum-aspect", "min_aspect_ratio"),
                ("maximum-aspect", "max_aspect_ratio"),
                ]:
                if a in self._metadata:
                    hints[h] = size_metadata[a][0] * 1.0 / size_metadata[a][1]
            set_geometry_hints(self, hints)

        if hasattr(self, "get_realized"):
            #pygtk 2.22 and above have this method:
            realized = self.get_realized()
        else:
            #older versions:
            realized = self.flags() & gtk.REALIZED
        if not realized:
            self.set_wmclass(*self._metadata.get("class-instance",
                                                 ("xpra", "Xpra")))

        if "icon" in self._metadata:
            (width, height, coding, data) = self._metadata["icon"]
            if coding == "premult_argb32":
                cairo_surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
                cairo_surf.get_data()[:] = data
                # FIXME: We round-trip through PNG. This is ridiculous, but faster
                # than doing a bunch of alpha un-premultiplying and byte-swapping
                # by hand in Python (better still would be to write some Pyrex,
                # but I don't have time right now):
                loader = gdk.PixbufLoader()
                cairo_surf.write_to_png(loader)
                loader.close()
                pixbuf = loader.get_pixbuf()
            else:
                loader = gdk.PixbufLoader(coding)
                loader.write(data, len(data))
                loader.close()
                pixbuf = loader.get_pixbuf()
            self.set_icon(pixbuf)

        if "transient-for" in self._metadata:
            wid = self._metadata.get("transient-for")
            window = self._client._id_to_window.get(wid)
            log("found transient-for: %s / %s", wid, window)
            if window:
                self.set_transient_for(window)

        #apply window-type hint if window is not mapped yet:
        if "window-type" in self._metadata and not is_mapped(self):
            window_types = self._metadata.get("window-type")
            log("window types=%s", window_types)
            for window_type in window_types:
                hint = NAME_TO_HINT.get(window_type)
                if hint:
                    log("setting window type to %s - %s", window_type, hint)
                    self.set_type_hint(hint)
                    break

    def refresh_window(self, *args):
        log("refresh_window(%s) wid=%s", args, self._id)
        self._client.send_refresh(self._id)

    def refresh_all_windows(self):
        #this method is only here because we may want to fire it
        #from a --key-shortcut action and the event is delivered to
        #the "ClientWindow"
        self._client.send_refresh_all()

    def draw_region(self, x, y, width, height, coding, img_data, rowstride, packet_sequence):
        success = self._backing.draw_region(x, y, width, height, coding, img_data, rowstride)
        if success:
            queue_draw(self, x, y, width, height)
        #clear the auto refresh if enough pixels were sent (arbitrary limit..)
        if success and self._refresh_timer and width*height>16*16:
            gobject.source_remove(self._refresh_timer)
            self._refresh_timer = None
        #if we need to set a refresh timer, do it:
        if self._refresh_timer is None and self._client.auto_refresh_delay and coding in ("jpeg", "vpx", "x264"):
            #make sure our own refresh does not make us fire again
            #FIXME: this should be per-window!
            if self._refresh_ignore_sequence<packet_sequence:
                self._refresh_ignore_sequence = packet_sequence+1
                self._refresh_timer = gobject.timeout_add(int(1000 * self._client.auto_refresh_delay), self.refresh_window)
        return success

    """ gtk3 """
    def do_draw(self, context):
        log("do_draw(%s)", context)
        if self.get_mapped():
            self._backing.cairo_draw(context, 0, 0)

    """ gtk2 """
    def do_expose_event(self, event):
        log("do_expose_event(%s) area=%s", event, event.area)
        if not (self.flags() & gtk.MAPPED):
            return
        x,y,_,_ = event.area
        context = self.window.cairo_create()
        context.rectangle(event.area)
        context.clip()
        self._backing.cairo_draw(context, x, y)

    def do_map_event(self, event):
        log("Got map event")
        gtk.Window.do_map_event(self, event)
        if not self._override_redirect:
            x, y, w, h = get_window_geometry(self)
            self._client.send(["map-window", self._id, x, y, w, h])
            self._pos = (x, y)
            self._size = (w, h)
        self._been_mapped = True
        gobject.idle_add(self._focus_change)

    def do_configure_event(self, event):
        log("Got configure event: %s", event)
        gtk.Window.do_configure_event(self, event)
        if self._override_redirect:
            return
        x, y, w, h = get_window_geometry(self)
        ox, oy = self._pos
        dx, dy = x-ox, y-oy
        self._pos = (x, y)
        if self._client.window_configure:
            #if we support configure-window, send that first
            self._client.send(["configure-window", self._id, x, y, w, h])
        if dx!=0 or dy!=0:
            #window has moved
            if not self._client.window_configure:
                #if we don't handle the move via configure:
                self._client.send(["move-window", self._id, x, y])
            #move any OR window with their parent:
            for window in self._override_redirect_windows:
                x, y = window.get_position()
                window.move(x+dx, y+dy)
        if (w, h) != self._size:
            self._size = (w, h)
            self.new_backing(w, h)
            if not self._client.window_configure:
                self._client.send(["resize-window", self._id, w, h])

    def move_resize(self, x, y, w, h):
        assert self._override_redirect
        self.window.move_resize(x, y, w, h)
        self.new_backing(w, h)

    def destroy(self):
        self._unfocus()
        gtk.Window.destroy(self)
        self._backing.close()

    def _unfocus(self):
        if self._client._focused==self._id:
            self._client.update_focus(self._id, False)

    def do_unmap_event(self, event):
        self._unfocus()
        if not self._override_redirect:
            self._client.send(["unmap-window", self._id])

    def do_delete_event(self, event):
        self._client.send(["close-window", self._id])
        return True

    def quit(self):
        self._client.quit(0)

    def void(self):
        pass

    def do_key_press_event(self, event):
        self._client.handle_key_action(event, self, True)

    def do_key_release_event(self, event):
        self._client.handle_key_action(event, self, False)

    def _pointer_modifiers(self, event):
        pointer = (int(event.x_root), int(event.y_root))
        modifiers = self._client.mask_to_names(event.state)
        return pointer, modifiers

    def do_motion_notify_event(self, event):
        if self._client.readonly:
            return
        (pointer, modifiers) = self._pointer_modifiers(event)
        self._client.send_mouse_position(["pointer-position", self._id,
                                          pointer, modifiers])

    def _button_action(self, button, event, depressed):
        if self._client.readonly:
            return
        (pointer, modifiers) = self._pointer_modifiers(event)
        self._client.send_positional(["button-action", self._id,
                                      button, depressed,
                                      pointer, modifiers])

    def do_button_press_event(self, event):
        if self._client.readonly:
            return
        self._button_action(event.button, event, True)

    def do_button_release_event(self, event):
        if self._client.readonly:
            return
        self._button_action(event.button, event, False)

    def do_scroll_event(self, event):
        if self._client.readonly:
            return
        self._button_action(SCROLL_MAP[event.direction], event, True)
        self._button_action(SCROLL_MAP[event.direction], event, False)

    def _focus_change(self, *args):
        log("_focus_change(%s)", args)
        if self._been_mapped:
            self._client.update_focus(self._id, self.get_property("has-toplevel-focus"))


gobject.type_register(ClientWindow)
