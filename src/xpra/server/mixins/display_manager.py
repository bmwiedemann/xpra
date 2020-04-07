# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2010-2020 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.os_util import strtobytes
from xpra.util import engs, csv, iround
from xpra.util import log_screen_sizes
from xpra.os_util import bytestostr, get_loaded_kernel_modules
from xpra.server.mixins.stub_server_mixin import StubServerMixin
from xpra.log import Logger

log = Logger("screen")
gllog = Logger("opengl")

"""
Mixin for servers that handle displays.
"""
class DisplayManager(StubServerMixin):

    def __init__(self):
        self.randr = False
        self.bell = False
        self.cursors = False
        self.default_dpi = 96
        self.dpi = 0
        self.xdpi = 0
        self.ydpi = 0
        self.antialias = {}
        self.cursor_size = 0
        self.double_click_time  = -1
        self.double_click_distance = -1, -1
        self.opengl = False
        self.opengl_props = {}

    def init(self, opts):
        self.opengl = opts.opengl
        self.bell = opts.bell
        self.cursors = opts.cursors
        self.default_dpi = int(opts.dpi)


    def parse_hello(self, ss, caps, send_ui):
        if send_ui:
            self.parse_screen_info(ss)


    def last_client_exited(self):
        self.reset_icc_profile()


    def threaded_setup(self):
        self.opengl_props = self.query_opengl()


    def query_opengl(self):
        props = {}
        if self.opengl.lower()=="noprobe":
            gllog("query_opengl() skipped: %s", self.opengl)
            return
        blacklisted_kernel_modules = get_loaded_kernel_modules("vboxguest", "vboxvideo")
        if blacklisted_kernel_modules:
            gllog.warn("Warning: skipped OpenGL probing,")
            gllog.warn(" found %i blacklisted kernel module%s:",
                       len(blacklisted_kernel_modules), engs(blacklisted_kernel_modules))
            gllog.warn(" %s", csv(blacklisted_kernel_modules))
            props["error"] = "VirtualBox guest detected: %s" % csv(blacklisted_kernel_modules)
        else:
            try:
                from subprocess import Popen, PIPE
                from xpra.platform.paths import get_xpra_command
                cmd = self.get_full_child_command(get_xpra_command()+["opengl", "--opengl=yes"])
                env = self.get_child_env()
                #we want the output so we can parse it:
                env["XPRA_REDIRECT_OUTPUT"] = "0"
                proc = Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=env)
                out,err = proc.communicate()
                gllog("out(%s)=%s", cmd, out)
                gllog("err(%s)=%s", cmd, err)
                if proc.returncode==0:
                    #parse output:
                    for line in out.splitlines():
                        parts = line.split(b"=")
                        if len(parts)!=2:
                            continue
                        k = bytestostr(parts[0].strip())
                        v = bytestostr(parts[1].strip())
                        props[k] = v
                    gllog("opengl props=%s", props)
                    if props:
                        gllog.info("OpenGL is supported on display '%s'", self.display_name)
                        renderer = props.get("renderer")
                        if renderer:
                            gllog.info(" using '%s' renderer", renderer)
                    else:
                        gllog.info("No OpenGL information available")
                else:
                    props["error-details"] = str(err).strip("\n\r")
                    error = "unknown error"
                    for x in str(err).splitlines():
                        if x.startswith("RuntimeError: "):
                            error = x[len("RuntimeError: "):]
                            break
                        if x.startswith("ImportError: "):
                            error = x[len("ImportError: "):]
                            break
                    props["error"] = error
                    log.warn("Warning: OpenGL support check failed:")
                    log.warn(" %s", error)
            except Exception as e:
                gllog("query_opengl()", exc_info=True)
                gllog.error("Error: OpenGL support check failed")
                gllog.error(" '%s'", e)
                props["error"] = str(e)
        gllog("OpenGL: %s", props)
        return props


    def get_caps(self, source) -> dict:
        root_w, root_h = self.get_root_window_size()
        caps = {
            "bell"          : self.bell,
            "cursors"       : self.cursors,
            "desktop_size"  : self._get_desktop_size_capability(source, root_w, root_h),
            }
        if self.opengl_props:
            caps["opengl"] = self.opengl_props
        return caps

    def get_info(self, _proto) -> dict:
        i = {
                "randr" : self.randr,
                "bell"  : self.bell,
                "cursors" : {
                    ""      : self.cursors,
                    "size"  : self.cursor_size,
                    },
                "double-click"  : {
                    "time"      : self.double_click_time,
                    "distance"  : self.double_click_distance,
                    },
                "dpi" : {
                    "default"   : self.default_dpi,
                    "value"     : self.dpi,
                    "x"         : self.xdpi,
                    "y"         : self.ydpi,
                    },
                "antialias" : self.antialias,
                }
        if self.opengl_props:
            i["opengl"] = self.opengl_props
        return {
            "display": i,
            }


    def _process_set_cursors(self, proto, packet):
        assert self.cursors, "cannot toggle send_cursors: the feature is disabled"
        ss = self.get_server_source(proto)
        if ss:
            ss.send_cursors = bool(packet[1])

    def _process_set_bell(self, proto, packet):
        assert self.bell, "cannot toggle send_bell: the feature is disabled"
        ss = self.get_server_source(proto)
        if ss:
            ss.send_bell = bool(packet[1])


    ######################################################################
    # display / screen / root window:
    def set_screen_geometry_attributes(self, w, h):
        #by default, use the screen as desktop area:
        self.set_desktop_geometry_attributes(w, h)

    def set_desktop_geometry_attributes(self, w, h):
        self.calculate_desktops()
        self.calculate_workarea(w, h)
        self.set_desktop_geometry(w, h)


    def parse_screen_info(self, ss):
        return self.do_parse_screen_info(ss, ss.desktop_size)

    def do_parse_screen_info(self, ss, desktop_size):
        log("do_parse_screen_info%s", (ss, desktop_size))
        dw, dh = None, None
        if desktop_size:
            try:
                dw, dh = desktop_size
                if not ss.screen_sizes:
                    log.info(" client root window size is %sx%s", dw, dh)
                else:
                    log.info(" client root window size is %sx%s with %s display%s:",
                             dw, dh, len(ss.screen_sizes), engs(ss.screen_sizes))
                    log_screen_sizes(dw, dh, ss.screen_sizes)
            except Exception:
                dw, dh = None, None
        sw, sh = self.configure_best_screen_size()
        log("configure_best_screen_size()=%s", (sw, sh))
        #we will tell the client about the size chosen in the hello we send back,
        #so record this size as the current server desktop size to avoid change notifications:
        ss.desktop_size_server = sw, sh
        #prefer desktop size, fallback to screen size:
        w = dw or sw
        h = dh or sh
        #clamp to max supported:
        maxw, maxh = self.get_max_screen_size()
        w = min(w, maxw)
        h = min(h, maxh)
        self.set_desktop_geometry_attributes(w, h)
        self.set_icc_profile()
        return w, h


    def set_icc_profile(self):
        log("set_icc_profile() not implemented")

    def reset_icc_profile(self):
        log("reset_icc_profile() not implemented")


    def _monitors_changed(self, screen):
        self.do_screen_changed(screen)

    def _screen_size_changed(self, screen):
        self.do_screen_changed(screen)

    def do_screen_changed(self, screen):
        log("do_screen_changed(%s)", screen)
        #randr has resized the screen, tell the client (if it supports it)
        w, h = screen.get_width(), screen.get_height()
        log("new screen dimensions: %ix%i", w, h)
        self.set_screen_geometry_attributes(w, h)
        self.idle_add(self.send_updated_screen_size)

    def get_root_window_size(self):
        raise NotImplementedError()

    def send_updated_screen_size(self):
        max_w, max_h = self.get_max_screen_size()
        root_w, root_h = self.get_root_window_size()
        root_w = min(root_w, max_w)
        root_h = min(root_h, max_h)
        count = 0
        for ss in self._server_sources.values():
            if ss.updated_desktop_size(root_w, root_h, max_w, max_h):
                count +=1
        if count>0:
            log.info("sent updated screen size to %s client%s: %sx%s (max %sx%s)",
                     count, engs(count), root_w, root_h, max_w, max_h)

    def get_max_screen_size(self):
        max_w, max_h = self.get_root_window_size()
        return max_w, max_h

    def _get_desktop_size_capability(self, server_source, root_w, root_h):
        client_size = server_source.desktop_size
        log("client resolution is %s, current server resolution is %sx%s", client_size, root_w, root_h)
        if not client_size:
            #client did not specify size, just return what we have
            return root_w, root_h
        client_w, client_h = client_size
        w = min(client_w, root_w)
        h = min(client_h, root_h)
        return w, h

    def configure_best_screen_size(self):
        root_w, root_h = self.get_root_window_size()
        return root_w, root_h

    def _process_desktop_size(self, proto, packet):
        log("new desktop size from %s: %s", proto, packet)
        width, height = packet[1:3]
        ss = self.get_server_source(proto)
        if ss is None:
            return
        ss.desktop_size = (width, height)
        if len(packet)>=10:
            #added in 0.16 for scaled client displays:
            xdpi, ydpi = packet[8:10]
            if xdpi!=self.xdpi or ydpi!=self.ydpi:
                self.xdpi, self.ydpi = xdpi, ydpi
                log("new dpi: %ix%i", self.xdpi, self.ydpi)
                self.dpi = iround((self.xdpi + self.ydpi)/2.0)
                self.dpi_changed()
        if len(packet)>=8:
            #added in 0.16 for scaled client displays:
            ss.desktop_size_unscaled = packet[6:8]
        if len(packet)>=6:
            desktops, desktop_names = packet[4:6]
            ss.set_desktops(desktops, desktop_names)
            self.calculate_desktops()
        if len(packet)>=4:
            ss.set_screen_sizes(packet[3])
        bigger = ss.screen_resize_bigger
        log("client requesting new size: %sx%s (bigger=%s)", width, height, bigger)
        self.set_screen_size(width, height, bigger)
        if len(packet)>=4:
            log.info("received updated display dimensions")
            log.info("client display size is %sx%s with %s screen%s:",
                     width, height, len(ss.screen_sizes), engs(ss.screen_sizes))
            log_screen_sizes(width, height, ss.screen_sizes)
            self.calculate_workarea(width, height)
        #ensures that DPI and antialias information gets reset:
        self.update_all_server_settings()

    def dpi_changed(self):
        pass

    def calculate_desktops(self):
        count = 1
        for ss in self._server_sources.values():
            if ss.desktops:
                count = max(count, ss.desktops)
        count = max(1, min(20, count))
        names = []
        for i in range(count):
            if i==0:
                name = "Main"
            else:
                name = "Desktop %i" % (i+1)
            for ss in self._server_sources.values():
                if ss.desktops and i<len(ss.desktop_names) and ss.desktop_names[i]:
                    dn = ss.desktop_names[i]
                    if isinstance(dn, str):
                        #newer clients send unicode
                        name = dn
                    else:
                        #older clients send byte strings:
                        try :
                            v = strtobytes(dn).decode("utf8")
                        except (UnicodeEncodeError, UnicodeDecodeError):
                            log.error("Error parsing '%s'", dn, exc_info=True)
                        else:
                            if v!="0" or i!=0:
                                name = v
            names.append(name)
        self.set_desktops(names)

    def set_desktops(self, names):
        pass

    def calculate_workarea(self, w, h):
        raise NotImplementedError()

    def set_workarea(self, workarea):
        pass


    ######################################################################
    # screenshots:
    def _process_screenshot(self, proto, _packet):
        packet = self.make_screenshot_packet()
        ss = self.get_server_source(proto)
        if packet and ss:
            ss.send(*packet)

    def make_screenshot_packet(self):
        try:
            return self.do_make_screenshot_packet()
        except Exception:
            log.error("make_screenshot_packet()", exc_info=True)
            return None

    def do_make_screenshot_packet(self):
        raise NotImplementedError("no screenshot capability in %s" % type(self))

    def send_screenshot(self, proto):
        #this is a screenshot request, handle it and disconnect
        try:
            packet = self.make_screenshot_packet()
            if not packet:
                self.send_disconnect(proto, "screenshot failed")
                return
            proto.send_now(packet)
            self.timeout_add(5*1000, self.send_disconnect, proto, "screenshot sent")
        except Exception as e:
            log.error("failed to capture screenshot", exc_info=True)
            self.send_disconnect(proto, "screenshot failed: %s" % e)


    def init_packet_handlers(self):
        self.add_packet_handlers({
            "set-cursors"   : self._process_set_cursors,
            "set-bell"      : self._process_set_bell,
            "desktop_size"  : self._process_desktop_size,
            "screenshot"    : self._process_screenshot,
            })
