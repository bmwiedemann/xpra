# This file is part of Xpra.
# Copyright (C) 2018 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

"""
Utility functions for loading xdg menus
using python-xdg
"""

import os
import sys
import glob

from xpra.util import envbool, print_nested_dict
from xpra.os_util import load_binary_file, BytesIOClass, OSEnvContext, PYTHON3
from xpra.log import Logger, add_debug_category

log = Logger("exec", "menu")

LOAD_GLOB = envbool("XPRA_XDG_LOAD_GLOB", True)
EXPORT_ICONS = envbool("XPRA_XDG_EXPORT_ICONS", True)
DEBUG_COMMANDS = os.environ.get("XPRA_XDG_DEBUG_COMMANDS", "").split(",")
if PYTHON3:
    unicode = str           #@ReservedAssignment
    from typing import Generator as generator       #@UnresolvedImport, @UnusedImport
else:
    from types import GeneratorType as generator    #@Reimport


def isvalidtype(v):
    if isinstance(v, (list, tuple, generator)):
        if not v:
            return True
        return all(isvalidtype(x) for x in v)
    return isinstance(v, (bytes, str, unicode, bool, int))

def export(entry, properties):
    name = entry.getName()
    props = {}
    if any(x and name.lower().find(x.lower())>=0 for x in DEBUG_COMMANDS):
        l = log.info
    else:
        l = log
    for prop in properties:
        fn_name = "get%s" % prop
        try:
            fn = getattr(entry, fn_name, None)
            if fn:
                v = fn()
                if isinstance(v, (list, tuple, generator)):
                    l("%s=%s (%s)", prop, v, type(x for x in v))
                else:
                    l("%s=%s (%s)", prop, v, type(v))
                if not isvalidtype(v):
                    log.warn("Warning: found invalid type for '%s': %s", v, type(v))
                else:
                    props[prop] = v
        except Exception as e:
            l("error on %s", entry, exc_info=True)
            log.error("Error parsing '%s': %s", prop, e)
    l("properties(%s)=%s", name, props)
    if EXPORT_ICONS:
        #load icon binary data:
        icon = props.get("Icon")
        icondata = load_icon_from_theme(icon)
        if icondata:
            bdata, ext = icondata
            props["IconData"] = bdata
            props["IconType"] = ext
    return props

def load_icon_from_file(filename):
    if filename.endswith("xpm"):
        try:
            from xpra.gtk_common.gobject_compat import import_pixbufloader
            from xpra.gtk_common.gtk_util import pixbuf_save_to_memory
            data = load_binary_file(filename)
            loader = import_pixbufloader()()
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
            pngicondata = pixbuf_save_to_memory(pixbuf, "png")
            return pngicondata, "png"
        except Exception as e:
            log("pixbuf error loading %s", filename, exc_info=True)
            log.error("Error loading '%s':", filename)
            log.error(" %s", e)
        #try PIL:
        from PIL import Image
        try:
            img = Image.open(filename)
        except Exception as e:
            log("Image.open(%s)", filename, exc_info=True)
            log.error("Error loading '%s':", filename)
            log.error(" %s", e)
            return None
        buf = BytesIOClass()
        img.save(buf, "PNG")
        pngicondata = buf.getvalue()
        buf.close()
        return pngicondata, "png"
    icondata = load_binary_file(filename)
    if not icondata:
        return None
    log("got icon data from '%s': %i bytes", filename, len(icondata))
    return icondata, os.path.splitext(filename)[1].rstrip(".")

def load_icon_from_theme(icon_name, theme=None):
    if not EXPORT_ICONS or not icon_name:
        return None
    from xdg import IconTheme
    filename = IconTheme.getIconPath(icon_name, theme=theme)
    if not filename:
        return None
    return load_icon_from_file(filename)

def load_glob_icon(submenu_data, main_dirname="categories"):
    if not LOAD_GLOB:
        return None
    #doesn't work with IconTheme.getIconPath,
    #so do it the hard way:
    from xdg import IconTheme
    icondirs = getattr(IconTheme, "icondirs", [])
    if not icondirs:
        return None
    for x in ("Icon", "Name", "GenericName"):
        name = submenu_data.get(x)
        if name:
            icondata = find_icon(main_dirname, icondirs, name)
            if icondata:
                return icondata
    return None

