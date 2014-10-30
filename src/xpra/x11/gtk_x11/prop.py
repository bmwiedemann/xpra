# This file is part of Xpra.
# Copyright (C) 2008, 2009 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2012-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

"""All the goo needed to deal with X properties.

Everyone else should just use prop_set/prop_get with nice clean Python calling
conventions, and if you need more (un)marshalling smarts, add them here."""

import struct
import gtk.gdk
import cairo

from xpra.x11.gtk_x11.gdk_bindings import (
                get_xatom, get_pyatom,      #@UnresolvedImport
                get_xwindow, get_pywindow,  #@UnresolvedImport
                get_xvisual,                #@UnresolvedImport
               )
from xpra.x11.bindings.window_bindings import (
                constants,                      #@UnresolvedImport
                X11WindowBindings,          #@UnresolvedImport
                PropertyError)              #@UnresolvedImport
X11Window = X11WindowBindings()

from xpra.os_util import StringIOClass
from xpra.x11.xsettings_prop import set_settings, get_settings
from xpra.gtk_common.error import xsync, XError
from xpra.codecs.argb.argb import premultiply_argb_in_place #@UnresolvedImport
from xpra.log import Logger
log = Logger("x11", "window")


import sys
if sys.version > '3':
    long = int              #@ReservedAssignment
    unicode = str           #@ReservedAssignment


USPosition      = constants["USPosition"]
PPosition       = constants["PPosition"]
PMaxSize        = constants["PMaxSize"]
PMinSize        = constants["PMinSize"]
PBaseSize       = constants["PBaseSize"]
PResizeInc      = constants["PResizeInc"]
PAspect         = constants["PAspect"]
PWinGravity     = constants["PWinGravity"]
XUrgencyHint    = constants["XUrgencyHint"]
WindowGroupHint = constants["WindowGroupHint"]
StateHint       = constants["StateHint"]
IconicState     = constants["IconicState"]
InputHint       = constants["InputHint"]


def unsupported(*args):
    raise Exception("unsupported")

def _force_length(name, data, length, noerror_length=None):
    if len(data)==length:
        return data
    if len(data)!=noerror_length:
        log.warn("Odd-lengthed property %s: wanted %s bytes, got %s: %r"
                 % (name, length, len(data), data))
    # Zero-pad data
    data += "\0" * length
    return data[:length]

class WMSizeHints(object):
    def __init__(self, disp, data):
        # pre-ICCCM size is 15
        data = _force_length("WM_SIZE_HINTS", data, 18*4, noerror_length=15*4)
        def normint(v):
            if v==(2**32-1):
                return -1
            return v
        (flags,
         pad1, pad2, pad3, pad4,            #@UnusedVariable
         min_width, min_height,
         max_width, max_height,
         width_inc, height_inc,
         min_aspect_num, min_aspect_denom,
         max_aspect_num, max_aspect_denom,
         base_width, base_height,
         win_gravity) = [normint(x) for x in struct.unpack("=" + "I" * 18, data)] #@UnusedVariable
        # We only extract the pieces we care about:
        if flags & PMaxSize:
            self.max_size = (max_width, max_height)
        else:
            self.max_size = None
        if flags & PMinSize:
            self.min_size = (min_width, min_height)
        else:
            self.min_size = None
        if flags & PBaseSize:
            self.base_size = (base_width, base_height)
        else:
            self.base_size = None
        if flags & PResizeInc:
            self.resize_inc = (width_inc, height_inc)
        else:
            self.resize_inc = None
        if flags & PAspect:
            self.min_aspect = min_aspect_num * 1.0 / min_aspect_denom
            self.min_aspect_ratio = (min_aspect_num, min_aspect_denom)
            self.max_aspect = max_aspect_num * 1.0 / max_aspect_denom
            self.max_aspect_ratio = (max_aspect_num,  max_aspect_denom)
        else:
            self.min_aspect, self.max_aspect = (None, None)
            self.min_aspect_ratio, self.max_aspect_ratio = (None, None)
        if flags & PWinGravity:
            self.win_gravity = win_gravity
        else:
            self.win_gravity = -1
        self.set_initial_position = bool((flags & USPosition) or (flags & PPosition))

    def to_dict(self, include_none=False):
        d = {}
        for attr, metakey in [
            ("max_size", "maximum-size"),
            ("min_size", "minimum-size"),
            ("base_size", "base-size"),
            ("resize_inc", "increment"),
            ("min_aspect_ratio", "minimum-aspect-ratio"),
            ("max_aspect_ratio", "maximum-aspect-ratio"),
            ("set_initial_position", "set-initial-position")
            ]:
            v = getattr(self, attr)
            if v is not None or include_none:
                d[metakey] = v
        return d

    def __str__(self):
        d = {"max_size"  : self.max_size,
             "min_size"  : self.min_size,
             "base_size" : self.base_size,
             "resize_inc": self.resize_inc,
             "min_aspect": self.min_aspect,
             "max_aspect": self.max_aspect,
             "min_aspect_ratio"  : self.min_aspect_ratio,
             "max_aspect_ratio"  : self.max_aspect_ratio,
             "win_gravity"       : self.win_gravity,
             "set_initial_position"  : self.set_initial_position}
        return "WMSizeHints(%s)" % dict([(k,v) for k,v in d.items() if v not in (False, None, (0,0))])


