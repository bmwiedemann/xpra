# This file is part of Xpra.
# Copyright (C) 2010 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os.path
import sys
import site

USE_RUNTIME_DIR = os.environ.get("XPRA_USE_RUNTIME_DIR", "1")


USE_RUNTIME_LOG_DIR = os.environ.get("XPRA_USE_RUNTIME_LOG_DIR", USE_RUNTIME_DIR)=="1"
USE_RUNTIME_BIN_DIR = os.environ.get("XPRA_USE_RUNTIME_BIN_DIR", USE_RUNTIME_DIR)=="1"
USE_RUNTIME_SOCKET_DIR = os.environ.get("XPRA_USE_RUNTIME_SOCKET_DIR", USE_RUNTIME_DIR)=="1"


def do_get_install_prefix():
    #special case for "user" installations, ie:
    #$HOME/.local/lib/python2.7/site-packages/xpra/platform/paths.py
    try:
        base = site.getuserbase()
    except:
        base = site.USER_BASE
    if __file__.startswith(base):
        return base
    return sys.prefix

def do_get_resources_dir():
    #is there a better/cleaner way?
    from xpra.platform.paths import get_install_prefix
    options = [get_install_prefix(),
               sys.exec_prefix,
               "/usr",
               "/usr/local"]
    for x in options:
        p = os.path.join(x, "share", "xpra")
        if os.path.exists(p) and os.path.isdir(p):
            return p
    try:
        # test for a local installation path (run from source tree):
        local_share_path = os.path.abspath(os.path.join(os.path.dirname(sys.argv[0]), "..", "share", "xpra"))
        if os.path.exists(local_share_path) and os.path.isdir(local_share_path):
            return local_share_path
    except:
        pass
    return os.getcwd()

def do_get_app_dir():
    from xpra.platform.paths import get_resources_dir
    return get_resources_dir()

def do_get_icon_dir():
    from xpra.platform.paths import get_app_dir
    return os.path.join(get_app_dir(), "icons")

def do_get_script_bin_dirs():
    #versions before 0.17 only had "~/.xpra/run-xpra"
    script_bin_dirs = []
    runtime_dir = _get_xpra_runtime_dir()
    if USE_RUNTIME_BIN_DIR and runtime_dir:
        script_bin_dirs.append(runtime_dir)
    script_bin_dirs.append("~/.xpra")
    return script_bin_dirs


def _get_runtime_dir():
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        #replace uid with the string "$UID"
        head, tail = list(os.path.split(runtime_dir))
        try:
            assert int(tail)
            runtime_dir = os.path.join(head, "$UID")
        except (ValueError, AssertionError):
            pass
    elif os.path.exists("/var/run/user") and os.path.isdir("/var/run/user"):
        runtime_dir = "/var/run/user/$UID"
    return runtime_dir

def _get_xpra_runtime_dir():
    runtime_dir = _get_runtime_dir()
    if not runtime_dir:
        return None
    return os.path.join(runtime_dir, "xpra")

def do_get_socket_dirs():
    SOCKET_DIRS = []
    runtime_dir = _get_xpra_runtime_dir()
    if USE_RUNTIME_SOCKET_DIR and runtime_dir:
        #private, per user: /run/user/1000/xpra
        SOCKET_DIRS.append(runtime_dir)
    SOCKET_DIRS.append("~/.xpra")   #the old default path
    #for shared sockets (the 'xpra' group should own this directory):
    if os.path.exists("/var/run/xpra") and os.access('/var/run/xpra', os.W_OK):
        SOCKET_DIRS.append("/var/run/xpra")
    return SOCKET_DIRS

def do_get_default_log_dir():
    runtime_dir = _get_xpra_runtime_dir()
    if USE_RUNTIME_LOG_DIR and runtime_dir:
        return runtime_dir
    return "~/.xpra"
