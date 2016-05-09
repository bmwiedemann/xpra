#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2010 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2011-2015 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import inspect
import os
import sys


def valid_dir(path):
    try:
        return path and os.path.exists(path) and os.path.isdir(path)
    except:
        return False


#helpers to easily override using env vars:
def envaslist_or_delegate(env_name, impl):
    env_value = os.environ.get(env_name)
    if env_value is not None:
        return [env_value]
    return impl()
def env_or_delegate(env_name, impl):
    env_value = os.environ.get(env_name)
    if env_value is not None:
        return env_value
    return impl()


def get_install_prefix():
    return env_or_delegate("XPRA_INSTALL_PREFIX", do_get_install_prefix)
def do_get_install_prefix():
    return sys.prefix


def get_system_conf_dirs():
    return envaslist_or_delegate("XPRA_SYSCONF_DIRS", do_get_system_conf_dirs)
def do_get_system_conf_dirs():
    prefix = get_install_prefix()
    #the system wide configuration directory
    if prefix == '/usr':
        #default posix config location:
        return ['/etc/xpra']
    #hope the prefix is something like "/usr/local" or "$HOME/.local":
    return [prefix + '/etc/xpra/']

def get_user_conf_dirs():
    return envaslist_or_delegate("XPRA_USER_CONF_DIRS", do_get_user_conf_dirs)
def do_get_user_conf_dirs():
    #per-user configuration location:
    return ["~/.xpra"]

def get_default_conf_dirs():
    return envaslist_or_delegate("XPRA_DEFAULT_CONF_DIRS", do_get_default_conf_dirs)
def do_get_default_conf_dirs():
    #some platforms may also ship a default config with the application
    return []


def get_socket_dirs():
    return envaslist_or_delegate("XPRA_SOCKET_DIRS", do_get_socket_dirs)
def do_get_socket_dirs():
    return ["~/.xpra"]

def get_default_log_dir():
    return env_or_delegate("XPRA_LOG_DIR", do_get_default_log_dir)
def do_get_default_log_dir():
    return "~/.xpra"

def get_download_dir():
    return env_or_delegate("XPRA_DOWNLOAD_DIR", do_get_download_dir)
def do_get_download_dir():
    d = "~/Downloads"
    if not os.path.exists(os.path.expanduser(d)):
        return "~"
    return d

def get_script_bin_dirs():
    return envaslist_or_delegate("XPRA_SCRIPT_BIN_DIRS", do_get_script_bin_dirs)
def do_get_script_bin_dirs():
    return ["~/.xpra"]

def get_remote_run_xpra_scripts():
    return envaslist_or_delegate("XPRA_REMOTE_RUN_XPRA_SCRIPTS", do_get_remote_run_xpra_scripts)
def do_get_remote_run_xpra_scripts():
    return ["~/.xpra/run-xpra", "$XDG_RUNTIME_DIR/xpra/run-xpra", "xpra"]


#overriden in platform code:
def get_app_dir():
    return env_or_delegate("XPRA_APP_DIR", do_get_app_dir)
def do_get_app_dir():
    return default_get_app_dir()

def default_get_app_dir():
    if os.name=="posix":
        for prefix in [get_install_prefix(),
                       sys.exec_prefix,
                       "/usr",
                       "/usr/local"]:
            adir = os.path.join(prefix, "share", "xpra")
            if valid_dir(adir):
                return adir
    adir = os.path.dirname(inspect.getfile(sys._getframe(1)))
    if valid_dir(adir):
        return adir
    adir = os.path.dirname(sys.argv[0])
    if valid_dir(adir):
        return adir
    adir = os.getcwd()
    return adir       #tried our best, hope this works!

#may be overriden in platform code:
def get_resources_dir():
    return env_or_delegate("XPRA_RESOURCES_DIR", do_get_resources_dir)
def do_get_resources_dir():
    return get_app_dir()

#may be overriden in platform code:
def get_icon_dir():
    return env_or_delegate("XPRA_ICON_DIR", do_get_icon_dir)
