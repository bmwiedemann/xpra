#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2011-2020 Antoine Martin <antoine@xpra.org>
# Copyright (C) 2008, 2009, 2010 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from collections import namedtuple

from xpra.log import Logger
from xpra.net.header import FLAGS_RENCODE, FLAGS_YAML, FLAGS_BENCODE, FLAGS_NOHEADER
from xpra.util import envbool

#all the encoders we know about, in best compatibility order:
ALL_ENCODERS = ("rencode", "bencode", "yaml", "none")
#order for performance:
PERFORMANCE_ORDER = ("rencode", "bencode", "yaml")

Encoding = namedtuple("Encoding", ["name", "flag", "version", "encode", "decode"])

ENCODERS = {}


def init_rencode():
    from rencode import dumps, loads, __version__
    def do_rencode(v):
        return dumps(v), FLAGS_RENCODE
    return Encoding("rencode", FLAGS_RENCODE, __version__, do_rencode, loads)

def init_bencode():
    from xpra.net.bencode import bencode, bdecode, __version__
    def do_bencode(v):
        return bencode(v), FLAGS_BENCODE
    def do_bdecode(data):
        packet, l = bdecode(data)
        assert len(data)==l, "expected %i bytes, but got %i" % (l, len(data))
        return packet
    return Encoding("bencode", FLAGS_BENCODE, __version__, do_bencode, do_bdecode)

def init_yaml():
    #json messes with strings and unicode (makes it unusable for us)
    from yaml import dump, safe_load, __version__
    def yaml_dump(v):
        return dump(v).encode("latin1"), FLAGS_YAML
    return Encoding("yaml", FLAGS_YAML, __version__, yaml_dump, safe_load)

def init_none():
    def encode(data):
        #just send data as a string for clients that don't understand xpra packet format:
        import codecs
        def b(x):
            if isinstance(x, bytes):
                return x
            return codecs.latin_1_encode(x)[0]
        return b(": ".join(str(x) for x in data)+"\n"), FLAGS_NOHEADER
    return Encoding("none", FLAGS_NOHEADER, 0, encode, None)


def init_all():
    for x in list(ALL_ENCODERS)+["none"]:
        if not envbool("XPRA_%s" % (x.upper()), True):
            continue
        fn = globals().get("init_%s" % x)
        try:
            e = fn()
            assert e
            ENCODERS[x] = e
        except (ImportError, AttributeError):
            logger = Logger("network", "protocol")
            logger.debug("no %s", x, exc_info=True)
init_all()


def get_packet_encoding_caps() -> dict:
    caps = {}
    for name in ALL_ENCODERS:
        d = caps.setdefault(name, {})
        e = ENCODERS.get(name)
        d[""] = e is not None
        if e is None:
            continue
        d["version"] = e.version
    return caps

def get_enabled_encoders(order=ALL_ENCODERS):
    return tuple(x for x in order if x in ENCODERS)


def get_encoder(e):
    assert e in ALL_ENCODERS, "invalid encoder name: %s" % e
    assert e in ENCODERS, "%s is not available" % e
    return ENCODERS[e].encode

def get_packet_encoding_type(protocol_flags) -> str:
    if protocol_flags & FLAGS_RENCODE:
        return "rencode"
    if protocol_flags & FLAGS_YAML:
        return "yaml"
    return "bencode"


def sanity_checks():
    if "rencode" not in ENCODERS:
        log = Logger("network", "protocol")
        log.warn("Warning: 'rencode' packet encoder not found")
        log.warn(" the other packet encoders are much slower")


class InvalidPacketEncodingException(Exception):
    pass


def pack_one_packet(packet):
    from xpra.net.header import pack_header
    ee = get_enabled_encoders()
    if ee:
        e = get_encoder(ee[0])
        data, flags = e(packet)
        return pack_header(flags, 0, 0, len(data))+data
    return str(packet)


def decode(data, protocol_flags):
    if isinstance(data, memoryview):
        data = data.tobytes()
    ptype = get_packet_encoding_type(protocol_flags)
    e = ENCODERS.get(ptype)
    if e:
        return e.decode(data)
    raise InvalidPacketEncodingException("%s decoder is not available" % ptype)


def main(): # pragma: no cover
    from xpra.util import print_nested_dict
    from xpra.platform import program_context
    with program_context("Packet Encoding", "Packet Encoding Info"):
        print_nested_dict(get_packet_encoding_caps())


if __name__ == "__main__":  # pragma: no cover
    main()
