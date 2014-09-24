# This file is part of Xpra.
# Copyright (C) 2008, 2009 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2010-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import time

from xpra.log import Logger
log = Logger("x11", "bindings", "randr")


ctypedef unsigned long CARD32

cdef extern from "X11/X.h":
    unsigned long CurrentTime
    unsigned long Success

######
# Xlib primitives and constants
######
cdef extern from "X11/Xlib.h":
    ctypedef struct Display:
        pass
    # To make it easier to translate stuff in the X header files into
    # appropriate pyrex declarations, without having to untangle the typedefs
    # over and over again, here are some convenience typedefs.  (Yes, CARD32
    # really is 64 bits on 64-bit systems.  Why?  I have no idea.)
    ctypedef CARD32 XID

    ctypedef int Bool
    ctypedef int Status
    ctypedef XID Drawable
    ctypedef XID Window
    ctypedef CARD32 Time

    Window XDefaultRootWindow(Display * display)

###################################
# Randr
###################################
cdef extern from "X11/extensions/randr.h":
    cdef unsigned int RR_Rotate_0

cdef extern from "X11/extensions/Xrandr.h":
    Bool XRRQueryExtension(Display *, int *, int *)
    Status XRRQueryVersion(Display *, int * major, int * minor)
    ctypedef struct XRRScreenSize:
        int width, height
        int mwidth, mheight
    XRRScreenSize *XRRSizes(Display *dpy, int screen, int *nsizes)
    void XRRSetScreenSize(Display *dpy, Window w, int width, int height, int mmWidth, int mmHeight)

    ctypedef unsigned short SizeID
    ctypedef struct XRRScreenConfiguration:
        pass
    ctypedef unsigned short Rotation
    Status XRRSetScreenConfigAndRate(Display *dpy, XRRScreenConfiguration *config,
                                  Drawable draw, int size_index, Rotation rotation,
                                  short rate, Time timestamp)
    XRRScreenConfiguration *XRRGetScreenInfo(Display *, Window w)
    XRRScreenSize *XRRConfigSizes(XRRScreenConfiguration *config, int *nsizes)
    short *XRRConfigRates(XRRScreenConfiguration *config, int sizeID, int *nrates)
    SizeID XRRConfigCurrentConfiguration(XRRScreenConfiguration *config, Rotation *rotation)

    void XRRFreeScreenConfigInfo(XRRScreenConfiguration *)

    int XScreenCount(Display *display)
    int XDisplayWidthMM(Display *display, int screen_number)
    int XDisplayHeightMM(Display *display, int screen_number)

from core_bindings cimport X11CoreBindings


cdef class RandRBindings(X11CoreBindings):

    cdef int _has_randr

    def __init__(self):
        self._has_randr = self.check_randr()

    def check_randr(self):
        cdef int event_base = 0, ignored = 0, cmajor = 0, cminor = 0
        if XRRQueryExtension(self.display, &event_base, &ignored):
            if XRRQueryVersion(self.display, &cmajor, &cminor):
                log("found XRandR extension version %s.%s", cmajor, cminor)
                if cmajor==1 and cminor>=2:
                    return True
        return False

    def has_randr(self):
        if self._has_randr is None:
            try:
                self._has_randr = self.check_randr()
            except Exception as e:
                log.warn("Warning: %s", e)
        return bool(self._has_randr)

    cdef _get_screen_sizes(self):
        cdef int num_sizes = 0
        cdef XRRScreenSize * xrrs
        cdef XRRScreenSize xrr
        xrrs = XRRSizes(self.display, 0, &num_sizes)
        sizes = []
        for i in range(num_sizes):
            xrr = xrrs[i]
            sizes.append((xrr.width, xrr.height))
        return    sizes

    def get_screen_sizes(self):
        return self._get_screen_sizes()

    cdef _set_screen_size(self, width, height):
        cdef Window window
        cdef XRRScreenConfiguration *config
        cdef int num_sizes = 0                          #@DuplicatedSignature
        cdef int num_rates = 0
        cdef short* rates = <short*> 0
        cdef short rate = 0
        cdef Rotation rotation = 0
        cdef Time time = 0
        cdef int sizeID = 0
        cdef XRRScreenSize *xrrs
        cdef XRRScreenSize xrr                          #@DuplicatedSignature

        window = XDefaultRootWindow(self.display)
        try:
            config = XRRGetScreenInfo(self.display, window)
            xrrs = XRRConfigSizes(config, &num_sizes)
            sizes = []
            sizeID = -1
            for i in range(num_sizes):
                xrr = xrrs[i]
                if xrr.width==width and xrr.height==height:
                    sizeID = i
            if sizeID<0:
                log.error("size not found for %sx%s" % (width, height))
                return False
            rates = XRRConfigRates(config, sizeID, &num_rates)
            rate = rates[0]
            rotation = RR_Rotate_0
            time = CurrentTime
            status = XRRSetScreenConfigAndRate(self.display, config, window, sizeID, rotation, rate, time)
            if status != Success:
                log.error("failed to set new screen size")
                return False
            return True
        finally:
            XRRFreeScreenConfigInfo(config)

    def get_screen_count(self):
        return XScreenCount(self.display)

    def get_screen_sizes_mm(self):
        cdef int n = XScreenCount(self.display)
        cdef int i, w, h
        cdef object sizes = []
        for i in range(n):
            w = XDisplayWidthMM(self.display, i)
            h = XDisplayHeightMM(self.display, i)
            sizes.append((w, h))
        return sizes

    def get_screen_size(self):
        return self._get_screen_size()

    def _get_screen_size(self):
        cdef Window window                              #@DuplicatedSignature
        cdef XRRScreenSize *xrrs                        #@DuplicatedSignature
        cdef Rotation original_rotation
        cdef int num_sizes = 0                          #@DuplicatedSignature
        cdef SizeID size_id
        window = XDefaultRootWindow(self.display)
        cdef XRRScreenConfiguration *config             #@DuplicatedSignature
        try:
            config = XRRGetScreenInfo(self.display, window)
            xrrs = XRRConfigSizes(config, &num_sizes)
            #short original_rate = XRRConfigCurrentRate(config);
            size_id = XRRConfigCurrentConfiguration(config, &original_rotation);

            width = xrrs[size_id].width;
            height = xrrs[size_id].height;
            return int(width), int(height)
        finally:
            XRRFreeScreenConfigInfo(config)

    def set_screen_size(self, width, height):
        return self._set_screen_size(width, height)
