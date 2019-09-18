#!/usr/bin/env python
# Copyright (C) 2017 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import cairo
from gi.repository import Gtk

from xpra.gtk_common.gtk_util import WIN_POS_CENTER, KEY_PRESS_MASK, add_close_accel


class TransparentWindow(Gtk.Window):

    def __init__(self):
        super(TransparentWindow, self).__init__()
        self.set_position(WIN_POS_CENTER)
        self.set_default_size(320, 320)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)
        else:
            print("transparency not available!")
        self.set_app_paintable(True)
        self.set_events(KEY_PRESS_MASK)
        self.connect("draw", self.area_draw)
        self.connect("destroy", Gtk.main_quit)
        self.show_all()

    def do_expose_event(self, *_args):
        cr = self.get_window().cairo_create()
        self.area_draw(self, cr)

    def area_draw(self, widget, cr):
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.0) # Transparent

        # Draw the background
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()

        # Draw a circle
        (width, height) = widget.get_size()
        cr.set_source_rgba(1.0, 0.2, 0.2, 0.6)
        # Python <2.4 doesn't have conditional expressions
        if width < height:
            radius = float(width)/2 - 0.8
        else:
            radius = float(height)/2 - 0.8

        cr.arc(float(width)/2, float(height)/2, radius, 0, 2.0*3.14)
        cr.fill()
        cr.stroke()

def main():
    import signal
    def signal_handler(*_args):
        Gtk.main_quit()
    signal.signal(signal.SIGINT, signal_handler)
    w = TransparentWindow()
    add_close_accel(w, Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    main()
