#!/bin/bash
# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2017 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

#using easy-install for python libraries which are not packaged by mingw:
# Note: netifaces not updated for now because of this bug:
# https://bitbucket.org/al45tair/netifaces/issues/39
for x in rencode xxhash enum34 enum-compat zeroconf lz4 websocket-client comtypes PyOpenGL PyOpenGL_accelerate websockify cffi pycparser cryptography nvidia-ml-py; do
    easy_install-2.7 -U -Z $x
    easy_install-3.5 -U -Z $x
done
