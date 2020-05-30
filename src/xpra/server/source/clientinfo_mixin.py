# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2010-2020 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.util import std, typedict
from xpra.server.source.stub_source_mixin import StubSourceMixin
from xpra.os_util import platform_name
from xpra.log import Logger

log = Logger("server")


"""
Store information about the client.
"""
class ClientInfoMixin(StubSourceMixin):

    def cleanup(self):
        self.init_state()

    def init_state(self):
        self.uuid = ""
        self.session_id = ""
        self.machine_id = ""
        self.hostname = ""
        self.username = ""
        self.name = ""
        self.argv = ()
        self.sharing = False
        # client capabilities/options:
        self.client_setting_change = False
        self.client_type = None
        self.client_version = None
        self.client_revision= None
        self.client_bits = 0
        self.client_platform = None
        self.client_machine = None
        self.client_processor = None
        self.client_release = None
        self.client_linux_distribution = None
        self.client_proxy = False
        self.client_wm_name = None
        self.client_session_type = None
        self.client_session_type_full = None
        self.client_connection_data = {}
        self.client_opengl = {}
        self.proxy_hostname = None
        self.proxy_platform = None
        self.proxy_release = None
        self.proxy_version = None
        self.proxy_version = None

    def parse_client_caps(self, c : typedict):
        self.uuid = c.strget("uuid")
        self.session_id = c.strget("session-id")
        self.machine_id = c.strget("machine_id")
        self.hostname = c.strget("hostname")
        self.username = c.strget("username")
        self.name = c.strget("name")
        self.argv = c.strtupleget("argv")
        self.sharing = c.boolget("share")
        self.client_type = c.strget("client_type", "PyGTK")
        self.client_platform = c.strget("platform")
        self.client_machine = c.strget("platform.machine")
        self.client_processor = c.strget("platform.processor")
        self.client_release = c.strget("platform.sysrelease")
        self.client_linux_distribution = c.strtupleget("platform.linux_distribution")
        self.client_version = c.strget("version")
        self.client_revision = c.strget("build.revision")
        self.client_bits = c.intget("python.bits")
        self.client_proxy = c.boolget("proxy")
        self.client_wm_name = c.strget("wm_name")
        self.client_session_type = c.strget("session-type")
        self.client_session_type_full = c.strget("session-type.full", "")
        self.client_setting_change = c.boolget("setting-change")
        self.client_opengl = typedict(c.dictget("opengl") or {})
        self.proxy_hostname = c.strget("proxy.hostname")
        self.proxy_platform = c.strget("proxy.platform")
        self.proxy_release = c.strget("proxy.platform.sysrelease")
        self.proxy_version = c.strget("proxy.version")
        self.proxy_version = c.strget("proxy.build.version", self.proxy_version)
        log("client uuid %s", self.uuid)

    def get_connect_info(self) -> list:
        cinfo = []
        #client platform / version info:
        pinfo = ""
        if self.client_platform:
            pinfo = " %s" % platform_name(self.client_platform, self.client_linux_distribution or self.client_release)
        if self.client_session_type:
            pinfo += " %s" % self.client_session_type
        revinfo = ""
        if self.client_revision:
            revinfo="-r%s" % self.client_revision
        bitsstr = ""
        if self.client_bits:
            bitsstr = " %i-bit" % self.client_bits
        cinfo.append("%s%s client version %s%s%s" % (
            std(self.client_type), pinfo, std(self.client_version), std(revinfo), bitsstr)
        )
        #opengl info:
        if self.client_opengl:
            msg = "OpenGL is "
            if not self.client_opengl.boolget("enabled"):
                msg += "disabled"
            else:
                msg += "enabled"
                driver_info = self.client_opengl.strget("renderer") or self.client_opengl.strget("vendor")
                if driver_info:
                    msg += " with %s" % driver_info
            cinfo.append(msg)
        #connection info:
        msg = ""
        if self.hostname:
            msg += "connected from '%s'" % std(self.hostname)
        if self.username:
            msg += " as '%s'" % std(self.username)
            if self.name and self.name!=self.username:
                msg += " - '%s'" % std(self.name)
        if msg:
            cinfo.append(msg)
        #proxy info
        if self.client_proxy:
            msg = "via %s proxy version %s" % (
                platform_name(self.proxy_platform, self.proxy_release),
                std(self.proxy_version or "unknown")
                )
            if self.proxy_hostname:
                msg += " on '%s'" % std(self.proxy_hostname)
            cinfo.append(msg)
        return cinfo


    def get_info(self) -> dict:
        info = {
                "version"           : self.client_version or "unknown",
                "revision"          : self.client_revision or "unknown",
                "platform_name"     : platform_name(self.client_platform, self.client_release),
                "session-type"      : self.client_session_type or "",
                "session-type.full" : self.client_session_type_full or "",
                "session-id"        : self.session_id or "",
                "uuid"              : self.uuid or "",
                "hostname"          : self.hostname or "",
                "argv"              : self.argv or (),
                "sharing"           : bool(self.sharing),
                }

        def addattr(k, name):
            v = getattr(self, name)
            if v is not None:
                info[k] = v
        for x in ("type", "platform", "release", "machine", "processor", "proxy", "wm_name", "session_type"):
            addattr(x, "client_"+x)
        return info
