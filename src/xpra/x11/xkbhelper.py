# This file is part of Xpra.
# Copyright (C) 2011-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import re

#ensure that we use gtk as display source:
from xpra.x11.gtk_x11 import gdk_display_source
assert gdk_display_source

from xpra.util import std
from xpra.keyboard.layouts import parse_xkbmap_query
from xpra.gtk_common.error import trap
from xpra.x11.bindings.keyboard_bindings import X11KeyboardBindings #@UnresolvedImport
X11Keyboard = X11KeyboardBindings()

from xpra.log import Logger
log = Logger("x11", "keyboard")
vlog = Logger("x11", "keyboard", "verbose")


def exec_keymap_command(args, stdin=None):
    try:
        from xpra.scripts.exec_util import safe_exec
        returncode, _, _ = safe_exec(args, stdin)
        def logstdin():
            if not stdin or len(stdin)<32:
                return  stdin
            return stdin[:30].replace("\n", "\\n")+".."
        if returncode==0:
            if not stdin:
                log("%s", args)
            else:
                log("%s with stdin=%s", args, logstdin())
        else:
            log.error("%s with stdin=%s, failed with exit code %s", args, logstdin(), returncode)
        return returncode
    except Exception, e:
        log.error("error calling '%s': %s" % (str(args), e))
        return -1


def clean_keyboard_state():
    try:
        X11Keyboard.ungrab_all_keys()
    except:
        log.error("error ungrabbing keys", exc_info=True)
    try:
        X11Keyboard.unpress_all_keys()
    except:
        log.error("error unpressing keys", exc_info=True)

################################################################################
# keyboard layouts

def do_set_keymap(xkbmap_layout, xkbmap_variant,
                  xkbmap_print, xkbmap_query):
    """ xkbmap_layout is the generic layout name (used on non posix platforms)
        xkbmap_variant is the layout variant (may not be set)
        xkbmap_print is the output of "setxkbmap -print" on the client
        xkbmap_query is the output of "setxkbmap -query" on the client
        Use those to try to setup the correct keyboard map for the client
        so that all the keycodes sent will be mapped
    """
    #First we try to use data from setxkbmap -query
    if xkbmap_query:
        log("do_set_keymap using xkbmap_query")
        """ The xkbmap_query data will look something like this:
        rules:      evdev
        model:      evdev
        layout:     gb
        options:    grp:shift_caps_toggle
        And we want to call something like:
        setxkbmap -rules evdev -model evdev -layout gb
        setxkbmap -option "" -option grp:shift_caps_toggle
        (we execute the options separately in case that fails..)
        """
        #parse the data into a dict:
        settings = parse_xkbmap_query(xkbmap_query)
        #construct the command line arguments for setxkbmap:
        args = ["setxkbmap"]
        used_settings = {}
        for setting in ["rules", "model", "layout"]:
            if setting in settings:
                value = settings.get(setting)
                args += ["-%s" % setting, value]
                used_settings[setting] = value
        if len(args)==1:
            log.warn("do_set_keymap could not find rules, model or layout in the xkbmap query string..")
        else:
            log.info("setting keymap: %s", ", ".join(["%s=%s" % (std(k), std(v)) for k,v in used_settings.items()]))
        exec_keymap_command(args)
        #try to set the options:
        if "options" in settings:
            log.info("setting keymap options: %s", std(str(settings.get("options"))))
            exec_keymap_command(["setxkbmap", "-option", "", "-option", settings.get("options")])
    elif xkbmap_print:
        log("do_set_keymap using xkbmap_print")
        #try to guess the layout by parsing "setxkbmap -print"
        try:
            sym_re = re.compile("\s*xkb_symbols\s*{\s*include\s*\"([\w\+]*)")
            for line in xkbmap_print.splitlines():
                m = sym_re.match(line)
                if m:
                    layout = std(m.group(1))
                    log.info("guessing keyboard layout='%s'" % layout)
                    exec_keymap_command(["setxkbmap", layout])
                    break
        except Exception, e:
            log.info("error setting keymap: %s" % e)
    else:
        layout = xkbmap_layout or "us"
        log.info("setting keyboard layout to '%s'", std(layout))
        set_layout = ["setxkbmap", "-layout", layout]
        if xkbmap_variant:
            set_layout += ["-variant", xkbmap_variant]
        if not exec_keymap_command(set_layout) and xkbmap_variant:
            log.info("error setting keymap with variant %s, retrying with just layout %s", std(xkbmap_variant), std(layout))
            set_layout = ["setxkbmap", "-layout", layout]
            exec_keymap_command(set_layout)

    display = os.environ.get("DISPLAY")
    if xkbmap_print:
        #there may be a junk header, if so remove it:
        pos = xkbmap_print.find("xkb_keymap {")
        if pos>0:
            xkbmap_print = xkbmap_print[pos:]
        log.info("setting full keymap definition from client via xkbcomp")
        exec_keymap_command(["xkbcomp", "-", display], xkbmap_print)


