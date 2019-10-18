# This file is part of Xpra.
# Copyright (C) 2017-2019 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
from subprocess import Popen
from gi.repository import GLib

from xpra.util import envint
from xpra.os_util import OSX
from xpra.child_reaper import getChildReaper
from xpra.server.auth.sys_auth_base import SysAuthenticator, log
from xpra.platform.features import EXECUTABLE_EXTENSION

TIMEOUT = envint("XPRA_EXEC_AUTH_TIMEOUT", 600)


class Authenticator(SysAuthenticator):

    def __init__(self, username, **kwargs):
        log("exec.Authenticator(%s, %s)", username, kwargs)
        self.command = kwargs.pop("command", "")
        self.timeout = kwargs.pop("timeout", TIMEOUT)
        self.timer = None
        self.proc = None
        self.timeout_event = False
        if not self.command:
            #try to find the default auth_dialog executable:
            from xpra.platform.paths import get_libexec_dir
            libexec = get_libexec_dir()
            xpralibexec = os.path.join(libexec, "xpra")
            log("libexec=%s, xpralibexec=%s", libexec, xpralibexec)
            if os.path.exists(xpralibexec) and os.path.isdir(xpralibexec):
                libexec = xpralibexec
            auth_dialog = os.path.join(libexec, "auth_dialog")
            if EXECUTABLE_EXTENSION:
                #ie: add ".exe" on MS Windows
                auth_dialog += ".%s" % EXECUTABLE_EXTENSION
            log("auth_dialog=%s", auth_dialog)
            if os.path.exists(auth_dialog):
                self.command = auth_dialog
        assert self.command, "exec authentication module is not configured correctly: no command specified"
        connection = kwargs.get("connection")
        log("exec connection info: %s", connection)
        assert connection, "connection object is missing"
        self.connection_str = str(connection)
        super().__init__(username, **kwargs)

    def requires_challenge(self) -> bool:
        return False

    def authenticate(self, _challenge_response=None, _client_salt=None) -> bool:
        info = "Connection request from %s" % self.connection_str
        cmd = [self.command, info, str(self.timeout)]
        proc = Popen(cmd)
        self.proc = proc
        log("authenticate(..) Popen(%s)=%s", cmd, proc)
        #if required, make sure we kill the command when it times out:
        if self.timeout>0:
            self.timer = GLib.timeout_add(self.timeout*1000, self.command_timedout)
            if not OSX:
                #python on macos may set a 0 returncode when we use poll()
                #so we cannot use the ChildReaper on macos,
                #and we can't cancel the timer
                getChildReaper().add_process(proc, "exec auth", cmd, True, True, self.command_ended)
        v = proc.wait()
        log("authenticate(..) returncode(%s)=%s", cmd, v)
        if self.timeout_event:
            return False
        return v==0

    def command_ended(self, *args):
        t = self.timer
        log("exec auth.command_ended%s timer=%s", args, t)
        if t:
            self.timer = None
            GLib.source_remove(t)

    def command_timedout(self):
        proc = self.proc
        log("exec auth.command_timedout() proc=%s", proc)
        self.timeout_event = True
        self.timer = None
        if proc:
            try:
                proc.terminate()
            except:
                log("error trying to terminate exec auth process %s", proc, exc_info=True)

    def __repr__(self):
        return "exec"
