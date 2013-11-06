#!/usr/bin/env python

# This file is part of Xpra.
# Copyright (C) 2011-2013 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from datetime import date
import subprocess
import getpass
import socket
import platform
import os.path
import re
import sys


def update_properties(props, filename):
    eprops = get_properties(filename)
    for key,value in props.items():
        set_prop(eprops, key, value)
    save_properties(eprops, filename)

def save_properties(props, filename):
    if os.path.exists(filename):
        try:
            os.unlink(filename)
        except:
            print("WARNING: failed to delete %s" % filename)
    f = open(filename, mode='w')
    for name,value in props.items():
        s = str(value).replace("'", "\\'")
        f.write("%s='%s'\n" % (name, s))
    f.close()
    print("updated %s with %s" % (filename, props))

def get_properties(filename):
    props = dict()
    if os.path.exists(filename):
        try:
            f = open(filename, "rU")
            for line in f:
                s = line.strip()
                if len(s)==0:
                    continue
                if s[0] in ('!', '#'):
                    continue
                parts = s.split("=", 1)
                name = parts[0]
                value = parts[1]
                if value[0]!="'" or value[-1]!="'":
                    continue
                props[name]= value[1:-1]
        finally:
            f.close()
    return props


def get_cpuinfo():
    if platform.uname()[5]:
        return platform.uname()[5]
    try:
        if os.path.exists("/proc/cpuinfo"):
            f = open("/proc/cpuinfo", "rU")
            for line in f:
                if line.startswith("model name"):
                    return line.split(": ")[1].replace("\n", "").replace("\r", "")
            f.close()
    finally:
        pass
    return "unknown"

def get_compiler_info():
    if sys.platform.startswith("win"):
        cmd = "cl"
    else:
        cmd = "gcc --version"
    proc = subprocess.Popen(cmd, stdin=None, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
    stdout, _ = proc.communicate()
    #print("get_compiler_info() %s returned %s" % (cmd, proc.returncode))
    #print("get_compiler_info() stdout(%s)=%s" % (cmd, stdout))
    #print("get_compiler_info() stderr(%s)=%s" % (cmd, stderr))
    if proc.returncode!=0:
        print("'%s' failed with return code %s" % (cmd, proc.returncode))
        return  ""
    if not stdout:
        print("could not get GCC version information")
        return  ""
    out = stdout.decode('utf-8')
    return out.splitlines()[0]

def set_prop(props, key, value):
    if value!="unknown" or props.get(key) is None:
        props[key] = value


def record_build_info(is_build=True):
    BUILD_INFO_FILE = "./xpra/build_info.py"

    props = get_properties(BUILD_INFO_FILE)
    if is_build:
        set_prop(props, "BUILT_BY", getpass.getuser())
        set_prop(props, "BUILT_ON", socket.gethostname())
        set_prop(props, "BUILD_DATE", date.today().isoformat())
        set_prop(props, "BUILD_CPU", get_cpuinfo())
        set_prop(props, "BUILD_BIT", platform.architecture()[0])
        try:
            from Cython import __version__ as cython_version
        except:
            cython_version = "unknown"
        set_prop(props, "CYTHON_VERSION", cython_version)
        set_prop(props, "COMPILER_INFO", get_compiler_info())
        set_prop(props, "RELEASE_BUILD", not bool(os.environ.get("BETA", "")))
    save_properties(props, BUILD_INFO_FILE)


def load_ignored_changed_files():
    ignored = []
    f = None
    try:
        f = open("./ignored_changed_files.txt", "rU")
        for line in f:
            s = line.strip()
            if len(s)==0:
                continue
            if s[0] in ('!', '#'):
                continue
            ignored.append(s)
    finally:
        if f:
            f.close()
    return ignored

def get_svn_props():
    props = {
                "REVISION" : "unknown",
                "LOCAL_MODIFICATIONS" : "unknown"
            }
    #find revision:
    proc = subprocess.Popen("svnversion -n", stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    (out, _) = proc.communicate()
    if proc.returncode!=0:
        print("'svnversion -n' failed with return code %s" % proc.returncode)
        return  props
    if not out:
        print("could not get version information")
        return  props
    out = out.decode('utf-8')
    if out=="exported":
        print("svn repository information is missing ('exported')")
        return  props
    pos = out.find(":")
    if pos>=0:
        out = out[pos+1:]
    rev_str = ""
    for c in out:
        if c in "0123456789":
            rev_str += c
    if not rev_str:
        print("could not parse version information from string: %s (original version string: %s)" % (rev_str, out))
        return  props

    rev = int(rev_str)
    props["REVISION"] = rev
    #find number of local files modified:
    changes = 0
    proc = subprocess.Popen("svn status", stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    (out, _) = proc.communicate()
    if proc.poll()!=0:
        print("could not get status of local files")
        return  props

    lines = out.decode('utf-8').splitlines()
    for line in lines:
        if not line.startswith("M"):
            continue
        parts = line.split(" ", 1)
        if len(parts)!=2:
            continue
        filename = parts[1].strip()
        ignore = False
        for x in load_ignored_changed_files():
            #use a normalized path ("/") that does not interfere with regexp:
            norm_path = filename.replace(os.path.sep, "/")
            if norm_path==x:
                print("'%s' matches ignore list entry: '%s' exactly, not counting it as a modified file" % (filename, x))
                ignore = True
                break
            rstr = r"^%s$" % x.replace("*", ".*")
            regexp = re.compile(rstr)
            if regexp.match(norm_path):
                print("'%s' matches ignore list regexp: '%s', not counting it as a modified file" % (filename, x))
                ignore = True
                break
        if ignore:
            continue
        changes += 1
        print("WARNING: found modified file: %s" % filename)
    props["LOCAL_MODIFICATIONS"] = changes
    return props

SRC_INFO_FILE = "./xpra/src_info.py"
def record_src_info():
    update_properties(get_svn_props(), SRC_INFO_FILE)

def has_src_info():
    return os.path.exists(SRC_INFO_FILE) and os.path.isfile(SRC_INFO_FILE)

def main():
    record_build_info(True)


if __name__ == "__main__":
    main()
