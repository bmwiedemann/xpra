# This file is part of Xpra.
# Copyright (C) 2008, 2009 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2013-2015 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import binascii
from xpra.os_util import strtobytes, bytestostr
import traceback
import threading
import sys


#constants shared between client and server:
#(do not modify the values, see also disconnect_is_an_error)
#timeouts:
CLIENT_PING_TIMEOUT     = "client ping timeout"
LOGIN_TIMEOUT           = "login timeout"
CLIENT_EXIT_TIMEOUT     = "client exit timeout"
#errors:
PROTOCOL_ERROR          = "protocol error"
VERSION_ERROR           = "version error"
CONTROL_COMMAND_ERROR   = "control command error"
AUTHENTICATION_ERROR    = "authentication error"
SERVER_ERROR            = "server error"
SESSION_NOT_FOUND       = "session not found error"
#informational (not a problem):
DONE                    = "done"
SERVER_EXIT             = "server exit"
SERVER_SHUTDOWN         = "server shutdown"
CLIENT_REQUEST          = "client request"
DETACH_REQUEST          = "detach request"
NEW_CLIENT              = "new client"
IDLE_TIMEOUT            = "idle timeout"
#client telling the server:
CLIENT_EXIT             = "client exit"


#magic value for "workspace" window property, means unset
WORKSPACE_UNSET = 65535
WORKSPACE_ALL = 0xffffffff

#this default value is based on 0.14.19 clients,
#later clients should provide the 'metadata.supported" capability instead
DEFAULT_METADATA_SUPPORTED = ("title", "icon-title", "pid", "iconic",
                              "size-hints", "class-instance", "client-machine",
                              "transient-for", "window-type",
                              "fullscreen", "maximized", "decorations", "skip-taskbar", "skip-pager",
                              "has-alpha", "override-redirect", "tray", "modal",
                              "role", "opacity", "xid", "group-leader")


#convenience method based on the strings above:
def disconnect_is_an_error(reason):
    return reason.find("error")>=0 or (reason.find("timeout")>=0 and reason!=IDLE_TIMEOUT)


if sys.version > '3':
    unicode = str           #@ReservedAssignment


def dump_exc():
    """Call this from a except: clause to print a nice traceback."""
    print("".join(traceback.format_exception(*sys.exc_info())))

# A simple little class whose instances we can stick random bags of attributes
# on.
class AdHocStruct(object):
    def __repr__(self):
        return ("<%s object, contents: %r>"
                % (type(self).__name__, self.__dict__))


def remove_dupes(seq):
    seen = set()
    seen_add = seen.add
    return [x for x in seq if not (x in seen or seen_add(x))]


class AtomicInteger(object):
    def __init__(self, integer = 0):
        self.counter = integer
        self.lock = threading.RLock()

    def increase(self, inc = 1):
        with self.lock:
            self.counter = self.counter + inc
            return self.counter

    def decrease(self, dec = 1):
        with self.lock:
            self.counter = self.counter - dec
            return self.counter

    def get(self):
        return self.counter

    def __str__(self):
        return str(self.counter)

    def __repr__(self):
        return "AtomicInteger(%s)" % self.counter


    def __int__(self):
        return self.counter

    def __eq__(self, other):
        try:
            return self.counter==int(other)
        except:
            return -1

    def __cmp__(self, other):
        try:
            return self.counter-int(other)
        except:
            return -1


class MutableInteger(object):
    def __init__(self, integer = 0):
        self.counter = integer

    def increase(self, inc = 1):
        self.counter = self.counter + inc
        return self.counter

    def decrease(self, dec = 1):
        self.counter = self.counter - dec
        return self.counter

    def get(self):
        return self.counter

    def __str__(self):
        return str(self.counter)

    def __repr__(self):
        return "MutableInteger(%s)" % self.counter


    def __int__(self):
        return self.counter

    def __eq__(self, other):
        try:
            return self.counter==int(other)
        except:
            return -1

    def __cmp__(self, other):
        try:
            return self.counter-int(other)
        except:
            return -1


