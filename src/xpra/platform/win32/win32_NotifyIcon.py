#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2011-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

# Low level support for the "system tray" on MS Windows
# Based on code from winswitch, itself based on "win32gui_taskbar demo"

import win32api                    #@UnresolvedImport
import win32gui                    #@UnresolvedImport
import win32con                    #@UnresolvedImport

import sys, os

from xpra.log import Logger
log = Logger("tray", "win32")

#found here:
#http://msdn.microsoft.com/en-us/library/windows/desktop/ff468877(v=vs.85).aspx
WM_XBUTTONDOWN  = 0x020B
WM_XBUTTONUP    = 0x020C
WM_XBUTTONDBLCLK= 0x020D

BUTTON_MAP = {
            win32con.WM_LBUTTONDOWN     : [(1, 1)],
            win32con.WM_LBUTTONUP       : [(1, 0)],
            win32con.WM_MBUTTONDOWN     : [(2, 1)],
            win32con.WM_MBUTTONUP       : [(2, 0)],
            win32con.WM_RBUTTONDOWN     : [(3, 1)],
            win32con.WM_RBUTTONUP       : [(3, 0)],
            win32con.WM_LBUTTONDBLCLK   : [(1, 1), (1, 0)],
            win32con.WM_MBUTTONDBLCLK   : [(2, 1), (2, 0)],
            win32con.WM_RBUTTONDBLCLK   : [(3, 1), (3, 0)],
            WM_XBUTTONDOWN              : [(4, 1)],
            WM_XBUTTONUP                : [(4, 0)],
            WM_XBUTTONDBLCLK            : [(4, 1), (4, 0)],
            }

FALLBACK_ICON = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)


