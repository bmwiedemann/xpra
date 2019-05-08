# This file is part of Xpra.
# Copyright (C) 2010-2017 Antoine Martin <antoine@xpra.org>
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

# DO NOT IMPORT GTK HERE: see
#  http://lists.partiwm.org/pipermail/parti-discuss/2008-September/000041.html
#  http://lists.partiwm.org/pipermail/parti-discuss/2008-September/000042.html
# (also do not import anything that imports gtk)

def verify_gdk_display(display_name):
    # Now we can safely load gtk and connect:
    from xpra.gtk_common.gobject_compat import import_gdk2, import_glib
    gdk = import_gdk2()
    #this will call glib.threads_init()
    import_glib()
    display = gdk.Display(display_name)
    manager = gdk.display_manager_get()
    default_display = manager.get_default_display()
    if default_display is not None and default_display!=display:
        default_display.close()
    manager.set_default_display(display)
    return display