################################################################################
# keycodes

def apply_xmodmap(instructions):
    try:
        unset = trap.call_synced(X11Keyboard.set_xmodmap, instructions)
    except:
        log.error("apply_xmodmap", exc_info=True)
        unset = instructions
    if unset is None:
        #None means an X11 error occurred, re-do all:
        unset = instructions
    return unset

def set_all_keycodes(xkbmap_x11_keycodes, xkbmap_keycodes, preserve_server_keycodes, modifiers):
    """
        Clients that have access to raw x11 keycodes should provide
        an xkbmap_x11_keycodes map, we otherwise fallback to using
        the xkbmap_keycodes gtk keycode list.
        We try to preserve the initial keycodes if asked to do so,
        we retrieve them from the current server keymap and combine
        them with the given keycodes.
        The modifiers dict can be obtained by calling
        get_modifiers_from_meanings or get_modifiers_from_keycodes.
        We use it to ensure that two modifiers are not
        mapped to the same keycode (which is not allowed).
        We return a translation map for keycodes after setting them up,
        the key is (keycode, keysym) and the value is the server keycode.
    """
    log("set_all_keycodes(%s.., %s.., %s.., %s)", str(xkbmap_x11_keycodes)[:60], str(xkbmap_keycodes)[:60], str(preserve_server_keycodes)[:60], modifiers)

    #so we can validate entries:
    keysym_to_modifier = {}
    for modifier, keysyms in modifiers.items():
        for keysym in keysyms:
            existing_mod = keysym_to_modifier.get(keysym)
            if existing_mod and existing_mod!=modifier:
                log.error("ERROR: keysym %s is mapped to both %s and %s !", keysym, modifier, existing_mod)
            else:
                keysym_to_modifier[keysym] = modifier
    log("keysym_to_modifier=%s", keysym_to_modifier)

    def modifiers_for(entries):
        """ entries can only point to a single modifier - verify """
        modifiers = set()
        for keysym, _ in entries:
            modifier = keysym_to_modifier.get(keysym)
            if modifier:
                modifiers.add(modifier)
        return modifiers

    def filter_mappings(mappings):
        filtered = {}
        for keycode, entries in mappings.items():
            mods = modifiers_for(entries)
            if len(mods)>1:
                log.warn("keymapping removed invalid keycode entry %s pointing to more than one modifier (%s): %s", keycode, mods, entries)
                continue
            #now remove entries for keysyms we don't have:
            f_entries = set([(keysym, index) for keysym, index in entries if X11Keyboard.parse_keysym(keysym) is not None])
            if len(f_entries)==0:
                log("keymapping removed invalid keycode entry %s pointing to only unknown keysyms: %s", keycode, entries)
                continue
            filtered[keycode] = f_entries
        return filtered

    #get the list of keycodes (either from x11 keycodes or gtk keycodes):
    if xkbmap_x11_keycodes and len(xkbmap_x11_keycodes)>0:
        log("using x11 keycodes: %s", xkbmap_x11_keycodes)
        dump_dict(xkbmap_x11_keycodes)
        keycodes = indexed_mappings(xkbmap_x11_keycodes)
    else:
        log("using gtk keycodes: %s", xkbmap_keycodes)
        keycodes = gtk_keycodes_to_mappings(xkbmap_keycodes)
    #filter to ensure only valid entries remain:
    log("keycodes=%s", keycodes)
    keycodes = filter_mappings(keycodes)

    #now lookup the current keycodes (if we need to preserve them)
    preserve_keycode_entries = {}
    if preserve_server_keycodes:
        preserve_keycode_entries = X11Keyboard.get_keycode_mappings()
        log("preserved mappings:")
        dump_dict(preserve_keycode_entries)
        log("preserve_keycode_entries=%s", preserve_keycode_entries)
        preserve_keycode_entries = filter_mappings(indexed_mappings(preserve_keycode_entries))

    kcmin, kcmax = X11Keyboard.get_minmax_keycodes()
    for try_harder in (False, True):
        trans, new_keycodes, missing_keycodes = translate_keycodes(kcmin, kcmax, keycodes, preserve_keycode_entries, keysym_to_modifier, try_harder)
        if len(missing_keycodes)==0:
            break
    instructions = keymap_to_xmodmap(new_keycodes)
    unset = apply_xmodmap(instructions)
    log("unset=%s", unset)
    return trans

