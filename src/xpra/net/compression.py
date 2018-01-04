#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2011-2017 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008, 2009, 2010 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import sys

from xpra.log import Logger
log = Logger("network", "protocol")
from xpra.net.header import LZ4_FLAG, ZLIB_FLAG, LZO_FLAG


MAX_SIZE = 256*1024*1024


python_lz4_version = None
lz4_version = None
has_lz4 = False
def lz4_compress(packet, level):
    raise Exception("lz4 is not supported!")
try:
    import lz4
    from lz4 import VERSION as python_lz4_version   #@UnresolvedImport
    try:
        #using unicode for version numbers is dumb:
        python_lz4_version = python_lz4_version.encode("latin1")
    except:
        pass
    #newer versions moved the version information to lz4.version.version
    #breaking backwards compatibility!
    try:
        from lz4.version import version as lz4_version
    except ImportError as e:
        log("outdated version of python-lz4: %s", e)
        try:
            from lz4 import LZ4_VERSION as lz4_version   #@UnresolvedImport
        except Exception as e:
            log("really outdated version of python-lz4: %s", e)
    try:
        from lz4.block import compress, decompress
        LZ4_uncompress = decompress
        has_lz4 = True
        def lz4_compress(packet, level):
            flag = min(15, level) | LZ4_FLAG
            if level>=7:
                return flag, compress(packet, mode="high_compression", compression=level)
            elif level<=3:
                return flag, compress(packet, mode="fast", acceleration=8-level*2)
            return flag, compress(packet)
    except ImportError as e:
        log("outdated version of python-lz4: %s", e)
        #LZ4_compress_fast is 0.8 or later
        from lz4 import LZ4_compress, LZ4_uncompress, compressHC        #@UnresolvedImport
        if hasattr(lz4, "LZ4_compress_fast"):
            from lz4 import LZ4_compress_fast     #@UnresolvedImport
            has_lz4 = True
            def lz4_compress(packet, level):
                if level>=9:
                    return level | LZ4_FLAG, compressHC(packet)
                #clamp it: 0->17, 1->12, 2->7, 3->2, >=4->1
                if level<=2:
                    #clamp it: 0->17, 1->12, 2->7, 3->2, >=4->1
                    accel = max(1, 17-level*5)
                    return level | LZ4_FLAG, LZ4_compress_fast(packet, accel)
                return level | LZ4_FLAG, LZ4_compress(packet)
except Exception as e:
    log("lz4 not found: %s", e)
    del e
    LZ4_uncompress = None

python_lzo_version = None
lzo_version = None
try:
    import lzo
    has_lzo = True
    python_lzo_version = lzo.__version__
    lzo_version = lzo.LZO_VERSION_STRING
    def lzo_compress(packet, level):
        return level | LZO_FLAG, lzo.compress(packet)
    LZO_decompress = lzo.decompress
except Exception as e:
    log("lzo not found: %s", e)
    del e
    LZO_decompress = None
    has_lzo = False
    def lzo_compress(packet, level):
        raise Exception("lzo is not supported!")


try:
    import zlib
    has_zlib = True
    #stupid python version breakage:
    if sys.version > '3':
        def zcompress(packet, level):
            if type(packet)!=bytes:
                packet = bytes(packet, 'UTF-8')
            return level + ZLIB_FLAG, zlib.compress(packet, level)
    else:
        def zcompress(packet, level):
            return level + ZLIB_FLAG, zlib.compress(str(packet), level)
except ImportError:
    has_zlib = False
    def zcompress(packet, level):
        raise Exception("zlib is not supported!")


if sys.version > '3':
    def nocompress(packet, _level):
        if type(packet)!=bytes:
            packet = bytes(packet, 'UTF-8')
        return 0, packet
else:
    def nocompress(packet, _level):
        return 0, packet


#defaults to True if available:
use_zlib = has_zlib
use_lzo = has_lzo
use_lz4 = has_lz4

#all the compressors we know about, in best compatibility order:
ALL_COMPRESSORS = ["zlib", "lz4", "lzo"]

#order for performance:
PERFORMANCE_ORDER = ["lz4", "lzo", "zlib"]


_COMPRESSORS = {
        "zlib"  : zcompress,
        "lz4"   : lz4_compress,
        "lzo"   : lzo_compress,
        "none"  : nocompress,
               }

def get_compression_caps():
    caps = {}
    _lzo = {"" : use_lzo}
    if lzo_version:
        _lzo["version"] = lzo_version
    if python_lzo_version:
        caps["python-lzo"] = {
                              ""            : True,
                              "version"     : python_lzo_version,
                              }
    _lz4 = {""  : use_lz4}
    if lz4_version:
        _lz4["version"] = lz4_version
    if python_lz4_version:
        caps["python-lz4"] = {
                              ""            : True,
                              "version"     : python_lz4_version,
                              }
    _zlib = {
             ""             : use_zlib,
             }
    if has_zlib:
        _zlib["version"] = zlib.__version__
    caps.update({
                 "lz4"                   : _lz4,
                 "lzo"                   : _lzo,
                 "zlib"                  : _zlib,
                 })
    return caps