class typedict(dict):

    from xpra.log import Logger
    log = Logger("util")

    def capsget(self, key, default=None):
        v = self.get(key)
        #py3k and bytes as keys...
        if v is None and type(key)==str:
            v = self.get(strtobytes(key), default)
        if sys.version >= '3' and type(v)==bytes:
            v = bytestostr(v)
        return v

    def strget(self, k, default=None):
        v = self.capsget(k, default)
        if v is None:
            return None
        return bytestostr(v)

    def intget(self, k, d=0):
        v = self.capsget(k)
        if v is None:
            return d
        try:
            return int(v)
        except Exception as e:
            typedict.log.warn("error on %s: %s", k, e)
            return d

    def boolget(self, k, default_value=False):
        return bool(self.capsget(k, default_value))

    def dictget(self, k, default_value={}):
        v = self.capsget(k, default_value)
        if v is None:
            return None
        if type(v)!=dict:
            typedict.log.warn("expected a dict value for %s but got %s", k, type(v))
            return default_value
        return v

    def intpair(self, k, default_value=None):
        v = self.intlistget(k, default_value)
        if v is None:
            return default_value
        if len(v)!=2:
            #"%s is not a pair of numbers: %s" % (k, len(v))
            return default_value
        try:
            return int(v[0]), int(v[1])
        except:
            return default_value

    def strlistget(self, k, default_value=[]):
        return self.listget(k, default_value, str)

    def intlistget(self, k, default_value=[]):
        return self.listget(k, default_value, int)

    def listget(self, k, default_value=[], item_type=None, max_items=None):
        v = self.capsget(k, default_value)
        if v is None:
            return default_value
        if type(v) not in (list, tuple):
            typedict.log.warn("expected a list or tuple value for %s but got %s", k, type(v))
            return default_value
        aslist = list(v)
        if item_type:
            for i in range(len(aslist)):
                x = aslist[i]
                if sys.version > '3' and type(x)==bytes and item_type==str:
                    x = bytestostr(x)
                    aslist[i] = x
                elif type(x)==unicode and item_type==str:
                    x = str(x)
                    aslist[i] = x
                if type(x)!=item_type:
                    typedict.log.warn("invalid item type for %s %s: expected %s but got %s", type(v), k, item_type, type(x))
                    return default_value
        if max_items is not None:
            if len(v)>max_items:
                typedict.log.warn("too many items in %s %s: maximum %s allowed, but got %s", type(v), k, max_items, len(v))
                return default_value
        return aslist


def parse_scaling_value(v):
    if not v:
        return None
    values = v.replace("/", ":").replace(",", ":").split(":", 1)
    values = [int(x) for x in values]
    for x in values:
        assert x>0, "invalid scaling value %s" % x
    if len(values)==1:
        ret = 1, values[0]
    else:
        assert values[0]<=values[1], "cannot upscale"
        ret = values[0], values[1]
    return ret

def from0to100(v):
    return intrangevalidator(v, 0, 100)

def intrangevalidator(v, min_value=None, max_value=None):
    v = int(v)
    if min_value is not None and v<min_value:
        raise ValueError("value must be greater than %i" % min_value)
    if max_value is not None and v>max_value:
        raise ValueError("value must be lower than %i" % max_value)
    return v


def log_screen_sizes(root_w, root_h, sizes):
    try:
        do_log_screen_sizes(root_w, root_h, sizes)
    except Exception as e:
        from xpra.log import Logger
        log = Logger("screen")
        log.warn("failed to parse screen size information: %s", e, exc_info=True)

def prettify_plug_name(s, default=""):
    if not s:
        return default
    #prettify strings on win32
    s = s.lstrip("0\\").lstrip("1\\").lstrip(".\\").replace("0\\", "-")
    if s=="WinSta-Default":
        s = "Default"
    return s