def dump_dict(d):
    for k,v in d.items():
        log("%s\t\t=\t%s", k, v)

def group_by_keycode(entries):
    keycodes = {}
    for keysym, keycode, index in entries:
        keycodes.setdefault(keycode, set()).add((keysym, index))
    return keycodes

def indexed_mappings(raw_mappings):
    indexed = {}
    for keycode, keysyms in raw_mappings.items():
        pairs = set()
        for i in range(0, len(keysyms)):
            pairs.add((keysyms[i], i))
        indexed[keycode] = pairs
    return indexed


def gtk_keycodes_to_mappings(gtk_mappings):
    """
        Takes gtk keycodes as obtained by get_gtk_keymap, in the form:
        #[(keyval, keyname, keycode, group, level), ..]
        And returns a list of entries in the form:
        [[keysym, keycode, index], ..]
    """
    #use the keycodes supplied by gtk:
    mappings = {}
    for _, name, keycode, group, level in gtk_mappings:
        if keycode<0:
            continue            #ignore old 'add_if_missing' client side code
        index = group*2+level
        mappings.setdefault(keycode, set()).add((name, index))
    return mappings

def x11_keycodes_to_list(x11_mappings):
    """
        Takes x11 keycodes as obtained by get_keycode_mappings(), in the form:
        #{keycode : [keysyms], ..}
        And returns a list of entries in the form:
        [[keysym, keycode, index], ..]
    """
    entries = []
    if x11_mappings:
        for keycode, keysyms in x11_mappings.items():
            index = 0
            for keysym in keysyms:
                if keysym:
                    entries.append([keysym, int(keycode), index])
                index += 1
    return entries


