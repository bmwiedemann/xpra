#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2020 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import tempfile
import unittest

from xpra.notifications.common import parse_image_data, parse_image_path


class TestCommon(unittest.TestCase):

    def test_parse_image_data(self):
        assert parse_image_data(None) is None
        assert parse_image_data((1, 2, 3)) is None
        assert parse_image_data((1, 2, 3, 4, 5, 6, 7)) is None
        assert parse_image_data((10, 10, 40, True, 32, 4, b"0"*40*10)) is not None
        assert parse_image_data((10, 10, 40, False, 24, 4, b"0"*40*10)) is not None
        assert parse_image_data((10, 10, 30, False, 24, 3, b"0"*30*10)) is not None

    def test_parse_image_path(self):
        from xpra.platform.paths import get_icon_filename
        filename = get_icon_filename("xpra")
        assert parse_image_path(filename) is not None
        f = tempfile.NamedTemporaryFile(prefix="test-invalid-file", delete=False)
        try:
            f.file.write(b"0000000000000001111111111111111111111")
            f.file.flush()
            f.close()
            for x in ("", None, "/invalid-path", f.name):
                assert parse_image_path(x) is None
        finally:
            os.unlink(f.name)


def main():
    unittest.main()

if __name__ == '__main__':
    main()