class WMHints(object):
    def __init__(self, disp, data):
        data = _force_length("WM_HINTS", data, 9 * 4)
        (flags, _input, initial_state,  #@UnusedVariable
         icon_pixmap, icon_window,      #@UnusedVariable
         icon_x, icon_y, icon_mask,     #@UnusedVariable
         window_group) = struct.unpack("=" + "i" * 9, data)
        # NB the last field is missing from at least some ICCCM 2.0's (typo).
        # FIXME: extract icon stuff too
        self.urgency = bool(flags & XUrgencyHint)
        if flags & WindowGroupHint:
            try:
                pywin = get_pywindow(disp, window_group)
            except:
                pywin = None
            self.group_leader = (window_group, pywin)
        else:
            self.group_leader = None
        if flags & StateHint:
            self.start_iconic = (initial_state == IconicState)
        else:
            self.start_iconic = None
        if flags & InputHint:
            self.input = _input
        else:
            self.input = None

    def __str__(self):
        return "WMHints(%s)" % {"group_leader"  : self.group_leader,
                                "start_iconic"  : self.start_iconic,
                                "input"         : self.input}

class NetWMStrut(object):
    def __init__(self, disp, data):
        # This eats both _NET_WM_STRUT and _NET_WM_STRUT_PARTIAL.  If we are
        # given a _NET_WM_STRUT instead of a _NET_WM_STRUT_PARTIAL, then it
        # will be only length 4 instead of 12, but _force_length will zero-pad
        # and _NET_WM_STRUT is *defined* as a _NET_WM_STRUT_PARTIAL where the
        # extra fields are zero... so it all works out.
        data = _force_length("_NET_WM_STRUT or _NET_WM_STRUT_PARTIAL", data, 4 * 12)
        (self.left, self.right, self.top, self.bottom,
         self.left_start_y, self.left_end_y,
         self.right_start_y, self.right_end_y,
         self.top_start_x, self.top_end_x,
         self.bottom_start_x, self.bottom_stop_x,
         ) = struct.unpack("=" + "I" * 12, data)

    def __str__(self):
        return "NetWMStrut(%s)" % str(self.left, self.right, self.top, self.bottom,
                                     self.left_start_y, self.left_end_y,
                                     self.right_start_y, self.right_end_y,
                                     self.top_start_x, self.top_end_x,
                                     self.bottom_start_x, self.bottom_stop_x)


