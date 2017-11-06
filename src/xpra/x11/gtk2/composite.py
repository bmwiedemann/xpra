# This file is part of Xpra.
# Copyright (C) 2008, 2009 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2012-2016 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import gobject

from xpra.log import Logger
from xpra.x11.gtk2.window_damage import WindowDamageHandler
log = Logger("x11", "window")

from xpra.gtk_common.gobject_util import one_arg_signal, AutoPropGObjectMixin
from xpra.x11.gtk2.gdk_bindings import (
            add_event_receiver,             #@UnresolvedImport
            remove_event_receiver,          #@UnresolvedImport
            get_parent)                     #@UnresolvedImport
from xpra.gtk_common.error import trap

from xpra.x11.gtk2.world_window import get_world_window
from xpra.x11.bindings.ximage import XImageBindings #@UnresolvedImport
XImage = XImageBindings()
from xpra.x11.bindings.window_bindings import constants, X11WindowBindings #@UnresolvedImport
X11Window = X11WindowBindings()
X11Window.ensure_XComposite_support()


StructureNotifyMask = constants["StructureNotifyMask"]


class CompositeHelper(WindowDamageHandler, AutoPropGObjectMixin, gobject.GObject):

    __gsignals__ = WindowDamageHandler.__common_gsignals__.copy()
    __gsignals__.update({
        #emit:
        "contents-changed"      : one_arg_signal,
        })

    # This may raise XError.
    def __init__(self, window):
        WindowDamageHandler.__init__(self, window)
        AutoPropGObjectMixin.__init__(self)
        gobject.GObject.__init__(self)
        self._listening_to = None

    def __repr__(self):
        xid = 0
        cw = self.client_window
        if cw:
            xid = cw.xid
        return "CompositeHelper(%#x)" % xid

    def setup(self):
        X11Window.XCompositeRedirectWindow(self.client_window.xid)
        WindowDamageHandler.setup(self)

    def do_destroy(self, window):
        trap.swallow_synced(X11Window.XCompositeUnredirectWindow, window.xid)
        WindowDamageHandler.do_destroy(self, window)

    def invalidate_pixmap(self):
        lt = self._listening_to
        if lt:
            self._listening_to = None
            self._cleanup_listening(lt)
        WindowDamageHandler.invalidate_pixmap(self)

    def _cleanup_listening(self, listening):
        if listening:
            # Don't want to stop listening to self.client_window!:
            assert self.client_window is None or self.client_window not in listening
            for w in listening:
                remove_event_receiver(w, self)

    def _set_pixmap(self):
        # The tricky part here is that the pixmap returned by
        # NameWindowPixmap gets invalidated every time the window's
        # viewable state changes.  ("viewable" here is the X term that
        # means "mapped, and all ancestors are also mapped".)  But
        # there is no X event that will tell you when a window's
        # viewability changes!  Instead we have to find all ancestors,
        # and watch all of them for unmap and reparent events.  But
        # what about races?  I hear you cry.  By doing things in the
        # exact order:
        #   1) select for StructureNotify
        #   2) QueryTree to get parent
        #   3) repeat 1 & 2 up to the root
        #   4) call NameWindowPixmap
        # we are safe.  (I think.)
        listening = []
        e = None
        try:
            root = self.client_window.get_screen().get_root_window()
            world = get_world_window().window
            win = get_parent(self.client_window)
            while win not in (None, root, world) and win.get_parent() is not None:
                # We have to use a lowlevel function to manipulate the
                # event selection here, because SubstructureRedirectMask
                # does not roundtrip through the GDK event mask
                # functions.  So if we used them, here, we would clobber
                # corral window selection masks, and those don't deserve
                # clobbering.  They are our friends!  X is driving me
                # slowly mad.
                X11Window.addXSelectInput(win.xid, StructureNotifyMask)
                add_event_receiver(win, self, max_receivers=-1)
                listening.append(win)
                win = get_parent(win)
            handle = XImage.get_xcomposite_pixmap(self.client_window.xid)
        except Exception as e:
            try:
                self._cleanup_listening(listening)
            except:
                pass
            raise
        if handle is None:
            #avoid race during signal exit, which will clear self.client_window:
            win = self.client_window
            xid = 0
            if win:
                xid = win.xid
            log("failed to name a window pixmap for %#x: %s", xid, e)
            self._cleanup_listening(listening)
        else:
            self._contents_handle = handle
            # Don't save the listening set until after
            # NameWindowPixmap has succeeded, to maintain our
            # invariant:
            self._listening_to = listening


    def do_xpra_damage_event(self, event):
        event.x += self._border_width
        event.y += self._border_width
        self.emit("contents-changed", event)

gobject.type_register(CompositeHelper)
