# This file is part of Xpra.
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2011-2017 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os

from xpra.os_util import get_util_logger, osexpand
from xpra.platform.dotxpra_common import LIVE, DEAD, UNKNOWN, INACCESSIBLE

DISPLAY_PREFIX = ""

PIPE_PREFIX = "Xpra\\"
PIPE_ROOT = "\\\\"
PIPE_PATH = "%s.\\pipe\\" % PIPE_ROOT


def norm_makepath(dirpath, name):
    return PIPE_PATH+PIPE_PREFIX+name.lstrip(":")


class DotXpra:
    def __init__(self, sockdir=None, sockdirs=(), actual_username="", *_args, **_kwargs):
        self.username = actual_username

    def osexpand(self, v):
        return osexpand(v, self.username)

    def mksockdir(self, d):
        #socket-dir is not used by the win32 shadow server
        pass


    def displays(self, check_uid=0, matching_state=None):
        return tuple(self.get_all_namedpipes().keys())


    def norm_socket_paths(self, local_display_name):
        return [self.socket_path(local_display_name)]


    def socket_path(self, local_display_name):
        return norm_makepath(None, local_display_name)

    LIVE = LIVE
    DEAD = DEAD
    UNKNOWN = UNKNOWN
    INACCESSIBLE = INACCESSIBLE

    def get_display_state(self, display):
        return self.get_server_state(PIPE_PREFIX+display)

    def get_server_state(self, sockpath, _timeout=5):
        full_path = PIPE_PATH+sockpath
        if os.path.exists(full_path):
            return self.LIVE
        return self.DEAD

    def socket_paths(self, check_uid=0, matching_state=None, matching_display=None):
        return self.get_all_namedpipes().values()

    #this is imported by winswitch, so we can't change the method signature
    def sockets(self, check_uid=0, matching_state=None):
        #flatten the dictionnary into a list:
        return self.get_all_namedpipes().items()

    #find the matching sockets, and return:
    #(state, local_display, sockpath)
    def socket_details(self, check_uid=0, matching_state=None, matching_display=None):
        np = self.get_all_namedpipes()
        if not np:
            return {}
        return {PIPE_PREFIX.rstrip("\\"): [(LIVE, display, pipe_name) for display, pipe_name in np.items()]}

    def get_all_namedpipes(self):
        log = get_util_logger()
        xpra_pipes = {}
        non_xpra = []
        for pipe_name in os.listdir(PIPE_PATH):
            if not pipe_name.startswith(PIPE_PREFIX):
                non_xpra.append(pipe_name)
                continue
            name = pipe_name[len(PIPE_PREFIX):]
            #found an xpra pipe
            #FIXME: filter using matching_display?
            xpra_pipes[name] = pipe_name
            log("found xpra pipe: %s", pipe_name)
        log("found %i non-xpra pipes: %s", len(non_xpra), non_xpra)
        log("get_all_namedpipes()=%s", xpra_pipes)
        return xpra_pipes