def do_log_screen_sizes(root_w, root_h, sizes):
    from xpra.log import Logger
    log = Logger("util")
    #old format, used by some clients (android):
    if type(sizes) not in (tuple, list):
        return
    if any(True for x in sizes if type(x) not in (tuple, list)):
        return
    def dpi(size_pixels, size_mm):
        if size_mm==0:
            return 0
        return int(size_pixels * 254 / size_mm / 10)
    for s in sizes:
        if len(s)<10:
            log.info(" %s", s)
            continue
        #more detailed output:
        display_name, width, height, width_mm, height_mm, \
        monitors, work_x, work_y, work_width, work_height = s[:10]
        #always log plug name:
        info = ["%s" % prettify_plug_name(display_name)]
        if width!=root_w or height!=root_h:
            #log plug dimensions if not the same as display (root):
            info.append("%ix%i" % (width, height))
        info.append("(%ix%i mm - DPI: %ix%i)" % (width_mm, height_mm, dpi(width, width_mm), dpi(height, height_mm)))
        def add_workarea(wx, wy, ww, wh):
            info.append("workarea: %ix%i" % (ww, wh))
            if wx!=0 or wy!=0:
                #log position if not (0, 0)
                info.append("at %ix%i" % (wx, wy))

        if work_width!=width or work_height!=height or work_x!=0 or work_y!=0:
            add_workarea(work_x, work_y, work_width, work_height)
        log.info("  "+" ".join(info))
        for i, m in enumerate(monitors, start=1):
            if len(m)<7:
                log.info("    %s", m)
                continue
            plug_name, plug_x, plug_y, plug_width, plug_height, plug_width_mm, plug_height_mm = m[:7]
            info = ['%s' % prettify_plug_name(plug_name, "monitor %i" % i)]
            if plug_width!=width or plug_height!=height or plug_x!=0 or plug_y!=0:
                info.append("%ix%i" % (plug_width, plug_height))
                if plug_x!=0 or plug_y!=0:
                    info.append("at %ix%i" % (plug_x, plug_y))
            if (plug_width_mm!=width_mm or plug_height_mm!=height_mm) and (plug_width_mm>0 or plug_height_mm>0):
                info.append("(%ix%i mm - DPI: %ix%i)" % (plug_width_mm, plug_height_mm, dpi(plug_width, plug_width_mm), dpi(plug_height, plug_height_mm)))
            if len(m)>=11:
                dwork_x, dwork_y, dwork_width, dwork_height = m[7:11]
                #only show it again if different from the screen workarea
                if dwork_x!=work_x or dwork_y!=work_y or dwork_width!=work_width or dwork_height!=work_height:
                    add_workarea(dwork_x, dwork_y, dwork_width, dwork_height)
            log.info("    "+" ".join(info))

def get_screen_info(screen_sizes):
    #same format as above
    if not screen_sizes:
        return {}
    info = {
            "screens" : len(screen_sizes)
            }
    for i, x in enumerate(screen_sizes):
        if type(x) not in (tuple, list):
            #legacy clients:
            info["screen[%s]" % i] = str(x)
            continue
        info["screen[%s].display" % i] = x[0]
        if len(x)>=3:
            info["screen[%s].size" % i] = x[1], x[2]
        if len(x)>=5:
            info["screen[%s].size_mm" % i] = x[3], x[4]
        if len(x)>=6:
            monitors = x[5]
            for j, monitor in enumerate(monitors):
                if len(monitor)>=7:
                    for k,v in {
                                "name"      : monitor[0],
                                "geometry"  : monitor[1:5],
                                "size_mm"   : monitor[5:7],
                                }.items():
                        info["screen[%s].monitor[%s].%s" % (i, j, k)] = v
        if len(x)>=10:
            info["screen[%s].workarea" % i] = x[6:10]
    return info


def dump_references(log, instances, exclude=[]):
    import gc
    import inspect
    gc.collect()
    frame = inspect.currentframe()
    try:
        exclude.append(instances)
        exclude.append([frame])
        for instance in instances:
            referrers = [x for x in gc.get_referrers(instance) if (x not in exclude and len([y for y in exclude if x in y])==0)]
            log.info("referrers for %s: %s", instance, len(referrers))
            for i in range(len(referrers)):
                r = referrers[i]
                log.info("[%s] in %s", i, type(r))
                if inspect.isframe(r):
                    log.info("  frame info: %s", str(inspect.getframeinfo(r))[:1024])
                elif type(r)==list:
                    listref = gc.get_referrers(r)
                    log.info("  list: %s..  %s referrers: %s", str(r[:32])[:1024], len(listref), str(listref[:32])[:1024])
                elif type(r)==dict:
                    if len(r)>64:
                        log.info("  %s items: %s", len(r), str(r)[:1024])
                        continue
                    for k,v in r.items():
                        if k is instance:
                            log.info("  key with value=%s", v)
                        elif v is instance:
                            log.info("  for key=%s", k)
                else:
                    log.info("     %s : %s", type(r), r)
    finally:
        del frame