def _read_image(disp, stream):
    try:
        header = stream.read(2 * 4)
        if not header:
            return None
        (width, height) = struct.unpack("=II", header)
        data = stream.read(width * height * 4)
        if len(data) < width * height * 4:
            log.warn("Corrupt _NET_WM_ICON")
            return None
    except Exception as e:
        log.warn("Weird corruption in _NET_WM_ICON: %s", e)
        return None
    # Cairo wants a native-endian array here, and since the icon is
    # transmitted as CARDINALs, that's what we get. It might seem more
    # sensible to use ImageSurface.create_for_data (at least it did to me!)
    # but then you end up with a surface that refers to the memory you pass in
    # directly, and also .get_data() doesn't work on it, and it breaks the
    # test suite and blah. This at least works, as odd as it is:
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    # old versions of cairo do not have this method, just ignore it
    if not hasattr(surf, "get_data"):
        log.warn("Your Cairo is too old! Carrying on as best I can, "
                 "but don't expect a miracle")
        return None
    surf.get_data()[:] = data
    # Cairo uses premultiplied alpha. EWMH actually doesn't specify what it
    # uses, but apparently the de-facto standard is non-premultiplied. (At
    # least that's what Compiz's sources say.)
    premultiply_argb_in_place(surf.get_data())
    return (width * height, surf)

# This returns a cairo ImageSurface which contains the largest icon defined in
# a _NET_WM_ICON property.
def NetWMIcons(disp, data):
    icons = []
    stream = StringIOClass(data)
    while True:
        size_image = _read_image(disp, stream)
        if size_image is None:
            break
        icons.append(size_image)
    if not icons:
        return None
    icons.sort()
    return icons[-1][1]

def _get_atom(disp, d):
    unpacked = struct.unpack("@I", d)[0]
    pyatom = get_pyatom(disp, unpacked)
    if not pyatom:
        log.error("invalid atom: %s - %s", repr(d), repr(unpacked))
        return  None
    return str(pyatom)

def _get_multiple(disp, d):
    uint_struct = struct.Struct("@I")
    log("get_multiple struct size=%s, len(%s)=%s", uint_struct.size, d, len(d))
    if len(d)!=uint_struct.size and False:
        log.info("get_multiple value is not an atom: %s", d)
        return  str(d)
    return _get_atom(disp, d)


_prop_types = {
    # Python type, X type Atom, formatbits, serializer, deserializer, list
    # terminator
    "utf8": (unicode, "UTF8_STRING", 8,
             lambda disp, u: u.encode("UTF-8"),
             lambda disp, d: d.decode("UTF-8"),
             "\0"),
    # In theory, there should be something clever about COMPOUND_TEXT here.  I
    # am not sufficiently clever to deal with COMPOUNT_TEXT.  Even knowing
    # that Xutf8TextPropertyToTextList exists.
    "latin1": (unicode, "STRING", 8,
               lambda disp, u: u.encode("latin1"),
               lambda disp, d: d.decode("latin1"),
               "\0"),
    "atom": (str, "ATOM", 32,
             lambda disp, a: struct.pack("@I", get_xatom(a)),
              _get_atom,
             ""),
    "u32": ((int, long), "CARDINAL", 32,
            lambda disp, c: struct.pack("=I", c),
            lambda disp, d: struct.unpack("=I", d)[0],
            ""),
    "integer": ((int, long), "INTEGER", 32,
            lambda disp, c: struct.pack("=I", c),
            lambda disp, d: struct.unpack("=I", d)[0],
            ""),
    "visual": (gtk.gdk.Visual, "VISUALID", 32,
               lambda disp, c: struct.pack("=I", get_xvisual(c)),
               unsupported,
               ""),
    "window": (gtk.gdk.Window, "WINDOW", 32,
               lambda disp, c: struct.pack("=I", get_xwindow(c)),
               lambda disp, d: get_pywindow(disp, struct.unpack("=I", d)[0]),
               ""),
    "wm-size-hints": (WMSizeHints, "WM_SIZE_HINTS", 32,
                      unsupported,
                      WMSizeHints,
                      None),
    "wm-hints": (WMHints, "WM_HINTS", 32,
                 unsupported,
                 WMHints,
                 None),
    "strut": (NetWMStrut, "CARDINAL", 32,
              unsupported, NetWMStrut, None),
    "strut-partial": (NetWMStrut, "CARDINAL", 32,
                      unsupported, NetWMStrut, None),
    "icon": (cairo.ImageSurface, "CARDINAL", 32,
             unsupported, NetWMIcons, None),
    "xsettings-settings": (tuple, "_XSETTINGS_SETTINGS", 8,
                           set_settings,
                           get_settings,
                           None),
    # For uploading ad-hoc instances of the above complex structures to the
    # server, so we can test reading them out again:
    "debug-CARDINAL": (str, "CARDINAL", 32,
                       lambda disp, c: c,
                       lambda disp, d: d,
                       None),
    # For fetching the extra information on a MULTIPLE clipboard conversion
    # request. The exciting thing about MULTIPLE is that it's not actually
    # specified what 'type' one should use; you just fetch with
    # AnyPropertyType and assume that what you get is a bunch of pairs of
    # atoms.
    "multiple-conversion": (str, 0, 32, unsupported, _get_multiple, None),
    }

