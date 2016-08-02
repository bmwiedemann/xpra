# This file is part of Xpra.
# Copyright (C) 2013-2016 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

#authentication from a file containing just the password

import os.path

from xpra.os_util import get_hex_uuid, strtobytes
from xpra.server.auth.sys_auth_base import SysAuthenticator
from xpra.log import Logger
log = Logger("auth")


#legacy interface: this is to inject the "--password-file=" option
#this is shared by all instances
password_file = None
def init(opts):
    global password_file
    password_file = opts.password_file


class FileAuthenticatorBase(SysAuthenticator):
    def __init__(self, username, **kwargs):
        SysAuthenticator.__init__(self, username)
        self.password_filename = kwargs.get("filename", password_file)
        self.password_filedata = None
        self.password_filetime = None
        self.authenticate = self.authenticate_hmac

    def requires_challenge(self):
        return True

    def get_challenge(self):
        if self.salt is not None:
            log.error("challenge already sent!")
            if self.salt is not False:
                self.salt = False
            return None
        self.salt = get_hex_uuid()+get_hex_uuid()
        #this authenticator can use the safer "hmac" digest:
        return self.salt, "hmac"

    def get_password(self):
        file_data = self.load_password_file()
        if file_data is None:
            return None
        return strtobytes(file_data)

    def parse_filedata(self, data):
        return data

    def load_password_file(self):
        if not self.password_filename:
            return None
        if not os.path.exists(self.password_filename):
            log.error("Error: password file '%s' is missing", self.password_filename)
            self.password_filedata = None
        else:
            ptime = self.stat_password_filetime()
            if self.password_filedata is None or ptime!=self.password_filetime:
                self.password_filetime = None
                self.password_filedata = None
                try:
                    with open(self.password_filename, mode='rb') as f:
                        data = f.read()
                    log("loaded %s bytes from '%s'", len(data), self.password_filename)
                    self.password_filedata = self.parse_filedata(data)
                    self.password_filetime = ptime
                except Exception as e:
                    log.error("Error reading password data from '%s':", self.password_filename, exc_info=True)
                    log.error(" %s", e)
                    self.password_filedata = None
        return self.password_filedata

    def stat_password_filetime(self):
        try:
            return os.stat(self.password_filename).st_mtime
        except Exception as e:
            log.error("Error accessing time of password file '%s'", self.password_filename)
            log.error(" %s", e)
            return 0
