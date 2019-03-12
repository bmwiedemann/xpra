#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2018 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import sys

from xpra.util import typedict, AdHocStruct
from xpra.os_util import PYTHON3
from xpra.log import Logger

log = Logger("opengl", "paint")


def get_opengl_backends(option_str):
    parts = option_str.split(":")
    if len(parts)==2:
        backend_str = parts[1]
    else:
        backend_str = option_str
    if backend_str in ("native", "gtk") or backend_str.find(",")>0:
        return backend_str.split(",")
    if PYTHON3:
        return ("native", )
    return ("native", "gtk")

def get_gl_client_window_module(backends, force_enable=False):
    gl_client_window_module = None
    for impl in backends:
        log("attempting to load '%s' OpenGL backend", impl)
        GL_CLIENT_WINDOW_MODULE = "xpra.client.gl.gtk%s.%sgl_client_window" % (sys.version_info[0], impl)
        log("importing %s", GL_CLIENT_WINDOW_MODULE)
        try:
            gl_client_window_module = __import__(GL_CLIENT_WINDOW_MODULE, {}, {}, ["GLClientWindow", "check_support"])
        except ImportError as e:
            log("cannot import %s", GL_CLIENT_WINDOW_MODULE, exc_info=True)
            log.warn("Warning: cannot import %s OpenGL module", impl)
            log.warn(" %s", e)
            del e
            continue
        log("%s=%s", GL_CLIENT_WINDOW_MODULE, gl_client_window_module)
        opengl_props = gl_client_window_module.check_support(force_enable)
        log("check_support(%s)=%s", force_enable, opengl_props)
        if opengl_props:
            return opengl_props, gl_client_window_module
    log("get_gl_client_window_module(%s, %s) no match found", backends, force_enable)
    return {}, None

def test_gl_client_window(gl_client_window_class, max_window_size=(1024, 1024), pixel_depth=24):
    #try to render using a temporary window:
    draw_result = {}
    window = None
    try:
        w, h = 50, 50
        from xpra.client.window_border import WindowBorder
        border = WindowBorder()
        default_cursor_data = None
        noclient = AdHocStruct()
        def no_idle_add(fn, *args, **kwargs):
            fn(*args, **kwargs)
        def no_timeout_add(*_args, **_kwargs):
            raise Exception("timeout_add should not have been called")
        def no_source_remove(*_args, **_kwargs):
            raise Exception("source_remove should not have been called")
        def no_scaling(*args):
            return args
        def get_None(*_args):
            return None
        def noop(*_args):
            pass
        #we have to suspend idle_add to make this synchronous
        #we can do this because this method must be running in the UI thread already:
        noclient.idle_add = no_idle_add
        noclient.timeout_add = no_timeout_add
        noclient.source_remove = no_source_remove
        noclient.sp = noclient.sx = noclient.sy = noclient.srect = no_scaling
        noclient.xscale = noclient.yscale = 1
        noclient.server_window_decorations = True
        noclient.mmap_enabled = False
        noclient.mmap = None
        noclient.encoding_defaults = {}
        noclient.get_window_frame_sizes = get_None
        noclient._set_window_menu = None
        noclient._focused = None
        noclient.request_frame_extents = noop
        window = gl_client_window_class(noclient, None, None, 2**32-1, -100, -100, w, h, w, h,
                                        typedict({}), False, typedict({}),
                                        border, max_window_size, default_cursor_data, pixel_depth)
        window_backing = window._backing
        window_backing.idle_add = no_idle_add
        window_backing.timeout_add = no_timeout_add
        window_backing.source_remove = no_source_remove
        window.realize()
        pixel_format = "BGRX"
        bpp = len(pixel_format)
        options = typedict({"pixel_format" : pixel_format})
        stride = bpp*w
        img_data = b"\0"*stride*h
        coding = "rgb32"
        widget = window_backing._backing
        widget.realize()
        def paint_callback(success, message):
            log("paint_callback(%s, %s)", success, message)
            draw_result.update({
                "success"   : success,
                "message"   : message,
                })
        log("OpenGL: testing draw on %s widget %s with %s : %s", window, widget, coding, pixel_format)
        window.draw_region(0, 0, w, h, coding, img_data, stride, 1, options, [paint_callback])
    finally:
        if window:
            window.destroy()
    log("test_gl_client_window(..) draw_result=%s", draw_result)
    return draw_result