def find_icon(main_dirname, icondirs, name):
    extensions = ("png", "svg", "xpm")
    pathnames = []
    for dn in (main_dirname, "*"):
        for d in icondirs:
            for ext in extensions:
                pathnames += [
                    os.path.join(d, "*", "*", dn, "%s.%s" % (name, ext)),
                    os.path.join(d, "*", dn, "*", "%s.%s" % (name, ext)),
                    ]
    for pathname in pathnames:
        filenames = glob.glob(pathname)
        if filenames:
            for f in filenames:
                icondata = load_icon_from_file(f)
                if icondata:
                    log("found icon for '%s' with glob '%s': %s", name, pathname, f)
                    return icondata
    return None


def load_xdg_entry(de):
    #not exposed:
    #"MimeType" is an re
    #"Version" is a float
    props = export(de, (
        "Type", "VersionString", "Name", "GenericName", "NoDisplay",
        "Comment", "Icon", "Hidden", "OnlyShowIn", "NotShowIn",
        "Exec", "TryExec", "Path", "Terminal", "MimeTypes",
        "Categories", "StartupNotify", "StartupWMClass", "URL",
        ))
    if de.getTryExec():
        try:
            command = de.findTryExec()
        except Exception:
            command = de.getTryExec()
    else:
        command = de.getExec()
    props["command"] = command
    icondata = props.get("IconData")
    if not icondata:
        #try harder:
        icondata = load_glob_icon(de, "apps")
        if icondata:
            bdata, ext = icondata
            props["IconData"] = bdata
            props["IconType"] = ext
    return props

def load_xdg_menu(submenu):
    #log.info("submenu %s: %s, %s", name, submenu, dir(submenu))
    submenu_data = export(submenu, [
        "Name", "GenericName", "Comment",
        "Path", "Icon",
        ])
    icondata = submenu_data.get("IconData")
    if not icondata:
        #try harder:
        icondata = load_glob_icon(submenu_data, "categories")
        if icondata:
            bdata, ext = icondata
            submenu_data["IconData"] = bdata
            submenu_data["IconType"] = ext
    entries_data = submenu_data.setdefault("Entries", {})
    for entry in submenu.getEntries():
        #can we have more than 2 levels of submenus?
        from xdg.Menu import MenuEntry
        if isinstance(entry, MenuEntry):
            de = entry.DesktopEntry
            name = de.getName()
            try:
                entries_data[name] = load_xdg_entry(de)
            except Exception as e:
                log("load_xdg_menu(%s)", submenu, exc_info=True)
                log.error("Error loading desktop entry '%s':", name)
                log.error(" %s", e)
    return submenu_data


xdg_menu_data = None
def load_xdg_menu_data():
    global xdg_menu_data
    if not xdg_menu_data:
        xdg_menu_data = do_load_xdg_menu_data()
    return xdg_menu_data

def do_load_xdg_menu_data():
    try:
        from xdg.Menu import parse, Menu, ParsingError
    except ImportError:
        log("do_load_xdg_menu_data()", exc_info=True)
        log.warn("Warning: cannot use application menu data:")
        log.warn(" no python-xdg module")
        return None
    menu = None
    error = None
    with OSEnvContext():
        #see ticket #2174,
        #things may break if the prefix is not set,
        #and it isn't set when logging in via ssh
        for prefix in (None, "", "gnome-", "kde-"):
            if prefix is not None:
                os.environ["XDG_MENU_PREFIX"] = prefix
            try:
                menu = parse()
                break
            except ParsingError as e:
                log("do_load_xdg_menu_data()", exc_info=True)
                error = e
                menu = None
    if menu is None:
        if error:
            log.error("Error parsing xdg menu data:")
            log.error(" %s", error)
            log.error(" this is either a bug in python-xdg,")
            log.error(" or an invalid system menu configuration")
        return None
    menu_data = {}
    for submenu in menu.getEntries():
        if isinstance(submenu, Menu) and submenu.Visible:
            name = submenu.getName()
            try:
                menu_data[name] = load_xdg_menu(submenu)
            except Exception as e:
                log("load_xdg_menu_data()", exc_info=True)
                log.error("Error loading submenu '%s':", name)
                log.error(" %s", e)
    return menu_data


def main():
    from xpra.platform import program_context
    with program_context("XDG-Menu-Helper", "XDG Menu Helper"):
        for x in list(sys.argv):
            if x in ("-v", "--verbose"):
                sys.argv.remove(x)
                add_debug_category("menu")
                log.enable_debug()
        def icon_fmt(icondata):
            return "%i bytes" % len(icondata)
        menu = load_xdg_menu_data()
        print_nested_dict(menu, vformat={"IconData" : icon_fmt})
    return 0

if __name__ == "__main__":
    v = main()
    sys.exit(v)
