#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2018 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import time
from collections import OrderedDict

from xpra.util import envbool
from xpra.os_util import pollwait, which, WIN32, OSX, POSIX
from unit.server_test_util import ServerTestUtil, log
from xpra.net.net_util import get_free_tcp_port


TEST_RFB = envbool("XPRA_TEST_RFB", not WIN32 and not OSX)
#TEST_RFB = envbool("XPRA_TEST_RFB", False)
VFB_INITIAL_RESOLUTION = os.environ.get("XPRA_TEST_VFB_INITIAL_RESOLUTION", "1920x1080")


OPTIONS = (
    "windows",
    "notifications",
    "webcam",
    "clipboard",
    "speaker",
    "microphone",
    "av-sync",
    "printing",
    "file-transfer",
    "mmap",
    "readonly",
    "dbus-proxy",
    "remote-logging",
    )

class ServerMixinsOptionTestUtil(ServerTestUtil):

    @classmethod
    def setUpClass(cls):
        ServerTestUtil.setUpClass()
        cls.default_xpra_args = []
        if POSIX:
            cls.default_xpra_args += [
                "--socket-dirs=/tmp",
                ]
            if not OSX:
                cls.default_xpra_args += [
                    "--systemd-run=no",
                    "--pulseaudio=no",
                    "--start=xterm",
                    ]
        cls.display = None
        cls.xvfb = None
        cls.client_display = None
        cls.client_xvfb = None
        if POSIX and not OSX:
            if False:
                #use a single display for the server that we recycle:
                cls.display = cls.find_free_display()
                cls.xvfb = cls.start_Xvfb(cls.display)
                time.sleep(1)
                assert cls.display in cls.find_X11_displays()
                log("ServerMixinsOptionTest.setUpClass() server display=%s, xvfb=%s", cls.display, cls.xvfb)
            if True:
                #display used by the client:
                cls.client_display = cls.find_free_display()
                cls.client_xvfb = cls.start_Xvfb(cls.client_display)
                log("ServerMixinsOptionTest.setUpClass() client display=%s, xvfb=%s", cls.client_display, cls.client_xvfb)
            if VFB_INITIAL_RESOLUTION:
                cls.run_command(["xrandr", "-s", VFB_INITIAL_RESOLUTION, "--display", cls.client_display])


    @classmethod
    def tearDownClass(cls):
        ServerTestUtil.tearDownClass()
        if cls.xvfb:
            cls.xvfb.terminate()
            cls.xvfb = None
        if cls.client_xvfb:
            cls.client_xvfb.terminate()
            cls.client_xvfb = None

    def _test(self, subcommand="start", options={}):
        log("starting test server with options=%s", options)
        args = ["--%s=%s" % (k,v) for k,v in options.items()]
        tcp_port = None
        xvfb = None
        if WIN32 or OSX:
            display = ""
            connect_args = []
        elif self.display:
            display = self.display
            connect_args = [display]
            args.append("--use-display")
        else:
            display = self.find_free_display()
            connect_args = [display]
            if subcommand=="shadow":
                xvfb = self.start_Xvfb(display)
        if TEST_RFB or WIN32:
            tcp_port = get_free_tcp_port()
            args += ["--bind-tcp=0.0.0.0:%i" % tcp_port]
            if WIN32:
                connect_args = ["tcp://127.0.0.1:%i" % tcp_port]
        server = None
        client = None
        rfb_client = None
        gui_client = None
        try:
            log("args=%s", " ".join("'%s'" % x for x in args))
            server = self.check_server(subcommand, display, *args)
            #we should always be able to get the version:
            client = self.run_xpra(["version"]+connect_args)
            assert pollwait(client, 5)==0, "version client failed to connect to server with args=%s" % args
            #run info query:
            cmd = ["info"]+connect_args
            client = self.run_xpra(cmd)
            r = pollwait(client, 5)
            assert r==0, "info client failed and returned %s for server with args=%s" % (r, args)

            client_kwargs = {}
            if not (WIN32 or OSX):
                env = os.environ.copy()
                env["DISPLAY"] = self.client_display
                client_kwargs = {"env" : env}

            if subcommand in ("shadow", "start-desktop") and TEST_RFB:
                vncviewer = which("vncviewer")
                log("testing RFB clients with vncviewer '%s'", vncviewer)
                if vncviewer:
                    rfb_cmd = [vncviewer, "localhost::%i" % tcp_port]
                    rfb_client = self.run_command(rfb_cmd, **client_kwargs)
                    r = pollwait(rfb_client, 10)
                    assert r is None, "rfb client terminated early and returned %i for server with args=%s" % (r, args)

            #connect a gui client:
            if WIN32 or (self.client_display and self.client_xvfb):
                xpra_args = [
                    "attach",
                    "--clipboard=no",       #could create loops
                    "--notifications=no",   #may get sent to the desktop session running the tests!
                    ]+connect_args
                gui_client = self.run_xpra(xpra_args, **client_kwargs)
                r = pollwait(gui_client, 10)
                if r is not None:
                    log.warn("gui client stdout: %s", gui_client.stdout_file)
                assert r is None, "gui client terminated early and returned %i for server with args=%s" % (r, args)

            if self.display:
                self.stop_server(server, "exit", *connect_args)
            else:
                self.stop_server(server, "stop", *connect_args)
            if display:
                assert display not in self.dotxpra.displays(), "server socket for display %s should have been removed" % display

            if gui_client:
                r = pollwait(gui_client, 5)
                assert r is not None, "gui client should have been disconnected"
        finally:
            for x in (xvfb, rfb_client, gui_client, server, client):
                try:
                    if x and x.poll() is None:
                        x.terminate()
                except:
                    log("%s.terminate()", exc_info=True)

    def _test_all(self, subcommand="start"):
        #to test all:
        #TEST_VALUES = range(0, 2**len(OPTIONS)-1)
        #to test nothing disabled and everything disabled only:
        TEST_VALUES = (0, 2**len(OPTIONS)-1)
        #test every option disabled individually:
        #TEST_VALUES = tuple(2**i for i in range(len(OPTIONS)))
        for i in TEST_VALUES:
            options = OrderedDict()
            for o, option in enumerate(OPTIONS):
                options[option] = not bool((2**o) & i)
            log("test options for %i: %s", i, options)
            self._test(subcommand, options=options)
