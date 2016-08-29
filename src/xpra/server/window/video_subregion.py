# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2013-2016 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import time
import math

from xpra.util import MutableInteger
from xpra.server.window.region import rectangle, add_rectangle, remove_rectangle, merge_all    #@UnresolvedImport
from xpra.log import Logger

sslog = Logger("regiondetect")
refreshlog = Logger("regionrefresh")

MAX_TIME = int(os.environ.get("XPRA_VIDEO_DETECT_MAX_TIME", "5"))
MIN_EVENTS = int(os.environ.get("XPRA_VIDEO_DETECT_MIN_EVENTS", "20"))
MIN_W = int(os.environ.get("XPRA_VIDEO_DETECT_MIN_WIDTH", "128"))
MIN_H = int(os.environ.get("XPRA_VIDEO_DETECT_MIN_HEIGHT", "96"))


class VideoSubregion(object):

    def __init__(self, timeout_add, source_remove, refresh_cb, auto_refresh_delay):
        self.timeout_add = timeout_add
        self.source_remove = source_remove
        self.refresh_cb = refresh_cb        #usage: refresh_cb(window, regions)
        self.auto_refresh_delay = auto_refresh_delay
        self.init_vars()

    def init_vars(self):
        self.enabled = True
        self.detection = True
        self.rectangle = None
        self.exclusion_zones = []
        self.inout = 0, 0       #number of damage pixels within / outside the region
        self.score = 0
        self.fps = 0
        self.damaged = 0        #proportion of the rectangle that got damaged (percentage)
        self.set_at = 0         #value of the "damage event count" when the region was set
        self.counter = 0        #value of the "damage event count" recorded at "time"
        self.time = 0           #see above
        self.refresh_timer = None
        self.refresh_regions = []
        #keep track of how much extra we batch non-video regions (milliseconds):
        self.non_max_wait = 150

    def reset(self):
        self.cancel_refresh_timer()
        self.init_vars()

    def cleanup(self):
        self.reset()


    def __repr__(self):
        return "VideoSubregion(%s)" % self.get_info()


    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.novideoregion("disabled")

    def set_detection(self, detection):
        self.detection = detection

    def set_region(self, x, y, w, h):
        sslog("set_region%s", (x, y, w, h))
        if self.detection:
            sslog("video region detection is on - the given region may or may not stick")
        if x==0 and y==0 and w==0 and h==0:
            self.novideoregion()
        else:
            self.rectangle = rectangle(x, y, w, h)

    def set_exclusion_zones(self, zones):
        rects = []
        for (x, y, w, h) in zones:
            rects.append(rectangle(int(x), int(y), int(w), int(h)))
        self.exclusion_zones = rects
        #force expire:
        self.counter = 0

    def set_auto_refresh_delay(self, d):
        refreshlog("subregion auto-refresh delay: %s", d)
        self.auto_refresh_delay = d

    def cancel_refresh_timer(self):
        rt = self.refresh_timer
        if rt:
            self.source_remove(rt)
            self.refresh_timer = None
            self.refresh_regions = []
        refreshlog("cancel_refresh_timer() timer=%s", rt)

    def get_info(self):
        r = self.rectangle
        info = {
                "enabled"   : self.enabled,
                "detection" : self.detection,
                "counter"   : self.counter,
                }
        if r is None:
            return info
        info.update({"x"            : r.x,
                     "y"            : r.y,
                     "width"        : r.width,
                     "height"       : r.height,
                     "rectangle"    : (r.x, r.y, r.width, r.height),
                     "set-at"       : self.set_at,
                     "time"         : int(self.time),
                     "non-max-wait" : self.non_max_wait,
                     "in-out"       : self.inout,
                     "score"        : self.score,
                     "fps"          : self.fps,
                     "damaged"      : self.damaged,
                     "exclusion-zones" : [(r.x, r.y, r.width, r.height) for r in self.exclusion_zones]
                     })
        rr = list(self.refresh_regions)
        if rr:
            for i, r in enumerate(rr):
                info["refresh_region[%s]" % i] = (r.x, r.y, r.width, r.height)
        return info


    def remove_refresh_region(self, region):
        remove_rectangle(self.refresh_regions, region)
        refreshlog("remove_refresh_region(%s) updated refresh regions=%s", region, self.refresh_regions)


    def add_video_refresh(self, region):
        #called by add_refresh_region if the video region got painted on
        #Note: this does not run in the UI thread!
        rect = self.rectangle
        if not rect:
            return
        refreshlog("add_video_refresh(%s) rectangle=%s", region, rect)
        #something in the video region is still refreshing,
        #so we re-schedule the subregion refresh:
        self.cancel_refresh_timer()
        #add the new region to what we already have:
        add_rectangle(self.refresh_regions, region)
        #do refresh any regions which are now outside the current video region:
        #(this can happen when the region moves or changes size)
        non_video = []
        for r in self.refresh_regions:
            if not rect.contains_rect(r):
                non_video += r.substract_rect(rect)
        delay = max(150, self.auto_refresh_delay)
        if non_video:
            #refresh via timeout_add so this will run in the UI thread:
            self.timeout_add(delay, self.refresh_cb, non_video)
            #only keep the regions still in the video region:
            inrect = [rect.intersection_rect(r) for r in self.refresh_regions]
            self.refresh_regions = [r for r in inrect if r is not None]
        #re-schedule the video region refresh (if we have regions to fresh):
        if self.refresh_regions:
            def refresh():
                #runs via timeout_add, safe to call UI!
                self.refresh_timer = None
                regions = self.refresh_regions
                self.refresh_regions = []
                #it probably makes sense to refresh the whole thing:
                #(the window source code doesn't know about the video region,
                # and would decide to do many overlapping refreshes)
                if len(regions)>=2 and rect:
                    regions = [rect]
                refreshlog("refresh() calling %s with regions=%s", self.refresh_cb, regions)
                self.refresh_cb(regions)
            self.refresh_timer = self.timeout_add(delay, refresh)


    def novideoregion(self, msg="", *args):
        sslog("novideoregion: "+msg, *args)
        self.rectangle = None
        self.time = 0
        self.set_at = 0
        self.counter = 0
        self.inout = 0, 0
        self.score = 0
        self.fps = 0
        self.damaged = 0

    def identify_video_subregion(self, ww, wh, damage_events_count, last_damage_events, starting_at=0):
        if not self.detection:
            return
        if not self.enabled:
            #could have been disabled since we started this method!
            self.novideoregion("disabled")
        sslog("%s.identify_video_subregion(..)", self)
        sslog("identify_video_subregion(%s, %s, %s, %s)", ww, wh, damage_events_count, last_damage_events)

        if damage_events_count < self.set_at:
            #stats got reset
            self.set_at = 0
        #validate against window dimensions:
        rect = self.rectangle
        if rect and (rect.width>ww or rect.height>wh):
            #region is now bigger than the window!
            return self.novideoregion("window is now smaller than current region")
        #arbitrary minimum size for regions we will look at:
        #(we don't want video regions smaller than this - too much effort for little gain)
        if ww<MIN_W or wh<MIN_H:
            return self.novideoregion("window is too small: %sx%s", MIN_W, MIN_H)

        def update_markers():
            self.counter = damage_events_count
            self.time = time.time()

        def few_damage_events(event_types, event_count):
            elapsed = time.time()-self.time
            #how many damage events occurred since we chose this region:
            event_count = max(0, damage_events_count - self.set_at)
            #make the timeout longer when the region has worked longer:
            slow_region_timeout = 2 + math.log(2+event_count, 1.5)
            if rect and elapsed>=slow_region_timeout:
                update_markers()
                return self.novideoregion("too much time has passed (%is for %s %s events)", elapsed, event_types, event_count)
            sslog("identify video: waiting for more %s damage events (%s) counters: %s / %s", event_types, event_count, self.counter, damage_events_count)

        if self.counter+10>damage_events_count:
            #less than 10 events since last time we called update_markers:
            event_count = damage_events_count-self.counter
            few_damage_events("total", event_count)
            return

        from_time = max(starting_at, time.time()-MAX_TIME)
        #create a list (copy) to work on:
        lde = [x for x in list(last_damage_events) if x[0]>=from_time]
        dc = len(lde)
        if dc<=MIN_EVENTS:
            return self.novideoregion("not enough damage events yet (%s)", dc)
        #structures for counting areas and sizes:
        wc = {}
        hc = {}
        dec = {}
        #count how many times we see each area, each width/height and where,
        #after removing any exclusion zones:        
        for _,x,y,w,h in lde:
            r = rectangle(x,y,w,h)
            rects = [r]
            if self.exclusion_zones:
                for e in self.exclusion_zones:
                    new_rects = []
                    for r in rects:
                        new_rects += r.substract_rect(e)
                    rects = new_rects
            for r in rects:
                dec.setdefault(r, MutableInteger()).increase()
                if w>=MIN_W:
                    wc.setdefault(w, dict()).setdefault(x, set()).add(r)
                if h>=MIN_H:
                    hc.setdefault(h, dict()).setdefault(y, set()).add(r)

        def inoutcount(region, ignore_size=0):
            #count how many pixels are in or out if this region
            incount, outcount = 0, 0
            for r, count in dec.items():
                inregion = r.intersection_rect(region)
                if inregion:
                    incount += inregion.width*inregion.height*int(count)
                outregions = r.substract_rect(region)
                for x in outregions:
                    if ignore_size>0 and x.width*x.height<ignore_size:
                        #skip small region outside rectangle
                        continue
                    outcount += x.width*x.height*int(count)
            return incount, outcount

        def scoreinout(region, incount, outcount):
            total = incount+outcount
            assert total>0
            #proportion of damage events that are within this region:
            inregion = float(incount)/total
            #devaluate by taking into account the number of pixels in the area
            #so that a large video region only wins if it really
            #has a larger proportion of the pixels
            #(but also offset this value to even things out a bit:
            # if we have a series of vertical or horizontal bands that we merge,
            # we would otherwise end up excluding the ones on the edge
            # if they ever happen to have a slightly lower hit count)
            #summary: bigger is better, as long as we still have more pixels in than out
            width = min(ww, region.width)
            height = min(wh, region.height)
            #proportion of pixels in this region relative to the whole window:
            inwindow = float(width*height) / (ww*wh)
            ratio = inregion / inwindow
            sizeboost = 1+inwindow
            sslog("scoreinout(%s, %i, %i) inregion=%.3f, inwindow=%.3f, ratio=%.3f, sizeboost=%.3f", region, incount, outcount, inregion, inwindow, ratio, sizeboost)
            return int(sizeboost*5 + 100 * ratio**sizeboost)

        def score_region(info, region, ignore_size=0):
            #check if the region given is a good candidate, and if so we use it
            #clamp it:
            if region.width<MIN_W or region.height<MIN_H:
                #too small, ignore it:
                return 0
            #and make sure this does not end up much bigger than needed:
            if ww*wh<(region.width*region.height):
                return 0
            incount, outcount = inoutcount(region, ignore_size)
            total = incount+outcount
            score = scoreinout(region, incount, outcount)
            sslog("testing %12s video region %34s: %3i%% in, %3i%% out, %3i%% of window, score=%2i",
                  info, region, 100*incount//total, 100*outcount//total, 100*region.width*region.height/ww/wh, score)
            return score

        def updateregion(rect):
            self.rectangle = rect
            self.time = time.time()
            self.inout = inoutcount(rect)
            self.score = scoreinout(rect, *self.inout)
            self.fps = int(self.inout[0]/(rect.width*rect.height) / (time.time()-from_time))
            rects = [self.rectangle]
            for _,x,y,w,h in lde:
                r = rectangle(x,y,w,h)
                new_rects = []
                for x in rects:
                    new_rects += x.substract_rect(r)
                rects = new_rects
                if not rects:
                    break
            self.damaged = 100-100*sum((r.width*r.height) for r in rects)//(rect.width*rect.height)
            sslog("score(%s)=%s, damaged=%i%%", self.inout, self.score, self.damaged)

        def setnewregion(rect, msg="", *args):
            sslog("setting new region %s: "+msg, rect, *args)
            self.set_at = damage_events_count
            self.counter = damage_events_count
            if not self.enabled:
                #could have been disabled since we started this method!
                self.novideoregion("disabled")
            if not self.detection:
                return
            updateregion(rect)

        update_markers()

        #see if we can keep the region we already have (if any):
        cur_score = 0
        if rect:
            cur_score = score_region("current", rect)
            if cur_score>=125:
                sslog("keeping existing video region %s with score %s", rect, cur_score)
                return

        scores = {None : 0}

        #split the regions we really care about (enough pixels, big enough):
        damage_count = {}
        min_count = max(2, len(lde)/40)
        for r, count in dec.items():
            #ignore small regions:
            if count>min_count and r.width>=MIN_W and r.height>=MIN_H:
                damage_count[r] = count
        c = sum([int(x) for x in damage_count.values()])
        most_damaged = -1
        most_pct = 0
        if c>0:
            most_damaged = int(sorted(damage_count.values())[-1])
            most_pct = 100*most_damaged/c
            sslog("identify video: most=%s%% damage count=%s", most_pct, damage_count)
            #is there a region that stands out?
            #try to use the region which is responsible for most of the large damage requests:
            most_damaged_regions = [r for r,v in damage_count.items() if v==most_damaged]
            if len(most_damaged_regions)==1:
                r = most_damaged_regions[0]
                score = score_region("most-damaged", r)
                sslog("identify video: score most damaged area %s=%s%%", r, score)
                if score>120:
                    setnewregion(r, "%s%% of large damage requests, score=%s", most_pct, score)
                    return
                elif score>=100:
                    scores[r] = score

        #try harder: try combining regions with the same width or height:
        #(some video players update the video region in bands)
        for w, d in wc.items():
            for x,regions in d.items():
                if len(regions)>=2:
                    #merge regions of width w at x
                    min_count = max(2, len(regions)/25)
                    keep = [r for r in regions if int(dec.get(r, 0))>=min_count]
                    sslog("vertical regions of width %i at %i with at least %i hits: %s", w, x, min_count, keep)
                    if keep:
                        merged = merge_all(keep)
                        scores[merged] = score_region("vertical", merged, 48*48)
        for h, d in hc.items():
            for y,regions in d.items():
                if len(regions)>=2:
                    #merge regions of height h at y
                    min_count = max(2, len(regions)/25)
                    keep = [r for r in regions if int(dec.get(r, 0))>=min_count]
                    sslog("horizontal regions of height %i at %i with at least %i hits: %s", h, y, min_count, keep)
                    if keep:
                        merged = merge_all(keep)
                        scores[merged] = score_region("horizontal", merged, 48*48)

        sslog("merged regions scores: %s", scores)
        highscore = max(scores.values())
        #a score of 100 is neutral
        if highscore>=120:
            region = [r for r,s in scores.items() if s==highscore][0]
            return setnewregion(region, "very high score: %s", highscore)

        #retry existing region, tolerate lower score:
        if cur_score>=90 and (highscore<100 or cur_score>=highscore):
            sslog("keeping existing video region %s with score %s", rect, cur_score)
            updateregion(self.rectangle)
            return

        if highscore>=100:
            region = [r for r,s in scores.items() if s==highscore][0]
            return setnewregion(region, "high score: %s", highscore)

        #FIXME: re-add some scrolling detection

        #try harder still: try combining all the regions we haven't discarded
        #(flash player with firefox and youtube does stupid unnecessary repaints)
        if len(damage_count)>=2:
            merged = merge_all(damage_count.keys())
            score = score_region("merged", merged)
            if score>=110:
                return setnewregion(merged, "merged all regions, score=%s", score, 48*48)

        self.novideoregion("failed to identify a video region")
