# This file is part of Xpra.
# Copyright (C) 2011 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2010-2014 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008, 2010 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.client.keyboard_helper import KeyboardHelper, log
from xpra.gtk_common.gobject_compat import import_gdk, import_glib
from xpra.gtk_common.keymap import get_gtk_keymap
from xpra.gtk_common.gtk_util import display_get_default
gdk = import_gdk()
glib = import_glib()


class GTKKeyboardHelper(KeyboardHelper):

    def __init__(self, net_send, keyboard_sync, key_shortcuts):
        KeyboardHelper.__init__(self, net_send, keyboard_sync, key_shortcuts)
        #used for delaying the sending of keymap changes
        #(as we may be getting dozens of such events at a time)
        self._keymap_changing = False
        self._keymap_change_handler_id = None
        try:
            self._keymap = gdk.keymap_get_default()
        except:
            self._keymap = None
        self.update()
        if self._keymap:
            self._keymap_change_handler_id = self._keymap.connect("keys-changed", self._keys_changed)

    def _keys_changed(self, *args):
        log("keys_changed")
        if self._keymap_change_handler_id:
            self._keymap.disconnect(self._keymap_change_handler_id)
            self._keymap_change_handler_id = None
        self._keymap = gdk.keymap_get_default()
        self._keymap_change_handler_id = self._keymap.connect("keys-changed", self._keys_changed)
        if self._keymap_changing:
            #timer due already
            return
        self._keymap_changing = True
        def do_keys_changed():
            self._keymap_changing = False
            if self.locked:
                #automatic changes not allowed!
                log.info("ignoring keymap change: layout is locked to '%s'", self.layout_str())
                return
            if self.update():
                log.info("keymap has been changed to '%s', sending updated mappings to the server", self.layout_str())
                if self.xkbmap_layout:
                    self.send_layout()
                self.send_keymap()
        glib.timeout_add(500, do_keys_changed)

    def update(self):
        old_hash = self.hash
        self.query_xkbmap()
        try:
            self.keyboard.update_modifier_map(display_get_default(), self.xkbmap_mod_meanings)
        except:
            log.error("error querying modifier map", exc_info=True)
        log("do_keys_changed() modifier_map=%s, old hash=%s, new hash=%s", self.keyboard.modifier_map, old_hash, self.hash)
        return old_hash!=self.hash

    def get_full_keymap(self):
        return  get_gtk_keymap()

    def cleanup(self):
        KeyboardHelper.cleanup(self)
        if self._keymap_change_handler_id:
            try:
                self._keymap.disconnect(self._keymap_change_handler_id)
                self._keymap_change_handler_id = None
            except Exception as e:
                log.warn("failed to disconnect keymap change handler: %s", e)


def main():
    #use gtk as display source:
    from xpra.x11.gtk2 import gdk_display_source
    assert gdk_display_source
    from xpra.util import nonl
    from xpra.platform import init as platform_init
    platform_init("GTK-Keyboard", "GTK Keyboard")

    x = GTKKeyboardHelper(None, True, "")
    x.query_xkbmap()
    for k,v in x.get_keymap_properties().items():
        print("%s=%s" % (k,nonl(v)))

if __name__ == "__main__":
    main()
