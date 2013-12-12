#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2013 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from tests.xpra.codecs.test_csc import test_all


def test_csc_opencl():
    from xpra.codecs.csc_opencl import colorspace_converter
    #from tests.xpra.codecs.test_csc import test_csc_planar, test_csc_rgb, test_csc_roundtrip
    #test_csc_rgb(colorspace_converter)
    #test_csc_planar(colorspace_converter)
    #test_csc_roundtrip(colorspace_converter)
    test_all(colorspace_converter)


def main():
    import logging
    import sys
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(logging.StreamHandler(sys.stdout))
    test_csc_opencl()


if __name__ == "__main__":
    main()
