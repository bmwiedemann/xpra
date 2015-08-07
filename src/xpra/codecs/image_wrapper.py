# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2013 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import time

try:
    memoryview_type = memoryview
except:
    memoryview_type = None

class ImageWrapper(object):

    PACKED = 0
    _3_PLANES = 3
    _4_PLANES = 4
    PLANE_OPTIONS = (PACKED, _3_PLANES, _4_PLANES)
    PLANE_NAMES = {PACKED       : "PACKED",
                   _3_PLANES    : "3_PLANES",
                   _4_PLANES    : "4_PLANES"}

    def __init__(self, x, y, width, height, pixels, pixel_format, depth, rowstride, planes=PACKED, thread_safe=True):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.pixels = pixels
        self.pixel_format = pixel_format
        self.depth = depth
        self.rowstride = rowstride
        self.planes = planes
        self.thread_safe = thread_safe
        self.freed = False
        self.timestamp = int(time.time()*1000)

    def __repr__(self):
        return "%s(%s:%s:%s)" % (type(self), self.pixel_format, self.get_geometry(), ImageWrapper.PLANE_NAMES.get(self.planes))

    def get_geometry(self):
        return self.x, self.y, self.width, self.height, self.depth

    def get_x(self):
        return self.x

    def get_y(self):
        return self.x

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height

    def get_rowstride(self):
        return self.rowstride

    def get_depth(self):
        return self.depth

    def get_size(self):
        return self.rowstride * self.height

    def get_pixel_format(self):
        return self.pixel_format

    def get_pixels(self):
        return self.pixels

    def get_planes(self):
        return self.planes

    def is_thread_safe(self):
        """ if True, free() and clone_pixel_data() can be called from any thread,
            if False, free() and clone_pixel_data() must be called from the same thread.
            Used by XImageWrapper to ensure X11 images are freed from the UI thread.
        """
        return self.thread_safe

    def get_timestamp(self):
        """ time in millis """
        return self.timestamp


    def set_timestamp(self, timestamp):
        self.timestamp = timestamp

    def set_planes(self, planes):
        self.planes = planes

    def set_rowstride(self, rowstride):
        self.rowstride = rowstride

    def set_pixel_format(self, pixel_format):
        self.pixel_format = pixel_format

    def set_pixels(self, pixels):
        self.pixels = pixels

    def allocate_buffer(self, buf_len, free_existing=1):
        #only defined for XImage wrappers:
        return 0

    def restride(self, *args):
        #not supported by the generic image wrapper:
        return False

    def freeze(self):
        #some wrappers (XShm) need to be told to stop updating the pixel buffer
        return False

    def clone_plane(self, plane):
        if type(plane)==memoryview_type:
            return plane.tobytes()
        return plane[:]

    def clone_pixel_data(self):
        assert not self.freed, "image has already been freed!"
        if self.planes == 0:
            #no planes, simple buffer:
            assert self.pixels, "no pixels!"
            self.pixels = self.clone_plane(self.pixels)
        else:
            assert self.planes>0
            for i in range(self.planes):
                self.pixels[i] = self.clone_plane(self.pixels[i])
        self.thread_safe = True

    def __del__(self):
        #print("ImageWrapper.__del__() calling %s" % self.free)
        self.free()

    def free(self):
        #print("ImageWrapper.free()")
        if not self.freed:
            self.freed = True
            self.planes = None
            self.pixels = None
            self.pixel_format = None
