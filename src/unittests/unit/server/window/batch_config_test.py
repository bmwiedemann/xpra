#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2020 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import unittest

from xpra.os_util import OSEnvContext, monotonic_time
from xpra.server.window.batch_config import DamageBatchConfig, ival


class TestBatchConfig(unittest.TestCase):

    def test_ival(self):
        with OSEnvContext():
            for k in ("XYZ", "WHATEVER"):
                try:
                    del os.environ["XPRA_BATCH_%s" % k]
                except KeyError:
                    pass
                assert ival(k, 20, 0, 100)==20
                os.environ["XPRA_BATCH_%s" % k] = "notanumber"
                assert ival(k, 30, 0, 100)==30
                os.environ["XPRA_BATCH_%s" % k] = "50"
                assert ival(k, 0, 0, 100)==50
                os.environ["XPRA_BATCH_%s" % k] = "120"
                assert ival(k, 0, 0, 100)==100
                os.environ["XPRA_BATCH_%s" % k] = "10"
                assert ival(k, 0, 20, 100)==20

    def test_batch_config(self):
        now = monotonic_time()
        bc = DamageBatchConfig()
        bc.delay_per_megapixel = 100
        bc.last_event = now
        for i in range(10):
            bc.last_delays.append((now-10+i, 10+i))
            bc.last_actual_delays.append((now-10+i, 5+i))
        bc.factors = (("name", {}, 1, 1),)
        assert bc.get_info()
        assert repr(bc)
        bc.cleanup()
        clone = bc.clone()
        assert repr(clone)==repr(bc)
        i = bc.get_info()
        ci = clone.get_info()
        for k,v in ci.items():
            assert v==i.get(k), "expected %s=%s, clone has %s=%s" % (k, i.get(k), k, v)
        bc.locked = True
        assert bc.get_info().get("delay")==bc.delay


def main():
    unittest.main()

if __name__ == '__main__':
    main()
