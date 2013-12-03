# This file is part of Xpra.
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2012, 2013 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import sys
import os
import struct
import re
import binascii

from xpra.gtk_common.gobject_compat import import_gobject, import_gtk, import_gdk, is_gtk3
gobject = import_gobject()
gtk = import_gtk()
gdk = import_gdk()
if is_gtk3():
    PROPERTY_CHANGE_MASK = gdk.EventMask.PROPERTY_CHANGE_MASK
else:
    PROPERTY_CHANGE_MASK = gdk.PROPERTY_CHANGE_MASK

from xpra.log import Logger, debug_if_env
log = Logger()
debug = debug_if_env(log, "XPRA_CLIPBOARD_DEBUG")

from xpra.gtk_common.gobject_util import n_arg_signal
from xpra.gtk_common.nested_main import NestedMainLoop
from xpra.net.protocol import compressed_wrapper


MAX_CLIPBOARD_PACKET_SIZE = 256*1024

ALL_CLIPBOARDS = ["CLIPBOARD", "PRIMARY", "SECONDARY"]
CLIPBOARDS = ALL_CLIPBOARDS
CLIPBOARDS_ENV = os.environ.get("XPRA_CLIPBOARDS")
if CLIPBOARDS_ENV is not None:
    CLIPBOARDS = CLIPBOARDS_ENV.split(",")
    CLIPBOARDS = [x.upper().strip() for x in CLIPBOARDS]


TEST_DROP_CLIPBOARD_REQUESTS = int(os.environ.get("XPRA_TEST_DROP_CLIPBOARD", "0"))

_discard_target_strs_ = ("^SAVE_TARGETS$",
        "^COMPOUND_TEXT$",
        "^NeXT ",
        "^com\.apple\.",
        "^CorePasteboardFlavorType ",
        "^dyn\.")
DISCARD_TARGETS = [re.compile(x) for x in _discard_target_strs_]

TEXT_TARGETS = ("UTF8_STRING", "TEXT", "STRING", "text/plain")


