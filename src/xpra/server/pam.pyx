# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2016 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

#!python
#cython: boundscheck=False, wraparound=False, cdivision=True

import os
import time

from xpra.log import Logger
log = Logger("util", "auth")

from xpra.os_util import strtobytes
from posix.types cimport gid_t, pid_t, off_t, uid_t

cdef extern from "errno.h" nogil:
    int errno

cdef extern from "pwd.h":
    struct passwd:
        char   *pw_name         #username
        char   *pw_passwd       #user password
        uid_t   pw_uid          #user ID
        gid_t   pw_gid          #group ID
        char   *pw_gecos        #user information
        char   *pw_dir          #home directory
        char   *pw_shell        #shell program
    passwd *getpwuid(uid_t uid)

cdef extern from "pam_misc.h":
    ctypedef struct pam_handle_t:
        pass
    void misc_conv(int num_msg, const pam_message **msgm, pam_response **response, void *appdata_ptr)


cdef extern from "pam_appl.h":
    int PAM_SUCCESS
    struct pam_conv:
        void *conv
        #int (*conv)(int num_msg, const pam_message **msg, pam_response **resp, void *appdata_ptr)
        void *appdata_ptr
    struct pam_message:
        pass
    struct pam_response:
        pass
    struct pam_xauth_data:
        int namelen
        char *name
        int datalen
        char *data

    const char *pam_strerror(pam_handle_t *pamh, int errnum)
    int pam_start(const char *service_name, const char *user, const pam_conv *pam_conversation, pam_handle_t **pamh)
    int pam_open_session(pam_handle_t *pamh, int flags)
    int pam_close_session(pam_handle_t *pamh, int flags)
    int pam_end(pam_handle_t *pamh, int pam_status)
    int pam_putenv(pam_handle_t *pamh, const char *name_value)
    int pam_set_item(pam_handle_t *pamh, int item_type, const void *item)

    int PAM_SERVICE             # The service name
    int PAM_USER                # The user name
    int PAM_TTY                 # The tty name
    int PAM_RHOST               # The remote host name
    int PAM_CONV                # The pam_conv structure
    int PAM_AUTHTOK             # The authentication token (password)
    int PAM_OLDAUTHTOK          # The old authentication token
    int PAM_RUSER               # The remote user name
    int PAM_USER_PROMPT         # the prompt for getting a username
    int PAM_FAIL_DELAY          # app supplied function to override failure
    int PAM_XDISPLAY            # X display name
    int PAM_XAUTHDATA           # X server authentication data
    int PAM_AUTHTOK_TYPE        # The type for pam_get_authtok


PAM_ERR_STR = {PAM_SUCCESS : "success"}

PAM_ITEMS = {
    "SERVICE"       : PAM_SERVICE,
    "USER"          : PAM_USER,
    "TTY"           : PAM_TTY,
    "RHOST"         : PAM_RHOST,
    "CONV"          : PAM_CONV,
    "AUTHTOK"       : PAM_AUTHTOK,
    "OLDAUTHTOK"    : PAM_OLDAUTHTOK,
    "RUSER"         : PAM_RUSER,
    "USER_PROMPT"   : PAM_USER_PROMPT,
    "FAIL_DELAY"    : PAM_FAIL_DELAY,
    "XDISPLAY"      : PAM_XDISPLAY,
    "XAUTHDATA"     : PAM_XAUTHDATA,
    "AUTHTOK_TYPE"  : PAM_AUTHTOK_TYPE,
    }


cdef pam_handle_t*   pam_handle = NULL

def pam_open(service_name="xpra", env=None, items=None):
    global pam_handle
    cdef passwd *passwd_struct
    cdef pam_conv conv
    cdef int r
    cdef const void* item
    cdef pam_xauth_data xauth_data

    if pam_handle!=NULL:
        log.error("Error: cannot open the pam session more than once!")
        return False

    passwd_struct = getpwuid(os.geteuid())
    if passwd_struct==NULL:
        try:
            estr = os.strerror(errno)
        except ValueError:
            estr = str(errno)
        log.error("Error: cannot find pwd entry for euid %i", os.geteuid())
        log.error(" %s", estr)
        return False

    conv.conv = <void*> misc_conv
    conv.appdata_ptr = NULL;
    r = pam_start(strtobytes(service_name), strtobytes(passwd_struct.pw_name), &conv, &pam_handle)
    log("pam_start: %s", PAM_ERR_STR.get(r, r))
    if r!=PAM_SUCCESS:
        pam_handle = NULL
        log.error("Error: pam_start failed:")
        log.error(" %s", pam_strerror(pam_handle, r))
        return False

    if env:
        for k,v in env.items():
            name_value = "%s=%s\0" % (k, v)
            r = pam_putenv(pam_handle, strtobytes(name_value))
            if r!=PAM_SUCCESS:
                log.error("Error %i: failed to add '%s' to pam environment", r, name_value)
            else:
                log("pam_putenv: %s", name_value)

    if items:
        from ctypes import addressof, create_string_buffer
        for k,v in items.items():
            v = strtobytes(v)
            item_type = PAM_ITEMS.get(k.upper())
            if item_type is None or item_type in (PAM_CONV, PAM_FAIL_DELAY):
                log.error("Error: invalid pam item '%s'", k)
                continue
            elif item_type==PAM_XAUTHDATA:
                method = "MIT-MAGIC-COOKIE-1\0"
                xauth_data.namelen = len("MIT-MAGIC-COOKIE-1")
                xauth_data.name = method
                s = v+b"\0"
                xauth_data.datalen = len(v)
                xauth_data.data = s
                item = <const void*> &xauth_data
            else:
                s = create_string_buffer(v)
                l = addressof(s)
                item = <const void*> l
            r = pam_set_item(pam_handle, item_type, item)
            if r!=PAM_SUCCESS:
                log.error("Error %i: failed to set pam item '%s' to '%s'", r, k, v)
            else:
                log("pam_set_item: %s=%s", k, v)

    r = pam_open_session(pam_handle, 0)
    log("pam_open_session: %s", PAM_ERR_STR.get(r, r))
    if r!=PAM_SUCCESS:
        pam_handle = NULL
        log.error("Error: pam_open_session failed:")
        log.error(" %s", pam_strerror(pam_handle, r))
        return False
    return True

def pam_close():
    global pam_handle
    if pam_handle==NULL:
        log.error("Error: no pam session to close!")
        return False

    cdef int r = pam_close_session(pam_handle, 0)
    log("pam_close_session: %s", PAM_ERR_STR.get(r, r))
    if r!=PAM_SUCCESS:
        pam_handle = NULL
        log.error("Error: failed to close the pam session:")
        log.error(" %s", pam_strerror(pam_handle, r))
        return False

    r = pam_end(pam_handle, r)
    log("pam_end: %s", PAM_ERR_STR.get(r, r))
    if r!=PAM_SUCCESS:
        log.error("Error: pam_end '%s'", pam_strerror(pam_handle, r))
        return False
    return True
