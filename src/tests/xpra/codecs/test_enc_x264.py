#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2013 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from tests.xpra.codecs.test_encoder import test_encoder, test_performance

TEST_DIMENSIONS = ((32, 32), (1920, 1080), (512, 512))

def test_nvenc():
    print("test_nvenc()")
    from xpra.codecs.enc_x264 import encoder #@UnresolvedImport
    test_encoder(encoder)
    test_performance(encoder)


def main():
    test_nvenc()


if __name__ == "__main__":
    main()
