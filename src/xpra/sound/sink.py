#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2010-2013 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import sys, os, time

from xpra.sound.sound_pipeline import SoundPipeline, debug
from xpra.sound.pulseaudio_util import has_pa
from xpra.sound.gstreamer_util import plugin_str, get_decoder_parser, MP3, CODECS, gst
from xpra.log import Logger
log = Logger()


SINKS = ["autoaudiosink"]
DEFAULT_SINK = SINKS[0]
if has_pa():
    SINKS.append("pulsesink")
if sys.platform.startswith("darwin"):
    SINKS.append("osxaudiosink")
    DEFAULT_SINK = "osxaudiosink"
elif sys.platform.startswith("win"):
    SINKS.append("directsoundsink")
    DEFAULT_SINK = "directsoundsink"
if os.name=="posix":
    SINKS += ["alsasink", "osssink", "oss4sink", "jackaudiosink"]

GST_QUEUE_NO_LEAK             = 0
GST_QUEUE_LEAK_UPSTREAM       = 1
GST_QUEUE_LEAK_DOWNSTREAM     = 2

QUEUE_TIME = int(os.environ.get("XPRA_SOUND_QUEUE_TIME", "450"))*1000000        #ns
QUEUE_START_TIME = int(os.environ.get("XPRA_SOUND_QUEUE_START_TIME", "250"))*1000000        #ns
QUEUE_MIN_TIME = int(os.environ.get("XPRA_SOUND_QUEUE_MIN_TIME", "50"))*1000000 #ns
QUEUE_TIME = max(0, QUEUE_TIME)
QUEUE_MIN_TIME = max(0, min(QUEUE_TIME, QUEUE_MIN_TIME))
DEFAULT_SINK = os.environ.get("XPRA_SOUND_SINK", DEFAULT_SINK)
if DEFAULT_SINK not in SINKS:
    log.error("invalid default sound sink: '%s' is not in %s, using %s instead", DEFAULT_SINK, SINKS, SINKS[0])
    DEFAULT_SINK = SINKS[0]

VOLUME = True


def sink_has_device_attribute(sink):
    return sink not in ("autoaudiosink", "jackaudiosink", "directsoundsink")


