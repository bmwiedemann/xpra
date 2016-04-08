# This file is part of Xpra.
# Copyright (C) 2016 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.server.auth.sys_auth_base import SysAuthenticator, init

assert init


class Authenticator(SysAuthenticator):

    def __init__(self, username, **kwargs):
        print("kwargs=%s" % (kwargs, ))
        SysAuthenticator.__init__(self, username)
        self.value = kwargs.get("value")
        self.authenticate = self.authenticate_hmac

    def __repr__(self):
        return "password"

    def get_challenge(self):
        return SysAuthenticator.get_challenge(self, mac="hmac")

    def get_password(self):
        return self.value