def do_get_icon_dir():
    adir = get_app_dir()
    idir = os.path.join(adir, "icons")
    if valid_dir(idir):
        return idir
    for prefix in [sys.exec_prefix, "/usr", "/usr/local"]:
        idir = os.path.join(prefix, "icons")
        if os.path.exists(idir):
            return idir
    return adir     #better than nothing :(

def get_icon_filename(name):
    def err(*msg):
        """ log an error message and return None """
        from xpra.log import Logger
        log = Logger("icon")
        log.error(*msg)
        return None
    idir = get_icon_dir()
    if not idir:
        return err("cannot find icons directory!")
    filename = os.path.join(idir, name)
    if not os.path.exists(filename):
        return err("icon file %s does not exist", filename)
    if not os.path.isfile(filename):
        return err("%s is not a file!", filename)
    return filename

def get_icon(name):
    filename = get_icon_filename(name)
    if not filename:
        return    None
    from xpra.gtk_common.gtk_util import get_icon_from_file
    return get_icon_from_file(filename)


LICENSE_TEXT = None
def get_license_text(self):
    global LICENSE_TEXT
    if LICENSE_TEXT:
        return  LICENSE_TEXT
    filename = os.path.join(get_resources_dir(), 'COPYING')
    if os.path.exists(filename):
        try:
            if sys.version < '3':
                license_file = open(filename, mode='rb')
            else:
                license_file = open(filename, mode='r', encoding='ascii')
            LICENSE_TEXT = license_file.read()
        finally:
            license_file.close()
    if not LICENSE_TEXT:
        LICENSE_TEXT = "GPL version 2"
    return LICENSE_TEXT


def get_websockify_command():
    envvalue = os.environ.get("XPRA_WEBSOCKIFY_COMMAND")
    if envvalue:
        import shlex
        return shlex.split(envvalue)
    return do_get_websockify_command()
def do_get_websockify_command():
    return ["websockify"]


def get_sound_command():
    envvalue = os.environ.get("XPRA_SOUND_COMMAND")
    if envvalue:
        import shlex
        return shlex.split(envvalue)
    return do_get_sound_command()
def do_get_sound_command():
    return ["xpra"]


from xpra.platform import platform_import
platform_import(globals(), "paths", True,
                "do_get_resources_dir",
                "do_get_app_dir",
                "do_get_icon_dir")
platform_import(globals(), "paths", False,
                "do_get_websockify_command",
                "do_get_sound_command",
                "do_get_install_prefix",
                "do_get_default_conf_dirs",
                "do_get_system_conf_dirs",
                "do_get_user_conf_dirs",
                "do_get_socket_dirs",
                "do_get_default_log_dir",
                "do_get_download_dir",
                "do_get_script_bin_dirs")

def get_info():
    return {
            "install"           : {"prefix" : get_install_prefix()},
            "default_conf"      : {"dirs"   : get_default_conf_dirs()},
            "system_conf"       : {"dirs"   : get_system_conf_dirs()},
            "user_conf"         : {"dirs"   : get_user_conf_dirs()},
            "socket"            : {"dirs"   : get_socket_dirs()},
            "log"               : {"dir"    : get_default_log_dir()},
            "download"          : {"dir"    : get_download_dir()},
            "app"               : {"dir"    : get_app_dir()},
            "app"               : {"default" : {"dir"   : default_get_app_dir()}},
            "resources"         : get_resources_dir(),
            "icons"             : get_icon_dir(),
            "home"              : os.path.expanduser("~"),
            "sound_command"     : get_sound_command(),
            "websockify_command": get_websockify_command(),
            }


def main():
    if "-v" in sys.argv or "--verbose" in sys.argv:
        from xpra.log import add_debug_category
        add_debug_category("util")

    from xpra.util import print_nested_dict
    from xpra.platform import program_context
    with program_context("Path-Info", "Path Info"):
        print_nested_dict(get_info())


if __name__ == "__main__":
    main()
