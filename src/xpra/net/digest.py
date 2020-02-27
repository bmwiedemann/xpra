# This file is part of Xpra.
# Copyright (C) 2011-2018 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import hmac
import hashlib

from xpra.util import csv
from xpra.log import Logger
from xpra.os_util import strtobytes, memoryview_to_bytes, hexstr

log = Logger("network", "crypto")

BLACKLISTED_HASHES = ("sha1", "md5")


def get_digests():
    digests = ["xor"]
    digests += ["hmac+%s" % x for x in tuple(reversed(sorted(hashlib.algorithms_available)))
                if not x.startswith("shake_") and x not in BLACKLISTED_HASHES
                and getattr(hashlib, x, None) is not None]
    try:
        from xpra.net import d3des
        assert d3des
        digests.append("des")
    except (ImportError, TypeError):    # pragma: no cover
        pass
    return digests

def get_digest_module(digest : str):
    log("get_digest_module(%s)", digest)
    if not digest or not digest.startswith("hmac"):
        return None
    try:
        digest_module = digest.split("+")[1]        #ie: "hmac+sha512" -> "sha512"
    except IndexError:
        return None
    try:
        return getattr(hashlib, digest_module)
    except AttributeError as e:
        log("no '%s' attribute in hashlib: %s", digest_module, e)
        return None

def choose_digest(options) -> str:
    assert len(options)>0, "no digest options"
    log("choose_digest(%s)", options)
    #prefer stronger hashes:
    for h in ("sha512", "sha384", "sha256", "sha224"):
        hname = "hmac+%s" % h
        if hname in options:
            return hname
    if "xor" in options:
        return "xor"
    if "des" in options:
        return "des"
    raise ValueError("no known digest options found in '%s'" % csv(options))

def gendigest(digest, password, salt):
    assert password and salt
    salt = memoryview_to_bytes(salt)
    password = strtobytes(password)
    if digest=="des":
        from xpra.net.d3des import generate_response
        password = password.ljust(8, b"\x00")[:8]
        salt = salt.ljust(16, b"\x00")[:16]
        v = generate_response(password, salt)
        return hexstr(v)
    if digest in ("xor", "kerberos", "gss"):
        #kerberos and gss use xor because we need to use the actual token
        #at the other end
        salt = salt.ljust(len(password), b"\x00")[:len(password)]
        from xpra.codecs.xor.cyxor import xor_str           #@UnresolvedImport
        v = xor_str(password, salt)
        return memoryview_to_bytes(v)
    digestmod = get_digest_module(digest)
    if not digestmod:
        log("invalid digest module '%s'", digest)
        return None
        #warn_server_and_exit(EXIT_UNSUPPORTED, "server requested digest '%s' but it is not supported" % digest, "invalid digest")
    v = hmac.HMAC(password, salt, digestmod=digestmod).hexdigest()
    return v

def verify_digest(digest, password, salt, challenge_response):
    if not password or not salt or not challenge_response:
        return False
    verify = gendigest(digest, password, salt)
    if not hmac.compare_digest(verify, challenge_response):
        log("expected '%s' but got '%s'", verify, challenge_response)
        return False
    return True


def get_salt(l=64):
    #too short: we would not feed enough random data to HMAC
    assert l>=32, "salt is too short: only %i bytes" % l
    #too long: limit the amount of random data we request from the system
    assert l<1024, "salt is too long: %i bytes" % l
    #all server versions support a client salt,
    #they also tell us which digest to use:
    return os.urandom(l)
