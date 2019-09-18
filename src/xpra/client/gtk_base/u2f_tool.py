#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2018-2019 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import sys
import glob
import os.path

from xpra.util import engs
from xpra.os_util import hexstr, osexpand, load_binary_file, use_tty
from xpra.platform.paths import get_user_conf_dirs
from xpra.log import Logger

log = Logger("auth", "util")

APP_ID = os.environ.get("XPRA_U2F_APP_ID", "Xpra")


def main():
    from xpra.platform import program_context
    with program_context("U2F-Register", "Xpra U2F Registration Tool"):
        if not use_tty():
            from gi.repository import GLib, Gtk
            def show_dialog(mode, *msgs):
                dialog = Gtk.MessageDialog(None, 0, mode,
                              Gtk.ButtonsType.CLOSE, "\n".join(msgs))
                dialog.set_title("Xpra U2F Registration Tool")
                v = dialog.run()
                dialog.destroy()
                #run the main loop long enough to destroy the dialog:
                GLib.idle_add(Gtk.main_quit)
                Gtk.main()
                return v
            def error(*msgs):
                return show_dialog(Gtk.MessageType.ERROR, *msgs)
            def info(*msgs):
                return show_dialog(Gtk.MessageType.INFO, *msgs)
        else:
            print("U2F Registration Tool")
            def printmsgs(*msgs):
                for x in msgs:
                    print(x)
            error = info = printmsgs

        key_handle_filenames = [os.path.join(d, "u2f-keyhandle.hex") for d in get_user_conf_dirs()]
        assert key_handle_filenames
        for filename in key_handle_filenames:
            p = osexpand(filename)
            key_handle_str = load_binary_file(p)
            if key_handle_str:
                error(" found an existing key handle in file '%s':" % p,
                      #" %s" % key_handle_str,
                      " skipping U2F registration",
                      " delete this file if you want to register again")
                return 1
        public_key_filenames = []
        for d in get_user_conf_dirs():
            public_key_filenames += glob.glob(os.path.join(d, "u2f*.pub"))
        if public_key_filenames:
            info(" found %i existing public key%s" % (len(public_key_filenames, engs(public_key_filenames))),
                 *((" - %s" % x) for x in public_key_filenames))

        #pick the first directory:
        conf_dir = osexpand(get_user_conf_dirs()[0])
        if not os.path.exists(conf_dir):
            os.mkdir(conf_dir)

        from pyu2f.u2f import GetLocalU2FInterface      #@UnresolvedImport
        try:
            dev = GetLocalU2FInterface()
        except Exception as e:
            error("Failed to open local U2F device:",
                  "%s" % (str(e) or type(e)))
            return 1

        info("Please activate your U2F device now to generate a new key")
        registered_keys = []
        challenge= b'01234567890123456789012345678901'  #unused
        rr = dev.Register(APP_ID, challenge, registered_keys)
        b = rr.registration_data
        assert b[0]==5
        pubkey = bytes(b[1:66])
        khl = b[66]
        key_handle = bytes(b[67:67 + khl])

        #save to files:
        key_handle_filename = osexpand(key_handle_filenames[0])
        with open(key_handle_filename, "wb") as f:
            f.write(hexstr(key_handle).encode())
        #find a filename we can use for this public key:
        i = 1
        while True:
            c = ""
            if i>1:
                c = "-%i"
            public_key_filename = os.path.join(conf_dir, "u2f%s-pub.hex" % c)
            if not os.path.exists(public_key_filename):
                break
        with open(public_key_filename, "wb") as f:
            f.write(hexstr(pubkey).encode())
        #info("key handle: %s" % csv(hex40(key_handle)),
        #     "saved to file '%s'" % key_handle_filename,
        #     "public key: %s" % csv(hex40(pubkey)),
        #     "saved to file '%s'" % public_key_filename,
        #     )
        info(
            "key handle saved to file:",
            "'%s'" % key_handle_filename,
            "public key saved to file:",
            "'%s'" % public_key_filename,
            )
        return 0


if __name__ == "__main__":
    sys.exit(main())