def translate_keycodes(kcmin, kcmax, keycodes, preserve_keycode_entries={}, keysym_to_modifier={}, try_harder=False):
    """
        The keycodes given may not match the range that the server supports,
        or some of those keycodes may not be usable (only one modifier can
        be mapped to a single keycode) or we want to preserve a keycode,
        or modifiers want to use the same keycode (which is not possible),
        so we return a translation map for those keycodes that have been
        remapped.
        The preserve_keycodes is a dict containing {keycode:[entries]}
        for keys we want to preserve the keycode for.
        Note: a client_keycode of '0' is valid (osx uses that),
        but server_keycode generally starts at 8...
    """
    log("translate_keycodes(%s, %s, %s, %s, %s, %s)", kcmin, kcmax, keycodes, preserve_keycode_entries, keysym_to_modifier, try_harder)
    #list of free keycodes we can use:
    free_keycodes = [i for i in range(kcmin, kcmax) if i not in preserve_keycode_entries]
    keycode_trans = {}              #translation map from client keycode to our server keycode
    server_keycodes = {}            #the new keycode definitions
    missing_keycodes = []           #the groups of entries we failed to map due to lack of free keycodes

    #to do faster lookups:
    preserve_keysyms_map = {}
    for keycode, entries in preserve_keycode_entries.items():
        for keysym, _ in entries:
            preserve_keysyms_map.setdefault(keysym, set()).add(keycode)

    def do_assign(keycode, server_keycode, entries):
        """ may change the keycode if needed
            in which case we update the entries and populate 'keycode_trans'
        """
        if server_keycode in server_keycodes:
            log("assign: keycode %s already in use: %s", server_keycode, server_keycodes.get(server_keycode))
            server_keycode = -1
        elif server_keycode>0 and (server_keycode<kcmin or server_keycode>kcmax):
            log("assign: keycode %s out of range (%s to %s)", server_keycode, kcmin, kcmax)
            server_keycode = -1
        if server_keycode<=0:
            if len(free_keycodes)>0:
                server_keycode = free_keycodes[0]
                log("set_keycodes key %s using free keycode=%s", entries, server_keycode)
            else:
                msg = "set_keycodes: no free keycodes!, cannot translate %s: %s", server_keycode, entries
                if try_harder:
                    log.error(*msg)
                else:
                    log(*msg)
                missing_keycodes.append(entries)
                server_keycode = -1
        if server_keycode>0:
            vlog("set_keycodes key %s (%s) mapped to keycode=%s", keycode, entries, server_keycode)
            #can't use it any more!
            if server_keycode in free_keycodes:
                free_keycodes.remove(server_keycode)
            #record it in trans map:
            for name, _ in entries:
                if keycode>=0 and server_keycode!=keycode:
                    keycode_trans[(keycode, name)] = server_keycode
                keycode_trans[name] = server_keycode
            server_keycodes[server_keycode] = entries
        return server_keycode

    def assign(client_keycode, entries):
        if len(entries)==0:
            return 0
        if len(preserve_keycode_entries)==0:
            return do_assign(client_keycode, client_keycode, entries)
        #all the keysyms for this keycode:
        keysyms = set([keysym for keysym, _ in entries])
        if len(keysyms)==0:
            return 0
        if len(keysyms)==1:
            #only one keysym, replace with single entry
            entries = set([(list(keysyms)[0], 0)])

        #the candidate preserve entries: those that have at least one of the keysyms:
        preserve_keycode_matches = {}
        for keysym in list(keysyms):
            keycodes = preserve_keysyms_map.get(keysym, [])
            for keycode in keycodes:
                preserve_keycode_matches[keycode] = preserve_keycode_entries.get(keycode)

        if len(preserve_keycode_matches)==0:
            log("no preserve matches for %s", entries)
            return do_assign(client_keycode, -1, entries)         #nothing to preserve

        log("preserve matches for %s : %s", entries, preserve_keycode_matches)
        #direct superset:
        for p_keycode, p_entries in preserve_keycode_matches.items():
            if entries.issubset(p_entries):
                vlog("found direct preserve superset for %s : %s -> %s : %s", client_keycode, entries, p_keycode, p_entries)
                return do_assign(client_keycode, p_keycode, p_entries)
            if p_entries.issubset(entries):
                vlog("found direct superset of preserve for %s : %s -> %s : %s", client_keycode, entries, p_keycode, p_entries)
                return do_assign(client_keycode, p_keycode, entries)

        #ignoring indexes, but requiring at least as many keysyms:
        for p_keycode, p_entries in preserve_keycode_matches.items():
            p_keysyms = set([keysym for keysym,_ in p_entries])
            if keysyms.issubset(p_keysyms):
                if len(p_entries)>len(entries):
                    vlog("found keysym preserve superset with more keys for %s : %s", entries, p_entries)
                    return do_assign(client_keycode, p_keycode, p_entries)
            if p_keysyms.issubset(keysyms):
                vlog("found keysym superset of preserve with more keys for %s : %s", entries, p_entries)
                return do_assign(client_keycode, p_keycode, entries)

        if try_harder:
            #try to match the main key only:
            main_key = set([(keysym, index) for keysym, index in entries if index==0])
            if len(main_key)==1:
                for p_keycode, p_entries in preserve_keycode_matches.items():
                    p_keysyms = set([keysym for keysym,_ in p_entries])
                    if main_key.issubset(p_entries):
                        vlog("found main key superset for %s : %s", main_key, p_entries)
                        return do_assign(client_keycode, p_keycode, p_entries)

        log("no matches for %s", entries)
        return do_assign(client_keycode, -1, entries)

    #now try to assign each keycode:
    for keycode in sorted(keycodes.keys()):
        entries = keycodes.get(keycode)
        log("assign(%s, %s)", keycode, entries)
        assign(keycode, entries)

    #add all the other preserved ones that have not been mapped to any client keycode:
    for server_keycode, entries in preserve_keycode_entries.items():
        if server_keycode not in server_keycodes:
            do_assign(-1, server_keycode, entries)

    #find all keysyms assigned so far:
    all_keysyms = set()
    for entries in server_keycodes.values():
        for x in [keysym for keysym, _ in entries]:
            all_keysyms.add(x)
    log("all_keysyms=%s", all_keysyms)

    #defined keysyms for modifiers if some are missing:
    for keysym, modifier in keysym_to_modifier.items():
        if keysym not in all_keysyms:
            log("found missing keysym %s for modifier %s, will add it", keysym, modifier)
            new_keycode = set([(keysym, 0)])
            server_keycode = assign(-1, new_keycode)
            log("assigned keycode %s for key '%s' of modifier '%s'", server_keycode, keysym, modifier)

    log("translated keycodes=%s", keycode_trans)
    log("%s free keycodes=%s", len(free_keycodes), free_keycodes)
    return keycode_trans, server_keycodes, missing_keycodes