class win32NotifyIcon(object):

    click_callbacks = {}
    move_callbacks = {}
    exit_callbacks = {}
    command_callbacks = {}
    live_hwnds = set()

    def __init__(self, title, move_callbacks, click_callback, exit_callback, command_callback=None, iconPathName=None):
        self.title = title[:127]
        self.current_icon = None
        # Register the Window class.
        self.hinst = NIwc.hInstance
        # Create the Window.
        style = win32con.WS_OVERLAPPED | win32con.WS_SYSMENU
        self.hwnd = win32gui.CreateWindow(NIclassAtom, self.title+" StatusIcon Window", style, \
            0, 0, win32con.CW_USEDEFAULT, win32con.CW_USEDEFAULT, \
            0, 0, self.hinst, None)
        win32gui.UpdateWindow(self.hwnd)
        self.current_icon = self.LoadImage(iconPathName)
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, self.make_nid(win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP))
        #register callbacks:
        win32NotifyIcon.live_hwnds.add(self.hwnd)
        win32NotifyIcon.move_callbacks[self.hwnd] = move_callbacks
        win32NotifyIcon.click_callbacks[self.hwnd] = click_callback
        win32NotifyIcon.exit_callbacks[self.hwnd] = exit_callback
        win32NotifyIcon.command_callbacks[self.hwnd] = command_callback

    def make_nid(self, flags):
        return (self.hwnd, 0, flags, WM_TRAY_EVENT, self.current_icon, self.title)

    def set_blinking(self, on):
        #FIXME: implement blinking on win32 using a timer
        pass

    def set_tooltip(self, name):
        self.title = name[:127]
        win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, self.make_nid(win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP))

    def set_icon(self, iconPathName):
        hicon = self.LoadImage(iconPathName)
        self.do_set_icon(hicon)
        win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, self.make_nid(win32gui.NIF_ICON))

    def do_set_icon(self, hicon):
        log("do_set_icon(%s)", hicon)
        self.current_icon = hicon
        win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, self.make_nid(win32gui.NIF_ICON))


    def set_icon_from_data(self, pixels, has_alpha, w, h, rowstride):
        #TODO: use native code somehow to avoid saving to file
        log("set_icon_from_data%s", ("%s pixels" % len(pixels), has_alpha, w, h, rowstride))
        from PIL import Image, ImageOps           #@UnresolvedImport
        if has_alpha:
            rgb_format = "RGBA"
        else:
            rgb_format = "RGB"
        img = Image.frombuffer(rgb_format, (w, h), pixels, "raw", rgb_format, 0, 1)
        #apparently, we have to use SM_CXSMICON (small icon) and not SM_CXICON (regular size):
        size = win32api.GetSystemMetrics(win32con.SM_CXSMICON)
        if w!=h or w!=size:
            img = img.resize((size, size), Image.ANTIALIAS)
        if has_alpha:
            #extract alpha channel as mask into an inverted "L" channel image:
            data_fn = getattr(img, "tobytes", getattr(img, "tostring", None))
            alpha = data_fn("raw", "A")
            from_fn = getattr(Image, "frombytes", getattr(Image, "fromstring", None))
            mask = from_fn("L", img.size, alpha)
            mask = ImageOps.invert(mask)
            #strip alpha from pixels:
            img = img.convert("RGB")
        else:
            #no alpha: just use image as mask:
            mask = img

        def img_to_bitmap(image, pixel_value):
            hdc = win32gui.CreateCompatibleDC(0)
            dc = win32gui.GetDC(0)
            hbm = win32gui.CreateCompatibleBitmap(dc, size, size)
            hbm_save = win32gui.SelectObject(hdc, hbm)
            for x in range(size):
                for y in range(size):
                    pixel = image.getpixel((x, y))
                    v = pixel_value(pixel)
                    win32gui.SetPixelV(hdc, x, y, v)
            win32gui.SelectObject(hdc, hbm_save)
            win32gui.ReleaseDC(self.hwnd, hdc)
            win32gui.ReleaseDC(self.hwnd, dc)
            return hbm

        hicon = FALLBACK_ICON
        try:
            def rgb_pixel(pixel):
                r, g, b = pixel[:3]
                return r+g*256+b*256*256
            bitmap = img_to_bitmap(img, rgb_pixel)
            if mask is img:
                mask_bitmap = bitmap
            else:
                #mask is in "L" mode, so we get the pixel value directly from getpixel(x, y)
                def mask_pixel(l):
                    return l+l*256+l*256*256
                mask_bitmap = img_to_bitmap(mask, mask_pixel)
            if mask_bitmap:
                pyiconinfo = (True, 0, 0, mask_bitmap, bitmap)
                hicon = win32gui.CreateIconIndirect(pyiconinfo)
                log("CreateIconIndirect(%s)=%s", pyiconinfo, hicon)
                if hicon==0:
                    hicon = FALLBACK_ICON
            self.do_set_icon(hicon)
            win32gui.UpdateWindow(self.hwnd)
        except:
            log.error("error setting icon", exc_info=True)
        finally:
            #DeleteDC(dc)
            if hicon!=FALLBACK_ICON:
                win32gui.DestroyIcon(hicon)


    def LoadImage(self, iconPathName, fallback=FALLBACK_ICON):
        v = fallback
        if iconPathName:
            icon_flags = win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE
            try:
                img_type = win32con.IMAGE_ICON
                if iconPathName.lower().split(".")[-1] in ("png", "bmp"):
                    img_type = win32con.IMAGE_BITMAP
                    icon_flags |= win32con.LR_CREATEDIBSECTION | win32con.LR_LOADTRANSPARENT
                log("LoadImage(%s) using image type=%s", iconPathName,
                                            {win32con.IMAGE_ICON    : "ICON",
                                             win32con.IMAGE_BITMAP  : "BITMAP"}.get(img_type))
                v = win32gui.LoadImage(self.hinst, iconPathName, img_type, 0, 0, icon_flags)
            except:
                log.error("Failed to load icon at %s", iconPathName, exc_info=True)
        log("LoadImage(%s)=%s", iconPathName, v)
        return v

    @classmethod
    def restart(cls):
        #FIXME: keep current icon and repaint it on restart
        pass

    @classmethod
    def remove_callbacks(cls, hwnd):
        for x in (cls.command_callbacks, cls.exit_callbacks, cls.click_callbacks):
            if hwnd in x:
                del x[hwnd]

    @classmethod
    def OnCommand(cls, hwnd, msg, wparam, lparam):
        cc = cls.command_callbacks.get(hwnd)
        log("OnCommand(%s,%s,%s,%s) command callback=%s", hwnd, msg, wparam, lparam, cc)
        if cc:
            cid = win32api.LOWORD(wparam)
            cc(hwnd, cid)

    @classmethod
    def OnDestroy(cls, hwnd, msg, wparam, lparam):
        ec = cls.exit_callbacks.get(hwnd)
        log("OnDestroy(%s,%s,%s,%s) exit_callback=%s", hwnd, msg, wparam, lparam, ec)
        if hwnd not in cls.live_hwnds:
            return
        cls.live_hwnds.remove(hwnd)
        cls.remove_callbacks(hwnd)
        try:
            nid = (hwnd, 0)
            log("OnDestroy(..) calling Shell_NotifyIcon(NIM_DELETE, %s)", nid)
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, nid)
            log("OnDestroy(..) calling exit_callback=%s", ec)
            if ec:
                ec()
        except:
            log.error("OnDestroy(..)", exc_info=True)

    @classmethod
    def OnTaskbarNotify(cls, hwnd, msg, wparam, lparam):
        if lparam==win32con.WM_MOUSEMOVE:
            cc = cls.move_callbacks.get(hwnd)
            bm = [(hwnd, msg, wparam, lparam)]
        else:
            cc = cls.click_callbacks.get(hwnd)
            bm = BUTTON_MAP.get(lparam)
        log("OnTaskbarNotify(%s,%s,%s,%s) button(s) lookup: %s, callback=%s", hwnd, msg, wparam, lparam, bm, cc)
        if bm is not None and cc:
            for button_event in bm:
                cc(*button_event)
        return 1

    def close(self):
        log("win32NotifyIcon.close()")
        win32NotifyIcon.remove_callbacks(self.hwnd)
        win32NotifyIcon.OnDestroy(self.hwnd, None, None, None)


