#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2011-2017 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import sys
import unittest
import tempfile
import uuid
import hmac
from xpra.util import xor
from xpra.os_util import strtobytes, bytestostr, WIN32
from xpra.net.protocol import get_digests
from xpra.net.crypto import get_digest_module


class FakeOpts(object):
	def __init__(self, d={}):
		self._d = d
	def __getattr__(self, name):
		return self._d.get(name)

class TestAuth(unittest.TestCase):

	def a(self, name):
		pmod = "xpra.server.auth"
		auth_module = __import__(pmod, globals(), locals(), ["%s_auth" % name], 0)
		mod = getattr(auth_module, "%s_auth" % name, None)
		assert mod, "cannot load '%s_auth' from %s" % (name, pmod)
		return mod

	def _init_auth(self, mod_name, options={}, username="foo", **kwargs):
		mod = self.a(mod_name)
		return self.do_init_auth(mod, options, username, **kwargs)

	def do_init_auth(self, module, options={}, username="foo", **kwargs):
		opts = FakeOpts(options)
		module.init(opts)
		try:
			c = module.Authenticator
		except Exception as e:
			raise Exception("module %s does not contain an Authenticator class!")
		try:
			return c(username, **kwargs)
		except Exception as e:
			raise Exception("failed to instantiate %s: %s" % (c, e))

	def _test_module(self, module):
		a = self._init_auth(module)
		assert a
		if a.requires_challenge():
			challenge = a.get_challenge(get_digests())
			assert challenge
		a = self._init_auth(module)
		assert a
		if a.requires_challenge():
			try:
				challenge = a.get_challenge(["invalid-digest"])
			except Exception:
				pass
			else:
				assert challenge is None


	def test_all(self):
		test_modules = ["reject", "allow", "none", "file", "multifile", "env", "password"]
		try:
			self.a("pam")
			test_modules.append("pam")
		except Exception:
			pass
		if sys.platform.startswith("win"):
			self.a("win32")
			test_modules.append("win32")
		for module in test_modules:
			self._test_module(module)

	def test_fail(self):
		try:
			fa = self._init_auth("fail")
		except:
			fa = None
		assert fa is None, "'fail_auth' did not fail!"

	def test_reject(self):
		a = self._init_auth("reject")
		assert a.requires_challenge()
		c, mac = a.get_challenge(get_digests())
		assert c and mac
		assert not a.get_sessions()
		assert not a.get_password()
		for x in (None, "bar"):
			assert not a.authenticate(x, c)
			assert not a.authenticate(x, x)

	def test_none(self):
		a = self._init_auth("none")
		assert not a.requires_challenge()
		assert a.get_challenge(get_digests()) is None
		assert not a.get_password()
		for x in (None, "bar"):
			assert a.authenticate(x, "")
			assert a.authenticate("", x)

	def test_allow(self):
		a = self._init_auth("allow")
		assert a.requires_challenge()
		assert a.get_challenge(get_digests())
		assert not a.get_password()
		for x in (None, "bar"):
			assert a.authenticate(x, "")
			assert a.authenticate("", x)

	def _test_hmac_auth(self, mod_name, password, **kwargs):
		for x in (password, "somethingelse"):
			a = self._init_auth(mod_name, **kwargs)
			assert a.requires_challenge()
			assert a.get_password()
			salt, mac = a.get_challenge([x for x in get_digests() if x.startswith("hmac")])
			assert salt
			assert mac.startswith("hmac"), "invalid mac: %s" % mac
			client_salt = strtobytes(uuid.uuid4().hex+uuid.uuid4().hex)
			auth_salt = strtobytes(xor(salt, client_salt))
			digestmod = get_digest_module(mac)
			verify = hmac.HMAC(strtobytes(x), auth_salt, digestmod=digestmod).hexdigest()
			passed = a.authenticate(verify, client_salt)
			assert passed == (x==password), "expected authentication to %s with %s vs %s" % (["fail", "succeed"][x==password], x, password)
			assert not a.authenticate(verify, client_salt)

	def test_env(self):
		for var_name in ("XPRA_PASSWORD", "SOME_OTHER_VAR_NAME"):
			password = strtobytes(uuid.uuid4().hex)
			os.environ[var_name] = bytestostr(password)
			try:
				kwargs = {}
				if var_name!="XPRA_PASSWORD":
					kwargs["name"] = var_name
				self._test_hmac_auth("env", password, name=var_name)
			finally:
				del os.environ[var_name]

	def test_password(self):
		password = strtobytes(uuid.uuid4().hex)
		self._test_hmac_auth("password", password, value=password)


	def _test_file_auth(self, mod_name, genauthdata):
		#no file, no go:
		a = self._init_auth(mod_name)
		assert a.requires_challenge()
		p = a.get_password()
		assert not p, "got a password from %s: %s" % (a, p)
		#challenge twice is a fail
		assert a.get_challenge(get_digests())
		assert not a.get_challenge(get_digests())
		assert not a.get_challenge(get_digests())
		for muck in (0, 1):
			if WIN32:
				#NamedTemporaryFile doesn't work for reading on win32...
				import time
				filename = os.path.join(os.environ.get("TEMP", "/tmp"), "file-auth-test-%s" % time.time())
				f = open(filename, 'wb')
			else:
				f = tempfile.NamedTemporaryFile(prefix=mod_name)
				filename = f.name
			try:
				with f:
					a = self._init_auth(mod_name, {"password_file" : filename})
					password, filedata = genauthdata(a)
					#print("saving password file data='%s' to '%s'" % (filedata, filename))
					f.write(strtobytes(filedata))
					f.flush()
					assert a.requires_challenge()
					salt, mac = a.get_challenge(get_digests())
					assert salt
					assert mac in get_digests()
					assert mac!="xor"
					password = strtobytes(password)
					client_salt = strtobytes(uuid.uuid4().hex+uuid.uuid4().hex)
					auth_salt = strtobytes(xor(salt, client_salt))
					if muck==0:
						digestmod = get_digest_module(mac)
						verify = hmac.HMAC(password, auth_salt, digestmod=digestmod).hexdigest()
						assert a.authenticate(verify, client_salt)
						assert not a.authenticate(verify, client_salt)
						assert a.get_password()==password
					elif muck==1:
						for verify in ("whatever", None, "bad"):
							assert not a.authenticate(verify, client_salt)
			finally:
				if WIN32:
					os.unlink(filename)

	def test_file(self):
		def genfiledata(a):
			password = uuid.uuid4().hex
			return password, password
		self._test_file_auth("file", genfiledata)

	def test_multifile(self):
		def genfiledata(a):
			password = uuid.uuid4().hex
			return password, "%s|%s|||" % (a.username, password)
		self._test_file_auth("multifile", genfiledata)


def main():
	import logging
	from xpra.log import set_default_level
	if "-v" in sys.argv:
		set_default_level(logging.DEBUG)
	else:
		set_default_level(logging.CRITICAL)
	try:
		from xpra.server import auth
		assert auth
	except ImportError as e:
		print("non server build, skipping auth module test: %s" % e)
		return
	unittest.main()

if __name__ == '__main__':
	main()
