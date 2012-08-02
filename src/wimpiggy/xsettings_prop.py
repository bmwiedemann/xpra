# This file is part of Parti.
# Copyright (C) 2012 Antoine Martin <antoine@devloop.org.uk>
# Parti is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

"""
XSETTINGS

This code deals with:
* extracting data from XSETTINGS into nice python data structures
and
* converting those structures back into XSETTINGS format
"""

import sys
import struct
from wimpiggy.log import Logger
log = Logger()

#debug = log.info
debug = log.debug

#undocumented XSETTINGS endianess values:
LITTLE_ENDIAN = 0
BIG_ENDIAN    = 1
def get_local_byteorder():
    if sys.byteorder=="little":
        return  LITTLE_ENDIAN
    else:
        return  BIG_ENDIAN

#the 3 types of settings supported:
XSettingsTypeInteger = 0
XSettingsTypeString = 1
XSettingsTypeColor = 2


def get_settings(disp, d):
    #parse xsettings according to
    #http://standards.freedesktop.org/xsettings-spec/xsettings-spec-0.5.html
    assert len(d)>=12, "_XSETTINGS_SETTINGS property is too small: %s" % len(d)
    debug("parse_xsettings(%s)", list(d))    
    byte_order, _, _, _, serial, n_settings = struct.unpack("=BBBBII", d[:12])
    debug("parse_xsettings(..) found byte_order=%s (local is %s), serial=%s, n_settings=%s", byte_order, get_local_byteorder(), serial, n_settings)
    settings = []
    pos = 12
    while n_settings>len(settings) and len(d)>0:
        istart = pos
        #parse header:
        setting_type, _, name_len = struct.unpack("=BBH", d[pos:pos+4])
        pos += 4
        #extract property name:
        prop_name = d[pos:pos+name_len]
        pos += (name_len + 0x3) & ~0x3
        #serial:
        last_change_serial = struct.unpack("=I", d[pos:pos+4])[0]
        pos += 4
        debug("parse_xsettings(..) found property %s of type %s, serial=%s", prop_name, setting_type, last_change_serial)
        #extract value:
        if setting_type==0:     #XSettingsTypeInteger
            value = struct.unpack("=I", d[pos:pos+4])[0]
            pos += 4
        elif setting_type==1:   #XSettingsTypeString
            value_len = struct.unpack("=I", d[pos:pos+4])[0]
            value = d[pos+4:pos+4+value_len]
            pos += 4 + ((value_len + 0x3) & ~0x3)
        elif setting_type==2:   #XSettingsTypeColor
            red, blue, green, alpha = struct.unpack("=HHHH", d[pos:pos+8])
            value = (red, blue, green, alpha)
            pos += 8
        else:
            log.error("invalid setting type: %s, cannot continue parsing XSETTINGS!", setting_type)
            break
        setting = setting_type, prop_name, value, last_change_serial
        debug("parse_xsettings(..) %s -> %s", list(d[istart:pos]), setting)
        settings.append(setting)
    debug("parse_xsettings(..) settings=%s", settings)
    return  serial, settings

def set_settings(disp, d):
    #TODO: detect old clients
    assert len(d)==2, "invalid format for XSETTINGS: %s" % str(d)
    serial, settings = d
    debug("format_xsettings(%s) serial=%s, %s settings", d, serial, len(settings))
    all_bin_settings = b''
    n_settings = 0
    for setting in settings:
        setting_type, prop_name, value, last_change_serial = setting
        x = b''
        x += struct.pack("=BBH", setting_type, 0, len(prop_name))
        x += struct.pack("="+"s"*len(prop_name), *list(prop_name))
        pad_len = ((len(prop_name) + 0x3) & ~0x3) - len(prop_name)
        x += '\0'*pad_len
        x += struct.pack("=I", last_change_serial)
        if setting_type==XSettingsTypeInteger:
            assert type(value)==int
            x += struct.pack("=I", value)
        elif setting_type==XSettingsTypeString:
            assert type(value)==str
            x += struct.pack("=I", len(value))
            x += struct.pack("="+"s"*len(value), *list(value))
            pad_len = ((len(value) + 0x3) & ~0x3) - len(value)
            x += '\0'*pad_len
        elif setting_type==XSettingsTypeColor:
            red, blue, green, alpha = value
            x = struct.pack("=HHHH", red, blue, green, alpha)
        else:
            log.error("invalid xsetting type: %s, skipped %s", setting_type, prop_name)
            continue
        debug("format_xsettings(..) %s -> %s", setting, list(x))
        all_bin_settings += x
        n_settings += 1
    #header
    v = struct.pack("=BBBBII", get_local_byteorder(), 0, 0, 0, serial, n_settings)
    v += all_bin_settings   #values
    v += '\0'               #null terminated
    debug("format_xsettings(%s)=%s", d, list(v))
    return  v