WM_TRAY_EVENT = win32con.WM_USER+20        #a message id we choose
message_map = {
    win32gui.RegisterWindowMessage("TaskbarCreated") : win32NotifyIcon.restart,
    win32con.WM_DESTROY                 : win32NotifyIcon.OnDestroy,
    win32con.WM_COMMAND                 : win32NotifyIcon.OnCommand,
    WM_TRAY_EVENT                       : win32NotifyIcon.OnTaskbarNotify,
}
NIwc = win32gui.WNDCLASS()
NIwc.hInstance = win32api.GetModuleHandle(None)
NIwc.lpszClassName = "win32NotifyIcon"
NIwc.lpfnWndProc = message_map # could also specify a wndproc.
NIclassAtom = win32gui.RegisterClass(NIwc)




def main():
    def notify_callback(hwnd):
        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu( menu, win32con.MF_STRING, 1024, "Generate balloon")
        win32gui.AppendMenu( menu, win32con.MF_STRING, 1025, "Exit")
        pos = win32api.GetCursorPos()
        win32gui.SetForegroundWindow(hwnd)
        win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN, pos[0], pos[1], 0, hwnd, None)
        win32api.PostMessage(hwnd, win32con.WM_NULL, 0, 0)

    def command_callback(hwnd, cid):
        if cid == 1024:
            from xpra.platform.win32.win32_balloon import notify
            notify(hwnd, "hello", "world")
        elif cid == 1025:
            print("Goodbye")
            win32gui.DestroyWindow(hwnd)
        else:
            print("OnCommand for ID=%s" % cid)

    def win32_quit():
        win32gui.PostQuitMessage(0) # Terminate the app.

    iconPathName = os.path.abspath(os.path.join( sys.prefix, "pyc.ico"))
    win32NotifyIcon(notify_callback, win32_quit, command_callback, iconPathName)
    win32gui.PumpMessages()


if __name__=='__main__':
    main()
