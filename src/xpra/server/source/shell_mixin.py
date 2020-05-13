# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2020 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import io
from contextlib import redirect_stdout, redirect_stderr

from xpra.util import typedict
from xpra.server.source.stub_source_mixin import StubSourceMixin
from xpra.log import Logger

log = Logger("exec")


class ShellMixin(StubSourceMixin):

    @classmethod
    def is_needed(cls, caps : typedict) -> bool:
        return caps.boolget("shell", False)

    def __init__(self, *_args):
        self._server = None
        self.saved_logging_handler = None
        self.log_records = []
        self.log_thread = None

    def init_from(self, _protocol, server):
        self._server = server

    def get_caps(self) -> dict:
        return {"shell" : True}

    def get_info(self) -> dict:
        info = {"shell" : True}
        return info

    def shell_exec(self, code):
        log("shell_exec(%r)", code)
        try:
            _globals = {
                "connection" : self,
                "server"    : self._server,
                "log"       : log,
                }
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout):
                with redirect_stderr(stderr):
                    exec(code, _globals, {})
            log("stdout=%r", stdout.getvalue())
            log("stderr=%r", stderr.getvalue())
            self.send("shell-reply", 1, stdout.getvalue().encode("utf8"))
            self.send("shell-reply", 2, stderr.getvalue().encode("utf8"))
        except Exception as e:
            log("shell_exec(..)", exc_info=True)
            log.error("Error running '%r':", code)
            log.error(" %s", e)
            self.send("shell-reply", 1, str(e))
