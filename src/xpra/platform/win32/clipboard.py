# This file is part of Xpra.
# Copyright (C) 2019 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from ctypes import (
    sizeof, byref, cast,
    get_last_error, create_string_buffer,
    WinError, FormatError,
    )
from xpra.platform.win32.common import (
    WNDCLASSEX, GetLastError, ERROR_ACCESS_DENIED, WNDPROC, LPCWSTR, LPWSTR,
    DefWindowProcW,
    GetModuleHandleA, RegisterClassExW, UnregisterClassA,
    CreateWindowExW, DestroyWindow,
    OpenClipboard, EmptyClipboard, CloseClipboard, GetClipboardData,
    GlobalLock, GlobalUnlock, GlobalAlloc, GlobalFree,
    WideCharToMultiByte, MultiByteToWideChar,
    AddClipboardFormatListener, RemoveClipboardFormatListener,
    SetClipboardData)
from xpra.platform.win32 import win32con
from xpra.clipboard.clipboard_timeout_helper import ClipboardTimeoutHelper
from xpra.clipboard.clipboard_core import (
    ClipboardProxyCore, log, _filter_targets,
    TEXT_TARGETS, MAX_CLIPBOARD_PACKET_SIZE,
    )
from xpra.util import csv, repr_ellipsized
from xpra.os_util import bytestostr, strtobytes
from xpra.gtk_common.gobject_compat import import_glib

glib = import_glib()


CP_UTF8 = 65001
MB_ERR_INVALID_CHARS = 0x00000008
GMEM_MOVEABLE = 0x0002

WM_CLIPBOARDUPDATE = 0x031D

CLIPBOARD_EVENTS = {
    win32con.WM_CLEAR               : "CLEAR",
    win32con.WM_CUT                 : "CUT",
    win32con.WM_COPY                : "COPY",
    win32con.WM_PASTE               : "PASTE",
    win32con.WM_ASKCBFORMATNAME     : "ASKCBFORMATNAME",
    win32con.WM_CHANGECBCHAIN       : "CHANGECBCHAIN",
    WM_CLIPBOARDUPDATE              : "CLIPBOARDUPDATE",
    win32con.WM_DESTROYCLIPBOARD    : "DESTROYCLIPBOARD",
    win32con.WM_DRAWCLIPBOARD       : "DRAWCLIPBOARD",
    win32con.WM_HSCROLLCLIPBOARD    : "HSCROLLCLIPBOARD",
    win32con.WM_PAINTCLIPBOARD      : "PAINTCLIPBOARD",
    win32con.WM_RENDERALLFORMATS    : "RENDERALLFORMATS",
    win32con.WM_RENDERFORMAT        : "RENDERFORMAT",
    win32con.WM_SIZECLIPBOARD       : "SIZECLIPBOARD",
    win32con.WM_VSCROLLCLIPBOARD    : "WM_VSCROLLCLIPBOARD",
    }

#initialize the window we will use
#for communicating with the OS clipboard API:

