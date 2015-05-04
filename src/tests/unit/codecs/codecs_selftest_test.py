#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2015 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import unittest


class TestDecoders(unittest.TestCase):

    def test_all_codecs_found(self):
        from xpra.codecs import loader
        #the self tests would swallow the exceptions and produce a warning:
        loader.RUN_SELF_TESTS = False
        loader.load_codecs()
        #test them all:
        for codec_name in loader.ALL_CODECS:
            codec = loader.get_codec(codec_name)
            if not codec:
                continue
            #print("found %s: %s" % (codec_name, codec))
            selftest = getattr(codec, "selftest", None)
            #print("selftest=%s" % selftest)
            if selftest:
                selftest()



def main():
    unittest.main()

if __name__ == '__main__':
    main()