def get_enabled_compressors(order=ALL_COMPRESSORS):
    enabled = [x for x,b in {
            "lz4"                   : use_lz4,
            "lzo"                   : use_lzo,
            "zlib"                  : use_zlib,
            }.items() if b]
    #order them:
    return [x for x in order if x in enabled]

def get_compressor(c):
    assert c=="none" or c in ALL_COMPRESSORS
    return _COMPRESSORS[c]

def get_compressor_name(c):
    assert c in _COMPRESSORS.values(), "invalid compressor: %s" % c
    for k,v in _COMPRESSORS.items():
        if v==c:
            return k
    raise Exception("impossible bug!")


def sanity_checks():
    if not use_lzo and not use_lz4:
        if not use_zlib:
            log.warn("Warning: all the compressors are disabled,")
            log.warn(" unless you use mmap or have a gigabit connection or better")
            log.warn(" performance will suffer")
        else:
            log.warn("Warning: zlib is the only compressor enabled")
            log.warn(" install and enable lzo or lz4 support for better performance")


class Compressed(object):
    def __init__(self, datatype, data, can_inline=False):
        assert data is not None, "compressed data cannot be set to None"
        self.datatype = datatype
        self.data = data
        self.can_inline = can_inline
    def __len__(self):
        return len(self.data)
    def __repr__(self):
        return  "Compressed(%s: %i bytes)" % (self.datatype, len(self.data))


class LevelCompressed(Compressed):
    def __init__(self, datatype, data, level, algo, can_inline):
        Compressed.__init__(self, datatype, data, can_inline)
        self.level = level
        self.algorithm = algo
    def __repr__(self):
        return  "LevelCompressed(%s: %i bytes as %s/%i)" % (self.datatype, len(self.data), self.algorithm, self.level)


class LargeStructure(object):
    def __init__(self, datatype, data):
        self.datatype = datatype
        self.data = data
    def __len__(self):
        return len(self.data)
    def __repr__(self):
        return  "LargeStructure(%s: %i bytes)" % (self.datatype, len(self.data))

class Compressible(LargeStructure):
    #wrapper for data that should be compressed at some point,
    #to use this class, you must override compress()
    def __repr__(self):
        return  "Compressible(%s: %i bytes)" % (self.datatype, len(self.data))
    def compress(self):
        raise Exception("compress() not defined on %s" % self)


def compressed_wrapper(datatype, data, level=5, zlib=False, lz4=False, lzo=False, can_inline=True):
    if isinstance(data, memoryview):
        data = data.tobytes()
    size = len(data)
    if size>MAX_SIZE:
        raise Exception("uncompressed data is too large: %iMB, limit is %iMB" % (size//1024//1024, MAX_SIZE//1024//1024))
    if lz4:
        assert use_lz4, "cannot use lz4"
        algo = "lz4"
        cl, cdata = lz4_compress(data, level)
    elif lzo:
        assert use_lzo, "cannot use lzo"
        algo = "lzo"
        cl, cdata = lzo_compress(data, level)
    else:
        assert zlib and use_zlib, "cannot use zlib"
        algo = "zlib"
        cl, cdata = zcompress(data, level)
    return LevelCompressed(datatype, cdata, cl, algo, can_inline=can_inline)


class InvalidCompressionException(Exception):
    pass


def get_compression_type(level):
    if level & LZ4_FLAG:
        return "lz4"
    elif level & LZO_FLAG:
        return "lzo"
    else:
        return "zlib"


import struct
LZ4_HEADER = struct.Struct('<L')
def decompress(data, level):
    #log.info("decompress(%s bytes, %s) type=%s", len(data), get_compression_type(level))
    if level & LZ4_FLAG:
        if not has_lz4:
            raise InvalidCompressionException("lz4 is not available")
        if not use_lz4:
            raise InvalidCompressionException("lz4 is not enabled")
        size = LZ4_HEADER.unpack_from(data[:4])[0]
        #TODO: it would be better to use the max_size we have in protocol,
        #but this hardcoded value will do for now
        if size>MAX_SIZE:
            raise Exception("uncompressed data is too large: %iMB, limit is %iMB" % (size//1024//1024, MAX_SIZE//1024//1024))
        return LZ4_uncompress(data)
    elif level & LZO_FLAG:
        if not has_lzo:
            raise InvalidCompressionException("lzo is not available")
        if not use_lzo:
            raise InvalidCompressionException("lzo is not enabled")
        return LZO_decompress(data)
    else:
        if not use_zlib:
            raise InvalidCompressionException("zlib is not enabled")
        return zlib.decompress(data)

NAME_TO_FLAG = {
                "lz4"   : LZ4_FLAG,
                "zlib"  : 0,
                "lzo"   : LZO_FLAG,
                }

def decompress_by_name(data, algo):
    assert algo in NAME_TO_FLAG, "invalid compression algorithm: %s" % algo
    flag = NAME_TO_FLAG[algo]
    return decompress(data, flag)


def main():
    from xpra.util import print_nested_dict
    from xpra.platform import program_context
    with program_context("Compression", "Compression Info"):
        print_nested_dict(get_compression_caps())


if __name__ == "__main__":
    main()
