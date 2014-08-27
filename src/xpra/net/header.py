# This file is part of Xpra.
# Copyright (C) 2011-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import sys
import struct


ZLIB_FLAG       = 0x0       #assume zlib if no other compression flag is set
FLAGS_RENCODE   = 0x1
FLAGS_CIPHER    = 0x2
FLAGS_YAML      = 0x4
#0x8 is free
LZ4_FLAG        = 0x10
LZO_FLAG        = 0x20
FLAGS_NOHEADER  = 0x40
#0x80 is free

_header_unpack_struct = struct.Struct('!cBBBL')
def unpack_header(buf):
    return _header_unpack_struct.unpack_from(buf)

#'P' + protocol-flags + compression_level + packet_index + data_size
_header_pack_struct = struct.Struct('!BBBBL')
def pack_header(proto_flags, level, index, payload_size):
    return _header_pack_struct.pack(ord("P"), proto_flags, level, index, payload_size)

if sys.version_info[0]<3:
    #before v3, python does the right thing without hassle:
    def pack_header_and_data(actual_size, proto_flags, level, index, payload_size, data):
        return struct.pack('!BBBBL%ss' % actual_size, ord("P"), proto_flags, level, index, payload_size, data)
else:
    pack_header_and_data = None