class Win32Clipboard(ClipboardTimeoutHelper):
    """
        Use Native win32 API to access the clipboard
    """
    def __init__(self, send_packet_cb, progress_cb=None, **kwargs):
        self.init_window()
        ClipboardTimeoutHelper.__init__(self, send_packet_cb, progress_cb, **kwargs)

    def init_window(self):
        log("Win32Clipboard.init_window() creating clipboard window class and instance")
        class_name = "XpraWin32Clipboard"
        self.wndclass = WNDCLASSEX()
        self.wndclass.cbSize = sizeof(WNDCLASSEX)
        self.wndclass.lpfnWndProc = WNDPROC(self.wnd_proc)
        self.wndclass.style =  win32con.CS_GLOBALCLASS
        self.wndclass.hInstance = GetModuleHandleA(0)
        self.wndclass.lpszClassName = class_name
        self.wndclass_handle = RegisterClassExW(byref(self.wndclass))
        log("RegisterClassExA(%s)=%#x", self.wndclass.lpszClassName, self.wndclass_handle)
        if self.wndclass_handle==0:
            raise WinError()
        style = win32con.WS_CAPTION   #win32con.WS_OVERLAPPED
        self.window = CreateWindowExW(0, self.wndclass_handle, u"Clipboard", style,
                                      0, 0, win32con.CW_USEDEFAULT, win32con.CW_USEDEFAULT,
                                      win32con.HWND_MESSAGE, 0, self.wndclass.hInstance, None)
        log("clipboard window=%s", self.window)
        if not self.window:
            raise WinError()
        if not AddClipboardFormatListener(self.window):
            log.warn("Warning: failed to setup clipboard format listener")
            log.warn(" %s", get_last_error())

    def wnd_proc(self, hwnd, msg, wparam, lparam):
        r = DefWindowProcW(hwnd, msg, wparam, lparam)
        if msg in CLIPBOARD_EVENTS:
            log("clipboard event: %s", CLIPBOARD_EVENTS.get(msg))
        if msg==WM_CLIPBOARDUPDATE:
            for proxy in self._clipboard_proxies.values():
                if not proxy._block_owner_change:
                    proxy.schedule_emit_token()
        return r


    def cleanup(self):
        ClipboardTimeoutHelper.cleanup(self)
        self.cleanup_window()

    def cleanup_window(self):
        w = self.window
        if w:
            self.window = None
            RemoveClipboardFormatListener(w)
            DestroyWindow(w)
        wch = self.wndclass_handle
        if wch:
            self.wndclass = None
            self.wndclass_handle = None
            UnregisterClassA(wch, GetModuleHandleA(0))

    def make_proxy(self, selection):
        proxy = Win32ClipboardProxy(self.window, selection,
                                    self._send_clipboard_request_handler, self._send_clipboard_token_handler)
        proxy.set_want_targets(self._want_targets)
        proxy.set_direction(self.can_send, self.can_receive)
        return proxy

    ############################################################################
    # just pass ATOM targets through
    # (we use them internally as strings)
    ############################################################################
    def _munge_wire_selection_to_raw(self, encoding, dtype, dformat, data):
        if encoding=="atoms":
            return _filter_targets(data)
        return ClipboardTimeoutHelper._munge_wire_selection_to_raw(self, encoding, dtype, dformat, data)


