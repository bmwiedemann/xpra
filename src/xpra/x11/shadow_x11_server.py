# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2012-2013 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import gtk.gdk

from xpra.x11.x11_server_base import X11ServerBase
from xpra.server.shadow_server_base import ShadowServerBase
from xpra.server.gtk_root_window_model import GTKRootWindowModel


class ShadowX11Server(ShadowServerBase, X11ServerBase):

    def __init__(self):
        ShadowServerBase.__init__(self, gtk.gdk.get_default_root_window())
        X11ServerBase.__init__(self, False)

    def init(self, opts):
        X11ServerBase.init(self, opts)

    def makeRootWindowModel(self):
        return GTKRootWindowModel(self.root)

    def _process_mouse_common(self, proto, wid, pointer, modifiers):
        #adjust pointer position for offset in client:
        x, y = pointer
        wx, wy = self.mapped_at[:2]
        pointer = x-wx, y-wy
        X11ServerBase._process_mouse_common(self, proto, wid, pointer, modifiers)

    def make_hello(self, source):
        capabilities = X11ServerBase.make_hello(self, source)
        capabilities.update(ShadowServerBase.make_hello(self, source))
        capabilities["server_type"] = "Python/gtk2/x11-shadow"
        return capabilities

    def get_info(self, proto):
        info = X11ServerBase.get_info(self, proto)
        info["features.shadow"] = True
        info["server.type"] = "Python/gtk2/x11-shadow"
        return info
