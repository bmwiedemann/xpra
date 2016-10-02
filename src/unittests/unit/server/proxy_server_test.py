#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2016 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import unittest
from unit.server_test_util import ServerTestUtil, log


class ProxyServerTest(ServerTestUtil):

	def test_proxy_start_stop(self):
		display = self.find_free_display()
		log("using free display=%s" % display)
		proxy = self.run_xpra(["proxy", display, "--no-daemon"])
		assert self.pollwait(proxy, 5) is None, "proxy failed to start"
		assert display in self.dotxpra.displays(), "proxy display not found"
		self.check_stop_server(proxy, "stop", display)


def main():
	unittest.main()


if __name__ == '__main__':
	main()