def keymap_to_xmodmap(trans_keycodes):
    """
        Given a dict with keycodes as keys and lists of keyboard entries as values,
        (keysym, keycode, index)
        produce a list of xmodmap instructions to set the x11 keyboard to match it,
        in the form:
        ("keycode", keycode, [keysyms])
    """
    missing_keysyms = []            #the keysyms lookups which failed
    instructions = []
    all_entries = []
    for entries in trans_keycodes.values():
        all_entries += entries
    keysyms_per_keycode = max([index for _, index in all_entries])+1
    for server_keycode, entries in trans_keycodes.items():
        keysyms = [None]*keysyms_per_keycode
        names = [""]*keysyms_per_keycode
        for name, index in entries:
            assert 0<=index and index<keysyms_per_keycode
            names[index] = name
            try:
                keysym = X11Keyboard.parse_keysym(name)
            except:
                keysym = None
            if keysym is None:
                if name!="":
                    missing_keysyms.append(name)
            else:
                if keysyms[index] is not None:
                    log.warn("we already have a keysym for %s at index %s: %s, entries=%s", server_keycode, index, keysyms[index], entries)
                else:
                    keysyms[index] = keysym
        #remove empty keysyms:
        while len(keysyms)>0 and keysyms[0] is None:
            keysyms = keysyms[1:]
        log("%s: %s -> %s", server_keycode, names, keysyms)
        instructions.append(("keycode", server_keycode, keysyms))

    if len(missing_keysyms)>0:
        log.error("cannot find the X11 keysym for the following key names: %s", set(missing_keysyms))
    log("instructions=%s", instructions)
    return  instructions


################################################################################
# modifiers

def clear_modifiers(modifiers):
    instructions = []
    for i in range(0, 8):
        instructions.append(("clear", i))
    apply_xmodmap(instructions)

def set_modifiers(modifiers):
    """
        modifiers is a dict: {modifier : [keynames]}
        Note: the same keysym cannot appear in more than one modifier
    """
    instructions = []
    for modifier, keynames in modifiers.items():
        mod = X11Keyboard.parse_modifier(modifier)
        if mod>=0:
            instructions.append(("add", mod, keynames))
        else:
            log.error("set_modifiers_from_dict: unknown modifier %s", modifier)
    log("set_modifiers: %s", instructions)
    def apply_or_trim(instructions):
        err = apply_xmodmap(instructions)
        log("set_modifiers: err=%s", err)
        if len(err):
            log("set_modifiers %s failed, retrying one more at a time", instructions)
            l = len(instructions)
            for i in range(1, l):
                subset = instructions[:i]
                log("set_modifiers testing with [:%s]=%s", i, subset)
                err = apply_xmodmap(subset)
                log("err=%s", err)
                if len(err)>0:
                    log.warn("removing problematic modifier mapping: %s", instructions[i-1])
                    instructions = instructions[:i-1]+instructions[i:]
                    return apply_or_trim(instructions)
    apply_or_trim(instructions)
    return  modifiers


def get_modifiers_from_meanings(xkbmap_mod_meanings):
    """
        xkbmap_mod_meanings maps a keyname to a modifier
        returns keynames_for_mod: {modifier : [keynames]}
    """
    #first generate a {modifier : [keynames]} dict:
    modifiers = {}
    for keyname, modifier in xkbmap_mod_meanings.items():
        modifiers.setdefault(modifier, set()).add(keyname)
    log("get_modifiers_from_meanings(%s) modifier dict=%s", xkbmap_mod_meanings, modifiers)
    return modifiers

def get_modifiers_from_keycodes(xkbmap_keycodes):
    """
        Some platforms can't tell us about modifier mappings
        So we try to find matches from the defaults below:
    """
    from xpra.keyboard.mask import DEFAULT_MODIFIER_MEANINGS
    pref = DEFAULT_MODIFIER_MEANINGS
    #keycodes are: {keycode : (keyval, name, keycode, group, level)}
    matches = {}
    log("get_modifiers_from_keycodes(%s...)", str(xkbmap_keycodes))
    log("get_modifiers_from_keycodes(%s...)", str(xkbmap_keycodes)[:160])
    all_keynames = set()
    for entry in xkbmap_keycodes:
        _, keyname, _, _, _ = entry
        modifier = pref.get(keyname)
        if modifier:
            keynames = matches.setdefault(modifier, set())
            keynames.add(keyname)
            all_keynames.add(keyname)
    #try to add missings ones (magic!)
    defaults = {}
    for keyname, modifier in DEFAULT_MODIFIER_MEANINGS.items():
        if keyname in all_keynames:
            continue            #aleady defined
        if modifier not in matches:
            #define it since it is completely missing
            defaults.setdefault(modifier, set()).add(keyname)
        elif modifier in ["shift", "lock", "control", "mod1", "mod2"] or keyname=="ISO_Level3_Shift":
            #these ones we always add them, even if a record for this modifier already exists
            matches.setdefault(modifier, set()).add(keyname)
    log("get_modifiers_from_keycodes(...) adding defaults: %s", defaults)
    matches.update(defaults)
    log("get_modifiers_from_keycodes(...)=%s", matches)
    return matches
