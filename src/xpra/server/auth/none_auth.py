# This file is part of Xpra.
# Copyright (C) 2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.server.auth.sys_auth_base import SysAuthenticator, init
from xpra.platform.info import get_username

assert init

class Authenticator(SysAuthenticator):

    def __init__(self, username, **kwargs):
        SysAuthenticator.__init__(self, username, **kwargs)
        self.salt = None
        self.pw = None
        self.username = get_username()

    def requires_challenge(self):
        return False

    def get_challenge(self):
        return None

    def get_password(self):
        return None

    def authenticate(self, challenge_response, client_salt):
        return True

    def __repr__(self):
        return "none"