def _prop_encode(disp, etype, value):
    if isinstance(etype, list):
        return _prop_encode_list(disp, etype[0], value)
    else:
        return _prop_encode_scalar(disp, etype, value)

def _prop_encode_scalar(disp, etype, value):
    (pytype, atom, formatbits, serialize, _, _) = _prop_types[etype]
    assert isinstance(value, pytype), "value for atom %s is not a %s: %s" % (atom, pytype, type(value))
    return (atom, formatbits, serialize(disp, value))

def _prop_encode_list(disp, etype, value):
    (_, atom, formatbits, _, _, terminator) = _prop_types[etype]
    value = list(value)
    serialized = [_prop_encode_scalar(disp, etype, v)[2] for v in value]
    no_none = [x for x in serialized if x is not None]
    # Strings in X really are null-separated, not null-terminated (ICCCM
    # 2.7.1, see also note in 4.1.2.5)
    return (atom, formatbits, terminator.join(no_none))


def prop_set(target, key, etype, value):
    with xsync:
        X11Window.XChangeProperty(get_xwindow(target), key,
                       _prop_encode(target, etype, value))

def _prop_decode(disp, etype, data):
    if isinstance(etype, list):
        return _prop_decode_list(disp, etype[0], data)
    else:
        return _prop_decode_scalar(disp, etype, data)

def _prop_decode_scalar(disp, etype, data):
    (pytype, _, _, _, deserialize, _) = _prop_types[etype]
    value = deserialize(disp, data)
    assert value is None or isinstance(value, pytype), "expected a %s but value is a %s" % (pytype, type(value))
    return value

def _prop_decode_list(disp, etype, data):
    (_, _, formatbits, _, _, terminator) = _prop_types[etype]
    if terminator:
        datums = data.split(terminator)
    else:
        datums = []
        nbytes = formatbits // 8
        while data:
            datums.append(data[:nbytes])
            data = data[nbytes:]
    props = [_prop_decode_scalar(disp, etype, datum) for datum in datums]
    #assert None not in props
    return [x for x in props if x is not None]

# May return None.
def prop_get(target, key, etype, ignore_errors=False, raise_xerrors=False):
    if isinstance(etype, list):
        scalar_type = etype[0]
    else:
        scalar_type = etype
    (_, atom, _, _, _, _) = _prop_types[scalar_type]
    try:
        with xsync:
            data = X11Window.XGetWindowProperty(get_xwindow(target), key, atom, etype)
        if data is None:
            if not ignore_errors:
                log("Missing property %s (%s)", key, etype)
            return None
    except XError:
        if raise_xerrors:
            raise
        log.info("Missing window %s or wrong property type %s (%s)", target, key, etype, exc_info=True)
        return None
    except PropertyError:
        if not ignore_errors:
            log.info("Missing property or wrong property type %s (%s)", key, etype, exc_info=True)
        return None
    try:
        return _prop_decode(target, etype, data)
    except:
        if not ignore_errors:
            log.warn("Error parsing property %s (type %s); this may be a"
                     + " misbehaving application, or bug in Xpra\n"
                     + "  Data: %r[...?]",
                     key, etype, data[:160], exc_info=True)
        raise