class ClipboardProtocolHelperBase(object):
    def __init__(self, send_packet_cb, progress_cb=None, clipboards=CLIPBOARDS, filter_res=None):
        self.send = send_packet_cb
        self.progress_cb = progress_cb
        self.max_clipboard_packet_size = MAX_CLIPBOARD_PACKET_SIZE
        self.filter_res = []
        if filter_res:
            for x in filter_res:
                try:
                    self.filter_res.append(re.compile(x))
                except:
                    log.error("invalid regular expression '%s' in clipboard filter")
        self._clipboard_request_counter = 0
        self._clipboard_outstanding_requests = {}
        self._want_targets = False
        self.init_packet_handlers()
        self.init_proxies(clipboards)

    def __str__(self):
        return "ClipboardProtocolHelperBase"

    def get_info(self):
        info = {"type"      : str(self),
                "max_size"  : self.max_clipboard_packet_size,
                "filters"   : [x.pattern for x in self.filter_res],
                "requests"  : self._clipboard_request_counter,
                "pending"   : self._clipboard_outstanding_requests.keys(),
                "want_targets"  : self._want_targets,
                }
        for clipboard, proxy in self._clipboard_proxies.items():
            for k,v in proxy.get_info().items():
                info["%s.%s" % (clipboard, k)] = v
        return info

    def cleanup(self):
        def nosend(*args):
            pass
        self.send = nosend
        for x in self._clipboard_proxies.values():
            x.cleanup()
        self._clipboard_proxies = {}

    def enable_selections(self, selections):
        #when clients connect, they can tell us which
        #clipboards they want enabled
        #(ie: OSX and win32 only use "CLIPBOARD", and not "PRIMARY" or "SECONDARY")
        for selection, proxy in self._clipboard_proxies.items():
            proxy.set_enabled(selection in selections)

    def set_greedy_client(self, greedy):
        for proxy in self._clipboard_proxies.values():
            proxy.set_greedy_client(greedy)

    def set_want_targets_client(self, want_targets):
        debug("set_want_targets_client(%s)", want_targets)
        self._want_targets = want_targets

    def init_packet_handlers(self):
        self._packet_handlers = {
            "clipboard-token":              self._process_clipboard_token,
            "clipboard-request":            self._process_clipboard_request,
            "clipboard-contents":           self._process_clipboard_contents,
            "clipboard-contents-none":      self._process_clipboard_contents_none,
            "clipboard-pending-requests":   self._process_clipboard_pending_requests,
            }

    def make_proxy(self, clipboard):
        return ClipboardProxy(clipboard)

    def init_proxies(self, clipboards):
        self._clipboard_proxies = {}
        for clipboard in clipboards:
            proxy = self.make_proxy(clipboard)
            proxy.connect("send-clipboard-token", self._send_clipboard_token_handler)
            proxy.connect("get-clipboard-from-remote", self._get_clipboard_from_remote_handler)
            proxy.show()
            self._clipboard_proxies[clipboard] = proxy
        debug("%s.init_proxies : %s", type(self), self._clipboard_proxies)

    def local_to_remote(self, selection):
        #overriden in some subclasses (see: translated_clipboard)
        return  selection
    def remote_to_local(self, selection):
        #overriden in some subclasses (see: translated_clipboard)
        return  selection

    # Used by the client during startup:
    def send_all_tokens(self):
        for selection, proxy in self._clipboard_proxies.items():
            self._send_clipboard_token_handler(proxy, selection)

    def _process_clipboard_token(self, packet):
        selection = packet[1]
        name = self.remote_to_local(selection)
        proxy = self._clipboard_proxies.get(name)
        if proxy is None:
            log.warn("ignoring token for clipboard proxy name '%s' (no proxy)", name)
            return
        if not proxy.is_enabled():
            log.warn("ignoring token for clipboard proxy name '%s' (disabled)", name)
            return
        debug("process clipboard token selection=%s, local clipboard name=%s, proxy=%s", selection, name, proxy)
        targets = None
        target_data = None
        if len(packet)>=3:
            targets = packet[2]
        if len(packet)>=8:
            target_data = {}
            target, dtype, dformat, wire_encoding, wire_data = packet[3:8]
            raw_data = self._munge_wire_selection_to_raw(wire_encoding, dtype, dformat, wire_data)
            target_data[target] = raw_data
        proxy.got_token(targets, target_data)

    def _get_clipboard_from_remote_handler(self, proxy, selection, target):
        request_id = self._clipboard_request_counter
        self._clipboard_request_counter += 1
        debug("get clipboard from remote handler id=%s", request_id)
        loop = NestedMainLoop()
        self._clipboard_outstanding_requests[request_id] = loop
        if self.progress_cb:
            self.progress_cb(len(self._clipboard_outstanding_requests), None)
        self.send("clipboard-request", request_id, self.local_to_remote(selection), target)
        result = loop.main(1 * 1000, 2 * 1000)
        debug("get clipboard from remote result(%s)=%s", request_id, result)
        del self._clipboard_outstanding_requests[request_id]
        if self.progress_cb:
            self.progress_cb(len(self._clipboard_outstanding_requests), None)
        return result

    def _clipboard_got_contents(self, request_id, dtype, dformat, data):
        loop = self._clipboard_outstanding_requests.get(request_id)
        debug("got clipboard contents for id=%s len=%s, loop=%s (type=%s, format=%s)",
              request_id, len(data or []), loop, dtype, dformat)
        if loop is None:
            debug("got unexpected response to clipboard request %s", request_id)
            return
        loop.done({"type": dtype, "format": dformat, "data": data})

    def _send_clipboard_token_handler(self, proxy, selection):
        debug("send clipboard token: %s", selection)
        rsel = self.local_to_remote(selection)
        if self._want_targets:
            #send the token with the target and data once we get them:
            #first get the targets, then get the contents for targets we want to send (if any)
            def got_targets(dtype, dformat, targets):
                debug("got_targets for selection %s: %s, %s, %s", selection, dtype, dformat, targets)
                #if there is a text target, send that too (just the first one that matches for now..)
                send_now = [x for x in targets if x in TEXT_TARGETS]
                def send_targets_only():
                    self.send("clipboard-token", rsel, targets)
                if len(send_now)==0:
                    send_targets_only()
                    return
                target = send_now[0]
                def got_contents(dtype, dformat, data):
                    debug("got_contents for selection %s: %s, %s, %s", selection, dtype, dformat, data)
                    #code mostly duplicated from _process_clipboard_request
                    #see there for details
                    if dtype is None or data is None:
                        send_targets_only()
                        return
                    wire_encoding, wire_data = self._munge_raw_selection_to_wire(target, dtype, dformat, data)
                    if wire_encoding is None:
                        send_targets_only()
                        return
                    wire_data = self._may_compress(dtype, dformat, wire_data)
                    if not wire_data:
                        send_targets_only()
                        return
                    target_data = (target, dtype, dformat, wire_encoding, wire_data)
                    debug("sending token with target data: %s", target_data)
                    self.send("clipboard-token", rsel, targets, *target_data)
                proxy.get_contents(target, got_contents)
            proxy.get_contents("TARGETS", got_targets)
            return
        self.send("clipboard-token", rsel)

    def _munge_raw_selection_to_wire(self, target, dtype, dformat, data):
        # Some types just cannot be marshalled:
        if type in ("WINDOW", "PIXMAP", "BITMAP", "DRAWABLE",
                    "PIXEL", "COLORMAP"):
            debug("skipping clipboard data of type: %s, format=%s, len(data)=%s", dtype, dformat, len(data))
            return None, None
        if target=="TARGETS" and dtype=="ATOM":
            #targets is special cased here
            #because we get the values in wire format already (not atoms)
            #thanks to the request_targets() function (required on win32)
            return "atoms", self._filter_targets(data)
        return self._do_munge_raw_selection_to_wire(target, dtype, dformat, data)

    def _filter_targets(self, targets):
        remove = []
        for target in targets:
            for x in DISCARD_TARGETS:
                if x.match(target):
                    remove.append(target)
                    break
        f = list(targets)
        for t in remove:
            f.remove(t)
        debug("_filter_targets(%s)=%s", targets, f)
        return f

    def _do_munge_raw_selection_to_wire(self, target, dtype, dformat, data):
        """ this method is overriden in xclipboard to parse X11 atoms """
        # Other types need special handling, and all types need to be
        # converting into an endian-neutral format:
        debug("_do_munge_raw_selection_to_wire(%s, %s, %s, %s:%s)", target, dtype, dformat, type(data), len(data or ""))
        if dformat == 32:
            #you should be using gdk_clipboard for atom support!
            if dtype in ("ATOM", "ATOM_PAIR") and os.name=="posix":
                #we cannot handle gdk atoms here (but gdk_clipboard does)
                return None, None
            #important note: on 64 bits, format=32 means 8 bytes, not 4
            #that's just the way it is...
            sizeof_long = struct.calcsize('@L')
            assert sizeof_long in (4, 8), "struct.calcsize('@L')=%s" % sizeof_long
            binfmt = "@" + "L" * (len(data) // sizeof_long)
            ints = struct.unpack(binfmt, data)
            return "integers", ints
        elif dformat == 16:
            sizeof_short = struct.calcsize('=H')
            assert sizeof_short == 2, "struct.calcsize('=H')=%s" % sizeof_short
            binfmt = "=" + "H" * (len(data) // sizeof_short)
            ints = struct.unpack(binfmt, data)
            return "integers", ints
        elif dformat == 8:
            for x in self.filter_res:
                if x.match(data):
                    log.warn("clipboard buffer contains blacklisted pattern '%s' and has been dropped!", x.pattern)
                    return None, None
            return "bytes", data
        else:
            log.error("unhandled format %s for clipboard data type %s" % (dformat, dtype))
            return None, None

    def _munge_wire_selection_to_raw(self, encoding, dtype, dformat, data):
        debug("wire selection to raw, encoding=%s, type=%s, format=%s, len(data)=%s", encoding, dtype, dformat, len(data))
        if encoding == "bytes":
            return data
        elif encoding == "integers":
            if dformat == 32:
                format_char = "L"
            elif dformat == 16:
                format_char = "H"
            elif dformat == 8:
                format_char = "B"
            else:
                raise Exception("unknown encoding format: %s" % dformat)
            fstr = "@" + format_char * len(data)
            debug("struct.pack(%s, %s)", fstr, data)
            return struct.pack(fstr, *data)
        else:
            raise Exception("unhanled encoding: %s" % encoding)

    def _process_clipboard_request(self, packet):
        request_id, selection, target = packet[1:4]
        def no_contents():
            self.send("clipboard-contents-none", request_id, selection)
        name = self.remote_to_local(selection)
        debug("process clipboard request, request_id=%s, selection=%s, local name=%s, target=%s", request_id, selection, name, target)
        proxy = self._clipboard_proxies.get(name)
        if proxy is None:
            #err, we were asked about a clipboard we don't handle..
            log.warn("ignoring clipboard request for '%s' (no proxy)", name)
            no_contents()
            return
        if not proxy.is_enabled():
            log.warn("ignoring clipboard request for '%s' (disabled)", name)
            no_contents()
            return
        if TEST_DROP_CLIPBOARD_REQUESTS>0 and (request_id % TEST_DROP_CLIPBOARD_REQUESTS)==0:
            log.warn("clipboard request %s dropped for testing!", request_id)
            return
        def got_contents(dtype, dformat, data):
            debug("got_contents(%s, %s, %s:%s) data=0x%s..",
                  dtype, dformat, type(data), len(data or ""), binascii.hexlify(str(data)[:200]))
            if dtype is None or data is None:
                no_contents()
                return
            munged = self._munge_raw_selection_to_wire(target, dtype, dformat, data)
            wire_encoding, wire_data = munged
            debug("clipboard raw -> wire: %r -> %r", (dtype, dformat, data), munged)
            if wire_encoding is None:
                no_contents()
                return
            wire_data = self._may_compress(dtype, dformat, wire_data)
            if wire_data:
                self.send("clipboard-contents", request_id, selection,
                       dtype, dformat, wire_encoding, wire_data)
        proxy.get_contents(target, got_contents)

    def _may_compress(self, dtype, dformat, wire_data):
        if len(wire_data)>256:
            wire_data = compressed_wrapper("clipboard: %s / %s" % (dtype, dformat), wire_data)
            if len(wire_data)>self.max_clipboard_packet_size:
                log.warn("even compressed, clipboard contents are too big and have not been sent:"
                         " %s compressed bytes dropped (maximum is %s)", len(wire_data), self.max_clipboard_packet_size)
                return  None
        return wire_data

    def _process_clipboard_contents(self, packet):
        request_id, selection, dtype, dformat, wire_encoding, wire_data = packet[1:8]
        debug("process clipboard contents, selection=%s, type=%s, format=%s", selection, dtype, dformat)
        raw_data = self._munge_wire_selection_to_raw(wire_encoding, dtype, dformat, wire_data)
        debug("clipboard wire -> raw: %r -> %r", (dtype, dformat, wire_encoding, wire_data), raw_data)
        self._clipboard_got_contents(request_id, dtype, dformat, raw_data)

    def _process_clipboard_contents_none(self, packet):
        debug("process clipboard contents none")
        request_id = packet[1]
        self._clipboard_got_contents(request_id, None, None, None)

    def _process_clipboard_pending_requests(self, packet):
        pending = packet[1]
        if self.progress_cb:
            self.progress_cb(None, pending)

    def process_clipboard_packet(self, packet):
        packet_type = packet[0]
        debug("process clipboard packet type=%s", packet_type)
        self._packet_handlers[packet_type](packet)


class DefaultClipboardProtocolHelper(ClipboardProtocolHelperBase):
    """
        Default clipboard implementation with all 3 selections.
        But without gdk atom support, see gdk_clipboard for a better one!
    """

    def __init__(self, send_packet_cb, progress_cb=None):
        ClipboardProtocolHelperBase.__init__(self, send_packet_cb, progress_cb, ["CLIPBOARD", "PRIMARY", "SECONDARY"])


class ClipboardProxy(gtk.Invisible):
    __gsignals__ = {
        # arguments: (selection, target)
        "get-clipboard-from-remote": (gobject.SIGNAL_RUN_LAST,
                                      gobject.TYPE_PYOBJECT,
                                      (gobject.TYPE_PYOBJECT,) * 2,
                                      ),
        # arguments: (selection,)
        "send-clipboard-token": n_arg_signal(1),
        }

    def __init__(self, selection):
        gtk.Invisible.__init__(self)
        self.add_events(PROPERTY_CHANGE_MASK)
        self._selection = selection
        self._clipboard = gtk.Clipboard(selection=selection)
        self._enabled = True
        self._have_token = False
        #this workaround is only needed on win32 AFAIK:
        self._strip_nullbyte = sys.platform.startswith("win")
        #clients that need a new token for every owner-change: (ie: win32 and osx)
        #(forces the client to request new contents - prevents stale clipboard data)
        self._greedy_client = False
        #semaphore to block the sending of the token when we change the owner ourselves:
        self._block_owner_change = False
        #counters for info:
        self._selection_request_events = 0
        self._selection_get_events = 0
        self._selection_clear_events = 0
        self._sent_token_events = 0
        self._got_token_events = 0
        self._get_contents_events = 0
        self._request_contents_events = 0

        try:
            from xpra.x11.gtk_x11.prop import prop_get
            self.prop_get = prop_get
        except ImportError:
            self.prop_get = None

        self._clipboard.connect("owner-change", self.do_owner_changed)

    def get_info(self):
        info = {"have_token"    : self._have_token,
                "enabled"       : self._enabled,
                "greedy_client" : self._greedy_client,
                "blocked_owner_change" : self._block_owner_change,
                "event.selection_request"   : self._selection_request_events,
                "event.selection_get"       : self._selection_get_events,
                "event.selection_clear"     : self._selection_clear_events,
                "event.got_token"           : self._got_token_events,
                "event.sent_token"          : self._sent_token_events,
                "event.get_contents"        : self._get_contents_events,
                "event.request_contents"    : self._request_contents_events,
                }
        return info

    def cleanup(self):
        if not self._have_token:
            self._clipboard.store()
        self.destroy()

    def is_enabled(self):
        return self._enabled

    def set_enabled(self, enabled):
        debug("%s.set_enabled(%s)", self, enabled)
        self._enabled = enabled

    def set_greedy_client(self, greedy):
        debug("%s.set_greedy_client(%s)", self, greedy)
        self._greedy_client = greedy

    def __str__(self):
        return  "ClipboardProxy(%s)" % self._selection

    def do_owner_changed(self, *args):
        debug("do_owner_changed(%s) greedy_client=%s, block_owner_change=%s", args, self._greedy_client, self._block_owner_change)
        if self._enabled and self._greedy_client and not self._block_owner_change:
            self._block_owner_change = True
            self._have_token = False
            self.emit("send-clipboard-token", self._selection)
            self._sent_token_events += 1
            gobject.idle_add(self.remove_block)

    def do_selection_request_event(self, event):
        debug("do_selection_request_event(%s)", event)
        self._selection_request_events += 1
        if not self._enabled:
            gtk.Invisible.do_selection_request_event(self, event)
            return
        # Black magic: the superclass default handler for this signal
        # implements all the hards parts of selection handling, occasionally
        # calling back to the do_selection_get handler (below) to actually get
        # the data to be sent.  However, it only does this for targets that
        # have been registered ahead of time; other targets fall through to a
        # default implementation that cannot be overridden.  So, we swoop in
        # ahead of time and add whatever target was requested to the list of
        # targets we want to handle!
        #
        # Special cases (magic targets defined by ICCCM):
        #   TIMESTAMP: the remote side has a different timeline than us, so
        #     sending TIMESTAMPS across the wire doesn't make any sense. We
        #     ignore TIMESTAMP requests, and let them fall through to GTK+'s
        #     default handler.
        #   TARGET: GTK+ has default handling for this, but we don't want to
        #     use it. Fortunately, if we tell GTK+ that we can handle TARGET
        #     requests, then it will pass them on to us rather than fall
        #     through to the default handler.
        #   MULTIPLE: Ugh. To handle this properly, we need to go out
        #     ourselves and fetch the magic property off the requesting window
        #     (with proper error trapping and all), and interpret its
        #     contents. Probably doable (FIXME), just a pain.
        #
        # Another special case is that if an app requests the contents of a
        # clipboard that it currently owns, then GTK+ will short-circuit the
        # normal logic and request the contents directly (i.e. it calls
        # gtk_selection_invoke_handler) -- without giving us a chance to
        # assert that we can handle the requested sort of target. Fortunately,
        # Xpra never needs to request the clipboard when it owns it, so that's
        # okay.
        assert str(event.selection) == self._selection
        target = str(event.target)
        if target == "TIMESTAMP":
            pass
        elif target == "MULTIPLE":
            if not self.prop_get:
                debug("MULTIPLE for property '%s' not handled due to missing xpra.x11.gtk_x11 bindings", event.property)
                gtk.Invisible.do_selection_request_event(self, event)
                return
            atoms = self.prop_get(event.window, event.property, ["multiple-conversion"])
            debug("MULTIPLE clipboard atoms: %r", atoms)
            if atoms:
                targets = atoms[::2]
                for t in targets:
                    self.selection_add_target(self._selection, t, 0)
        else:
            debug("target for %s: %r", self._selection, target)
            self.selection_add_target(self._selection, target, 0)
        debug("do_selection_request_event(%s) target=%s, selection=%s", event, target, self._selection)
        gtk.Invisible.do_selection_request_event(self, event)

    # This function is called by GTK+ when we own the clipboard and a local
    # app is requesting its contents:
    def do_selection_get(self, selection_data, info, time):
        # Either call selection_data.set() or don't, and then return.
        # In practice, send a call across the wire, then block in a recursive
        # main loop.
        if not self._enabled:
            return
        debug("do_selection_get(%s, %s, %s) selection=%s", selection_data, info, time, selection_data.selection)
        self._selection_get_events += 1
        assert self._selection == str(selection_data.selection)
        target = str(selection_data.target)
        self._request_contents_events += 1
        #check for clipboard loops:
        if gtk.main_level()>=20:
            log.warn("clipboard loop nesting too deep: %s", gtk.main_level())
            log.warn("you may have a clipboard forwarding loop, temporarily disabling the clipboard")
            log.warn("it may be best to disable the clipboard completely")
            self.set_enabled(False)
            gobject.timeout_add(60*1000, self.set_enabled, True)
            return
        result = self.emit("get-clipboard-from-remote", self._selection, target)
        if result is None or result["type"] is None:
            debug("remote selection fetch timed out or empty")
            selection_data.set("STRING", 8, "")
            return
        data = result["data"]
        dformat = result["format"]
        dtype = result["type"]
        debug("do_selection_get(%s,%s,%s) calling selection_data.set(%s, %s, %s:%s)",
              selection_data, info, time, dtype, dformat, type(data), len(data or ""))
        selection_data.set(dtype, dformat, data)

    def do_selection_clear_event(self, event):
        # Someone else on our side has the selection
        debug("do_selection_clear_event(%s) have_token=%s, block_owner_change=%s selection=%s", event, self._have_token, self._block_owner_change, self._selection)
        self._selection_clear_events += 1
        if self._enabled:
            #if greedy_client is set, do_owner_changed will fire the token
            #so don't bother sending it now (same if we don't have it)
            send = ((self._greedy_client and not self._block_owner_change) or self._have_token)
            self._have_token = False

            # Emit a signal -> send a note to the other side saying "hey its
            # ours now"
            # Send off the anti-token.
            if send:
                boc = self._block_owner_change
                self._block_owner_change = True
                self.emit("send-clipboard-token", self._selection)
                if boc is False:
                    gobject.idle_add(self.remove_block)
        gtk.Invisible.do_selection_clear_event(self, event)

    def got_token(self, targets, target_data):
        # We got the anti-token.
        debug("got token, selection=%s, targets=%s, target_data=%s", self._selection, targets, target_data)
        if not self._enabled:
            return
        self._got_token_events += 1
        self._have_token = True
        if self._greedy_client:
            self._block_owner_change = True
        self.claim()
        if self._block_owner_change:
            #re-enable the flag via idle_add so events like do_owner_changed
            #get a chance to run first.
            gobject.idle_add(self.remove_block)

    def remove_block(self, *args):
        log("remove_block(%s)", args)
        self._block_owner_change = False

    def claim(self):
        if self._enabled and not self.selection_owner_set(self._selection):
            # I don't know how this can actually fail, given that we pass
            # CurrentTime, but just in case:
            log.warn("Failed to acquire local clipboard %s; "
                     % (self._selection,)
                     + "will not be able to pass local apps "
                     + "contents of remote clipboard")


    # This function is called by the xpra core when the peer has requested the
    # contents of this clipboard:
    def get_contents(self, target, cb):
        debug("get_contents(%s,%s) selection=%s", target, cb, self._selection)
        if not self._enabled:
            cb(None, None, None)
            return
        self._get_contents_events += 1
        if self._have_token:
            log.warn("Our peer requested the contents of the clipboard, but "
                     + "*I* thought *they* had it... weird.")
            cb(None, None, None)
            return
        if target=="TARGETS":
            #handle TARGETS using "request_targets"
            def got_targets(c, targets, *args):
                debug("got_targets(%s, %s, %s)", c, targets, args)
                cb("ATOM", 32, targets)
            self._clipboard.request_targets(got_targets)
            return
        def unpack(clipboard, selection_data, user_data):
            debug("unpack %s: %s", clipboard, type(selection_data))
            if selection_data is None:
                cb(None, None, None)
                return
            debug("unpack: %s", selection_data)
            data = selection_data.data
            debug("unpack(..) type=%s, format=%s, data=%s:%s", selection_data.type, selection_data.format,
                        type(data), len(data or ""))
            if self._strip_nullbyte and selection_data.type in ("UTF8_STRING", "STRING") and selection_data.format==8:
                #we may have to strip the nullbyte:
                if data and data[-1]=='\0':
                    debug("stripping end of string null byte")
                    data = data[:-1]
            cb(str(selection_data.type), selection_data.format, data)
        self._clipboard.request_contents(target, unpack)

gobject.type_register(ClipboardProxy)
