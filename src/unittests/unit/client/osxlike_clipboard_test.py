#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2016-2019 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import unittest
from unit.client.x11_clipboard_test_util import X11ClipboardTestUtil, has_xclip
from xpra.os_util import OSX, POSIX


class OSXLikeClipboardTest(X11ClipboardTestUtil):

	def get_run_env(self):
		env = super().get_run_env()
		env.update({
					"XPRA_CLIPBOARD_WANT_TARGETS"	: "1",
					"XPRA_CLIPBOARD_GREEDY"			: "1",
					})
		return env


	def test_copy(self):
		self.do_test_copy()

	def test_disabled(self):
		self.do_test_copy("disabled")

	def test_to_server(self):
		self.do_test_copy("to-server")

	def test_to_client(self):
		self.do_test_copy("to-client")



def main():
	if POSIX and not OSX and has_xclip():
		unittest.main()


if __name__ == '__main__':
	main()