class SoundSink(SoundPipeline):

    __generic_signals__ = [
        "underrun",
        "overrun",
        "eos"
        ]

    def __init__(self, sink_type=DEFAULT_SINK, options={}, codec=MP3, decoder_options={}):
        assert sink_type in SINKS, "invalid sink: %s" % sink_type
        decoder, parser = get_decoder_parser(codec)
        SoundPipeline.__init__(self, codec)
        self.add_signals(self.__generic_signals__)
        self.sink_type = sink_type
        decoder_str = plugin_str(decoder, decoder_options)
        pipeline_els = []
        pipeline_els.append("appsrc name=src max-bytes=512")
        pipeline_els.append(parser)
        pipeline_els.append(decoder_str)
        if VOLUME:
            pipeline_els.append("volume name=volume")
        pipeline_els.append("audioconvert")
        pipeline_els.append("audioresample")
        if QUEUE_TIME>0:
            pipeline_els.append("queue" +
                                " name=queue"+
                                " min-threshold-time=%s" % QUEUE_MIN_TIME+
                                " max-size-time=%s" % QUEUE_START_TIME+
                                " leaky=%s" % GST_QUEUE_LEAK_DOWNSTREAM)
        else:
            pipeline_els.append("queue leaky=%s" % GST_QUEUE_LEAK_DOWNSTREAM)
        pipeline_els.append(sink_type)
        self.setup_pipeline_and_bus(pipeline_els)
        self.volume = self.pipeline.get_by_name("volume")
        self.src = self.pipeline.get_by_name("src")
        self.src.set_property('emit-signals', True)
        self.src.set_property('stream-type', 'stream')
        self.src.set_property('block', False)
        self.src.set_property('format', 4)
        self.src.set_property('is-live', True)
        self.queue = self.pipeline.get_by_name("queue")
        self.queue.connect("overrun", self.queue_overrun)
        self.queue.connect("underrun", self.queue_underrun)
        self.src.connect("need-data", self.need_data)
        self.src.connect("enough-data", self.on_enough_data)

    def reset_queue(self):
        #reset the start_time and go back to original queue size
        self.start_time = time.time()
        self.queue.set_property("max-size-time", QUEUE_START_TIME)

    def queue_underrun(self, *args):
        ltime = int(self.queue.get_property("current-level-time")/1000000)
        debug("sound sink queue underrun: level=%s", ltime)
        self.emit("underrun", ltime)

    def queue_overrun(self, *args):
        ltime = int(self.queue.get_property("current-level-time")/1000000)
        debug("sound sink queue overrun: level=%s", ltime)
        #no overruns for the first 2 seconds:
        if time.time()-self.start_time<2.0:
            return
        #if we haven't done so yet, just bump the max-size-time
        if int(self.queue.get_property("max-size-time")) < QUEUE_TIME:
            self.queue.set_property("max-size-time", QUEUE_TIME)
            return
        self.emit("overrun", ltime)

    def cleanup(self):
        SoundPipeline.cleanup(self)
        self.sink_type = ""
        self.volume = None
        self.src = None

    def set_queue_delay(self, ms):
        assert self.queue
        assert ms>0
        self.queue.set_property("max-size-time", ms*1000000)
        log("queue delay set to %s, current-level-time=%s", ms, int(self.queue.get_property("current-level-time")/1000/1000))

    def set_mute(self, mute):
        self.volume.set_property('mute', mute)

    def is_muted(self):
        return bool(self.volume.get_property("mute"))

    def get_volume(self):
        assert self.volume
        return  self.volume.get_property("volume")

    def set_volume(self, volume):
        assert self.volume
        assert volume>=0 and volume<=100
        self.volume.set_property('volume', float(volume)/10.0)

    def eos(self):
        debug("eos()")
        if self.src:
            self.src.emit('end-of-stream')
        self.cleanup()

    def get_info(self):
        info = SoundPipeline.get_info(self)
        if QUEUE_TIME>0:
            clt = self.queue.get_property("current-level-time")
            info["queue.used_pct"] = int(min(QUEUE_TIME, clt)*100.0/QUEUE_TIME)
        if VOLUME and self.volume:
            info["mute"] = self.volume.get_property("mute")
            info["volume"] = int(100.0*self.volume.get_property("volume"))
        return info

    def add_data(self, data, metadata=None):
        debug("sound sink: adding %s bytes to %s, metadata: %s, level=%s", len(data), self.src, metadata, int(self.queue.get_property("current-level-time")/1000000))
        if self.src:
            buf = gst.Buffer(data)
            if metadata:
                ts = metadata.get("timestamp")
                if ts is not None:
                    buf.timestamp = ts
                d = metadata.get("duration")
                if d is not None:
                    buf.duration = d
            #buf.size = size
            #buf.timestamp = timestamp
            #buf.duration = duration
            #buf.offset = offset
            #buf.offset_end = offset_end
            #buf.set_caps(gst.caps_from_string(caps))
            r = self.src.emit("push-buffer", buf)
            if r!=gst.FLOW_OK:
                log.error("push-buffer error: %s", r)
                self.emit('error', "push-buffer error: %s" % r)
            else:
                self.buffer_count += 1
                self.byte_count += len(data)

    def need_data(self, src_arg, needed):
        debug("need_data: %s bytes in %s", needed, src_arg)

    def on_enough_data(self, *args):
        debug("on_enough_data(%s)", args)


def main():
    import os.path
    import gobject
    if len(sys.argv) not in (2, 3):
        print("usage: %s filename [codec]" % sys.argv[0])
        sys.exit(1)
        return
    filename = sys.argv[1]
    if not os.path.exists(filename):
        print("file %s does not exist" % filename)
        sys.exit(2)
        return
    if len(sys.argv)==3:
        codec = sys.argv[2]
        if codec not in CODECS:
            print("invalid codec: %s" % codec)
            sys.exit(2)
            return
    else:
        codec = None
        parts = filename.split(".")
        if len(parts)>1:
            extension = parts[-1]
            if extension.lower() in CODECS:
                codec = extension.lower()
                print("guessed codec %s from file extension %s" % (codec, extension))
        if codec is None:
            print("assuming this is an mp3 file...")
            codec = MP3

    import logging
    logging.basicConfig(format="%(asctime)s %(message)s")
    logging.root.setLevel(logging.INFO)
    f = open(filename, "rb")
    data = f.read()
    f.close()
    print("loaded %s bytes from %s" % (len(data), filename))
    ss = SoundSink(codec=codec)
    ss.add_data(data)
    def eos(*args):
        print("eos")
        gobject.idle_add(gobject_mainloop.quit)
    ss.connect("eos", eos)
    ss.start()

    gobject_mainloop = gobject.MainLoop()
    gobject.threads_init()

    import signal
    def deadly_signal(*args):
        gobject.idle_add(gobject_mainloop.quit)
    signal.signal(signal.SIGINT, deadly_signal)
    signal.signal(signal.SIGTERM, deadly_signal)

    def check_for_end(*args):
        if not ss.pipeline:
            log.info("pipeline closed")
            gobject_mainloop.quit()
        return True
    gobject.timeout_add(1000, check_for_end)

    gobject_mainloop.run()


if __name__ == "__main__":
    main()