def detect_leaks(log, detailed=[]):
    import types
    from collections import defaultdict
    import gc
    import inspect
    global before, after
    gc.enable()
    gc.set_debug(gc.DEBUG_LEAK)
    before = defaultdict(int)
    after = defaultdict(int)
    gc.collect()
    detailed = []
    ignore = (defaultdict, types.BuiltinFunctionType, types.BuiltinMethodType, types.FunctionType, types.MethodType)
    for i in gc.get_objects():
        if type(i) not in ignore:
            before[type(i)] += 1

    def print_leaks():
        global before, after
        gc.collect()
        lobjs = gc.get_objects()
        for i in lobjs:
            if type(i) not in ignore:
                after[type(i)] += 1
        log.info("print_leaks:")
        leaked = {}
        for k in after:
            delta = after[k]-before[k]
            if delta>0:
                leaked[delta] = k
        before = after
        after = defaultdict(int)
        for delta in reversed(sorted(leaked.keys())):
            ltype = leaked[delta]
            matches = [x for x in lobjs if type(x)==ltype and ltype not in ignore]
            if len(matches)<32:
                minfo = [str(x)[:32] for x in matches]
            else:
                minfo = "%s matches" % len(matches)
            log.info("%8i : %s : %s", delta, ltype, minfo)
            if len(matches)<32 and ltype in detailed:
                frame = inspect.currentframe()
                exclude = [frame, matches, lobjs]
                try:
                    dump_references(log, matches, exclude=exclude)
                finally:
                    del frame
                    del exclude
            del matches
            del minfo
        del lobjs
        return True
    return print_leaks


def repr_ellipsized(obj, limit=100):
    if (isinstance(obj, str) or isinstance(obj, unicode)) and len(obj) > limit:
        try:
            s = repr(obj[:limit])
            if len(obj)>limit:
                s += "..."
            return s
        except:
            return binascii.hexlify(obj[:limit])
    else:
        return repr(obj)


def rindex(alist, avalue):
    return len(alist) - alist[::-1].index(avalue) - 1


#used for merging dicts with a prefix and suffix
#non-None values get added to <todict> with a prefix and optional suffix
def updict(todict, prefix, d, suffix=""):
    if not d:
        return
    for k,v in d.items():
        if v is not None:
            if k:
                k = prefix+"."+str(k)
            else:
                k = prefix
            if suffix:
                k = k+"."+suffix
            todict[k] = v


def pver(v):
    #print for lists with version numbers, or CSV strings
    if type(v) in (list, tuple):
        types = list(set([type(x) for x in v]))
        if len(types)==1 and types[0]==int:
            return ".".join([str(x) for x in v])
        if len(types)==1 and types[0]==str:
            return ", ".join(v)
    return v

def std(s, extras="-,./: "):
    try:
        s = s.decode("latin1")
    except:
        pass
    def c(v):
        try:
            return chr(v)
        except:
            return str(v)
    def f(v):
        return str.isalnum(c(v)) or v in extras
    return "".join(filter(f, s))

def alnum(s):
    try:
        s = s.encode("latin1")
    except:
        pass
    def c(v):
        try:
            return chr(v)
        except:
            return str(v)
    def f(v):
        return str.isalnum(c(v))
    return "".join(c(v) for v in filter(f, s))

def nonl(x):
    if x is None:
        return None
    return str(x).replace("\n", "\\n").replace("\r", "\\r")

def xor(s1,s2):
    def _ord(v):
        try:
            return ord(v)
        except:
            return int(v)
    return ''.join(chr(_ord(a) ^ _ord(b)) for a,b in zip(s1,s2))

def engs(v):
    try:
        if type(v)==int:
            l = v
        else:
            l = len(v)
        return ["","s"][l!=1]
    except:
        return ""

def csv(v):
    try:
        return ", ".join(str(x) for x in v)
    except:
        return str(v)
