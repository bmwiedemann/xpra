# This file is part of Xpra.
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2012-2016 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

#pygtk3 vs pygtk2 (sigh)
from xpra.gtk_common.gobject_compat import import_glib
glib = import_glib()

from xpra.client.window_backing_base import fire_paint_callbacks
from xpra.util import envint
from xpra.log import Logger
log = Logger("window", "fake")


FAKE_BACKING_DELAY = envint("XPRA_FAKE_BACKING_DELAY", 5)


class FakeBacking(object):

    HAS_ALPHA = True

    def __init__(self, wid, *_args):
        self.wid = wid
        self.fake_delay = FAKE_BACKING_DELAY
        self._video_encoder, self._video_encoder_lock, self._video_encoder_speed, self._video_encoder_quality = None, None, [], []

    def close(self):
        pass

    def draw_region(self, x, y, width, height, coding, img_data, rowstride, options, callbacks):
        log("draw_region(..) faking it after %sms", self.fake_delay)
        glib.timeout_add(self.fake_delay, fire_paint_callbacks, callbacks, True)

    def cairo_draw(self, context, x, y):
        pass

    def get_encoding_properties(self):
        return {
                "encodings.rgb_formats"    : ["RGBA", "RGB"],
               }
