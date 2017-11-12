#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2013-2017 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.os_util import monotonic_time
from xpra.gtk_common.gobject_compat import import_glib
glib = import_glib()
glib.threads_init()

import unittest
from collections import deque
try:
    from xpra.server.window import video_subregion, region
except ImportError:
    video_subregion = None
    region = None


class TestVersionUtilModule(unittest.TestCase):

    def test_eq(self):
        log = video_subregion.sslog

        def refresh_cb(window, regions):
            log("refresh_cb(%s, %s)", window, regions)
        r = video_subregion.VideoSubregion(glib.timeout_add, glib.source_remove, refresh_cb, 150, True)

        ww = 1024
        wh = 768
        def assertiswin():
            assert r.rectangle and r.rectangle.get_geometry()==(0, 0, ww, wh), "rectangle %s does not match whole window %ix%i" % (r.rectangle, ww, wh)

        log("* checking that we need some events")
        last_damage_events = []
        for x in range(video_subregion.MIN_EVENTS):
            last_damage_events.append((0, 0, 0, 1, 1))
        r.identify_video_subregion(ww, wh, video_subregion.MIN_EVENTS, last_damage_events)
        assert r.rectangle is None

        vr = (monotonic_time(), 100, 100, 320, 240)
        log("* easiest case: all updates in one region")
        last_damage_events = []
        for _ in range(50):
            last_damage_events.append(vr)
        r.identify_video_subregion(ww, wh, 50, last_damage_events)
        assert r.rectangle
        assert r.rectangle==region.rectangle(*vr[1:])

        log("* checking that empty damage events does not cause errors")
        r.reset()
        r.identify_video_subregion(ww, wh, 0, [])
        assert r.rectangle is None

        log("* checking that full window can be a region")
        vr = (monotonic_time(), 0, 0, ww, wh)
        last_damage_events = []
        for _ in range(50):
            last_damage_events.append(vr)
        r.identify_video_subregion(ww, wh, 50, last_damage_events)
        assert r.rectangle is not None

        log("* checking that regions covering the whole window give the same result")
        last_damage_events = deque(maxlen=150)
        for x in range(4):
            for y in range(4):
                vr = (monotonic_time(), ww*x/4, wh*y/4, ww/4, wh/4)
                for _ in range(3):
                    last_damage_events.append(vr)
        r.identify_video_subregion(ww, wh, 150, last_damage_events)
        assertiswin()

        vr = (monotonic_time(), ww/4, wh/4, ww/2, wh/2)
        log("* mixed with region using 1/4 of window and 1/3 of updates: %s", vr)
        for _ in range(24):
            last_damage_events.append(vr)
        r.identify_video_subregion(ww, wh, 200, last_damage_events)
        assertiswin()

        log("* info=%s", r.get_info())

        log("* checking that two video regions quite far apart do not get merged")
        last_damage_events = deque(maxlen=150)
        r.reset()
        v1 = (monotonic_time(), 100, 100, 320, 240)
        v2 = (monotonic_time(), 500, 500, 320, 240)
        for _ in range(50):
            last_damage_events.append(v1)
            last_damage_events.append(v2)
        r.identify_video_subregion(ww, wh, 100, last_damage_events)
        assert r.rectangle is None

        log("* checking that two video regions close to each other can be merged")
        for N1, N2 in ((50, 50), (60, 40), (50, 30)):
            last_damage_events = deque(maxlen=150)
            r.reset()
            v1 = (monotonic_time(), 100, 100, 320, 240)
            for _ in range(N1):
                last_damage_events.append(v1)
            v2 = (monotonic_time(), 460, 120, 320, 240)
            for _ in range(N2):
                last_damage_events.append(v2)
            r.identify_video_subregion(ww, wh, 100, last_damage_events)
            m = region.merge_all([region.rectangle(*v1[1:]), region.rectangle(*v2[1:])])
            assert r.rectangle and r.rectangle==m, "expected %s but got %s for N1=%i, N2=%i" % (m, r.rectangle, N1, N2)


    def test_cases(self):
        from xpra.server.window.video_subregion import scoreinout   #, sslog
        from xpra.server.window.region import rectangle         #@UnresolvedImport
        #sslog.enable_debug()
        r = rectangle(35, 435, 194, 132)
        score = scoreinout(1200, 1024, r, 1466834, 21874694)
        assert score<100
        r = rectangle(100, 600, 320, 240)
        score = scoreinout(1200, 1024, r, 320*240*10, 320*240*25)
        assert score<100


def main():
    if video_subregion and region:
        unittest.main()
    else:
        print("video_subregion_test skipped")

if __name__ == '__main__':
    main()
