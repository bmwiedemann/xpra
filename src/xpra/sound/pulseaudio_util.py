#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2010-2015 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import sys
import os.path

from xpra.log import Logger
log = Logger("sound")

default_icon_path = None
def set_icon_path(v):
    global default_icon_path
    default_icon_path = v

def add_audio_tagging_env(env_dict=os.environ, icon_path=None):
    """
        This is called audio-tagging in PulseAudio, see:
        http://pulseaudio.org/wiki/ApplicationProperties
        http://0pointer.de/blog/projects/tagging-audio.html
    """
    env_dict["PULSE_PROP_application.name"] = "xpra"
    env_dict["PULSE_PROP_media.role"] = "music"
    if not icon_path:
        icon_path = default_icon_path
    if icon_path and os.path.exists(icon_path):
        env_dict["PULSE_PROP_application.icon_name"] = str(icon_path)


#prefer the palib option which does everything in process:
try:
    #use "none" on win32 and osx:
    if sys.platform.startswith("win") or sys.platform.startswith("darwin"):
        from xpra.sound import pulseaudio_none_util as _pulseaudio_util
    else:
        if os.environ.get("XPRA_USE_PALIB", "0")=="1":
            from xpra.sound import pulseaudio_palib_util as _pulseaudio_util
        else:
            from xpra.sound import pulseaudio_pactl_util as  _pulseaudio_util       #@Reimport
except ImportError as e:
    #fallback forks a process and parses the output:
    log("using pulseaudio none fallback")
    from xpra.sound import pulseaudio_none_util as _pulseaudio_util

get_info                = _pulseaudio_util.get_info
has_pa                  = _pulseaudio_util.has_pa
get_pa_device_options   = _pulseaudio_util.get_pa_device_options
get_default_sink        = _pulseaudio_util.get_default_sink
get_pulse_server        = _pulseaudio_util.get_pulse_server
get_pulse_id            = _pulseaudio_util.get_pulse_id
set_source_mute         = _pulseaudio_util.set_source_mute


def main():
    from xpra.platform import program_context
    from xpra.log import enable_color
    with program_context("Pulseaudio-Info"):
        enable_color()
        if "-v" in sys.argv or "--verbose" in sys.argv:
            log.enable_debug()
        i = get_info()
        for k in sorted(i):
            log.info("%s : %s", k.ljust(64), i[k])


if __name__ == "__main__":
    main()