class Win32ClipboardProxy(ClipboardProxyCore):
    def __init__(self, window, selection, send_clipboard_request_handler, send_clipboard_token_handler):
        self.window = window
        self.send_clipboard_request_handler = send_clipboard_request_handler
        self.send_clipboard_token_handler = send_clipboard_token_handler
        ClipboardProxyCore.__init__(self, selection)

    def set_want_targets(self, want_targets):
        self._want_targets = want_targets

    def with_clipboard_lock(self, success_callback, failure_callback, retries=5, delay=5):
        r = OpenClipboard(self.window)
        if r:
            try:
                success_callback()
                return
            finally:
                CloseClipboard()
        if GetLastError()!=ERROR_ACCESS_DENIED:
            failure_callback()
            return
        if retries<=0:
            failure_callback()
            return
        #try again later:
        glib.timeout_add(delay, self.with_clipboard_lock,
                         success_callback, failure_callback, retries-1, delay)

    def clear(self):
        def clear_error():
            log.error("Error: failed to clear the clipboard")
        self.with_clipboard_lock(EmptyClipboard, clear_error)

    def do_emit_token(self):
        #TODO: if contents are not text,
        #send just the token
        if self._greedy_client:
            target = "UTF8_STRING"
            def got_contents(dtype, dformat, data):
                packet_data = ([target], (target, dtype, dformat, data))
                self.send_clipboard_token_handler(self, packet_data)
            self.get_contents(target, got_contents)
        self.send_clipboard_token_handler(self)

    def get_contents(self, target, got_contents):
        def got_text(text):
            log("got_text(%s)", repr_ellipsized(str(text)))
            got_contents("bytes", 8, text)
        def errback(error_text):
            log.error("Error: failed to get clipboard data")
            log.error(" %s", error_text)
            got_contents("bytes", 8, b"")
        self.get_clipboard_text(got_text, errback)

    def got_token(self, targets, target_data=None, claim=True, _synchronous_client=False):
        # the remote end now owns the clipboard
        self.cancel_emit_token()
        if not self._enabled:
            return
        self._got_token_events += 1
        log("got token, selection=%s, targets=%s, target data=%s, claim=%s, can-receive=%s",
            self._selection, targets, target_data, claim, self._can_receive)
        if self._can_receive:
            self.targets = _filter_targets(targets or ())
            self.target_data = target_data or {}
            if targets:
                self.got_contents("TARGETS", "ATOM", 32, targets)
            if target_data:
                for target, td_def in target_data.items():
                    dtype, dformat, data = td_def
                    dtype = bytestostr(dtype)
                    self.got_contents(target, dtype, dformat, data)
            #since we claim to be greedy
            #the peer should have sent us the target and target_data,
            #if not then request it:
            if not targets:
                self.send_clipboard_request_handler(self, self._selection, "TARGETS")
        if not claim:
            log("token packet without claim, not setting the token flag")
            return
        self._have_token = True
        if self._can_receive:
            self.claim()

    def got_contents(self, target, dtype=None, dformat=None, data=None):
        #if this is the special target 'TARGETS', cache the result:
        if target=="TARGETS" and dtype=="ATOM" and dformat==32:
            self.targets = _filter_targets(data)
            #TODO: tell system what targets we have
            log("got_contents: tell OS we have %s", csv(self.targets))
        if dformat==8 and dtype in TEXT_TARGETS:
            log("we got a byte string: %s", data)
            self.set_clipboard_text(data)


    def get_clipboard_text(self, callback, errback):
        def get_text():
            data_handle = GetClipboardData(win32con.CF_UNICODETEXT)
            if not data_handle:
                errback("no data handle")
                return
            data = GlobalLock(data_handle)
            if not data:
                errback("failed to lock handle")
                return
            try:
                wstr = cast(data, LPCWSTR)
                ulen = WideCharToMultiByte(CP_UTF8, 0, wstr, -1, None, 0, None, None)
                if ulen>MAX_CLIPBOARD_PACKET_SIZE:
                    errback("too much data")
                    return
                buf = create_string_buffer(ulen)
                l = WideCharToMultiByte(CP_UTF8, 0, wstr, -1, byref(buf), ulen, None, None)
                if l>0:
                    if buf.raw[l-1:l]==b"\0":
                        s = buf.raw[:l-1]
                    else:
                        s = buf.raw[:l]
                    log("got %i bytes of data: %s", len(s), repr_ellipsized(str(s)))
                    callback(strtobytes(s))
                else:
                    errback("failed to convert to UTF8: %s" % FormatError(get_last_error()))
            finally:
                GlobalUnlock(data)
        self.with_clipboard_lock(get_text, errback)

    def set_clipboard_text(self, text):
        #convert to wide char
        #get the length in wide chars:
        log("set_clipboard_text(%s)", text)
        wlen = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text, len(text), None, 0)
        if not wlen:
            return
        log("MultiByteToWideChar wlen=%i", wlen)
        #allocate some memory for it:
        buf = GlobalAlloc(GMEM_MOVEABLE, (wlen+1)*2)
        if not buf:
            return
        log("GlobalAlloc buf=%#x", buf)
        locked = GlobalLock(buf)
        if not locked:
            GlobalFree(buf)
            return
        try:
            locked_buf = cast(locked, LPWSTR)
            r = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text, len(text), locked_buf, wlen)
            if not r:
                return
        finally:
            GlobalUnlock(locked)

        def do_set_data():
            try:
                self._block_owner_change = True
                log("SetClipboardData(..) block_owner_change=%s", self._block_owner_change)
                EmptyClipboard()
                if not SetClipboardData(win32con.CF_UNICODETEXT, buf):
                    return
                #done!
            finally:
                GlobalFree(buf)
                glib.idle_add(self.remove_block)
        def set_error():
            GlobalFree(buf)
            log.error("Error: failed to set clipboard data")
        self.with_clipboard_lock(do_set_data, set_error)

    def __repr__(self):
        return "Win32ClipboardProxy"
