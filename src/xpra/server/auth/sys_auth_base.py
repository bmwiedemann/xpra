# This file is part of Xpra.
# Copyright (C) 2013-2016 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import hmac, hashlib, binascii

from xpra.platform.dotxpra import DotXpra
from xpra.util import xor
from xpra.net.crypto import get_salt
from xpra.os_util import strtobytes
from xpra.log import Logger
log = Logger("auth")


socket_dir = None
socket_dirs = None
def init(opts):
    global socket_dir, socket_dirs
    socket_dir = opts.socket_dir
    socket_dirs = opts.socket_dirs


class SysAuthenticator(object):
    def __init__(self, username, **kwargs):
        self.username = username
        self.salt = None
        try:
            import pwd
            self.pw = pwd.getpwnam(username)
        except:
            self.pw = None
        if kwargs:
            log.warn("Warning: unused keyword arguments for %s authentication:", self)
            log.warn(" %s", kwargs)

    def get_uid(self):
        if self.pw is None:
            raise Exception("username '%s' not found" % self.username)
        return self.pw.pw_uid

    def get_gid(self):
        if self.pw is None:
            raise Exception("username '%s' not found" % self.username)
        return self.pw.pw_gid

    def requires_challenge(self):
        return True

    def get_challenge(self, mac="xor"):
        if self.salt is not None:
            log.error("challenge already sent!")
            return None
        self.salt = get_salt()
        #we need the raw password, so tell the client to use "xor":
        return self.salt, mac

    def get_password(self):
        return None

    def check(self, password):
        raise NotImplementedError()

    def authenticate(self, challenge_response, client_salt):
        #this will call check(password)
        return self.authenticate_check(challenge_response, client_salt)

    def authenticate_check(self, challenge_response, client_salt):
        if self.salt is None:
            log.error("Error: illegal challenge response received - salt cleared or unset")
            return False
        if client_salt is None:
            salt = self.salt
        else:
            salt = xor(self.salt, client_salt)
        self.salt = None
        password = xor(challenge_response, salt)
        #warning: enabling logging here would log the actual system password!
        #log("authenticate(%s) password=%s", challenge_response, password)
        #verify login:
        try :
            if not self.check(password):
                return False
        except Exception as e:
            log.error("Error in %s authentication checks:", self)
            log.error(" %s", e)
            return False
        return True

    def authenticate_hmac(self, challenge_response, client_salt):
        if not self.salt:
            log.error("Error: illegal challenge response received - salt cleared or unset")
            return None
        #ensure this salt does not get re-used:
        if client_salt is None:
            salt = self.salt
        else:
            salt = xor(self.salt, client_salt)
        self.salt = None
        password = self.get_password()
        if not password:
            log.error("Error: %s authentication failed", self)
            log.error(" no password defined for '%s'", self.username)
            return False
        verify = hmac.HMAC(strtobytes(password), strtobytes(salt), digestmod=hashlib.md5).hexdigest()
        log("authenticate(%s) password=%s, hex(salt)=%s, hash=%s", challenge_response, password, binascii.hexlify(strtobytes(salt)), verify)
        if hasattr(hmac, "compare_digest"):
            eq = hmac.compare_digest(verify, challenge_response)
        else:
            eq = verify==challenge_response
        if not eq:
            log("expected '%s' but got '%s'", verify, challenge_response)
            log.error("Error: hmac password challenge for '%s' does not match", self.username)
            return False
        return True

    def get_sessions(self):
        uid = self.get_uid()
        gid = self.get_gid()
        log("%s.get_sessions() uid=%i, gid=%i", self, uid, gid)
        try:
            sockdir = DotXpra(socket_dir, socket_dirs, actual_username=self.username)
            results = sockdir.sockets(check_uid=uid)
            displays = []
            for state, display in results:
                if state==DotXpra.LIVE and display not in displays:
                    displays.append(display)
            log("sockdir=%s, results=%s, displays=%s", sockdir, results, displays)
        except Exception as e:
            log.error("Error: cannot get socket directory for '%s':", self.username)
            log.error(" %s", e)
            displays = []
        v = uid, gid, displays, {}, {}
        log("%s.get_sessions()=%s", self, v)
        return v
