# This file is part of Xpra.
# Copyright (C) 2010-2013 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008, 2010 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import signal
import os
import sys
import socket
import binascii
from xpra.gtk_common.gobject_compat import import_gobject, import_glib
gobject = import_gobject()

from xpra.log import Logger
log = Logger()

from xpra.net.protocol import Protocol, has_rencode, has_lz4, rencode_version, use_rencode
from xpra.scripts.config import ENCRYPTION_CIPHERS, python_platform
from xpra.version_util import version_compat_check, add_version_info
from xpra.platform.features import GOT_PASSWORD_PROMPT_SUGGESTION
from xpra.platform.info import get_name
from xpra.os_util import get_hex_uuid, get_machine_id, SIGNAMES, strtobytes, bytestostr
from xpra.util import typedict

EXIT_OK = 0
EXIT_CONNECTION_LOST = 1
EXIT_TIMEOUT = 2
EXIT_PASSWORD_REQUIRED = 3
EXIT_PASSWORD_FILE_ERROR = 4
EXIT_INCOMPATIBLE_VERSION = 5
EXIT_ENCRYPTION = 6
EXIT_FAILURE = 7
EXIT_SSH_FAILURE = 8
EXIT_PACKET_FAILURE = 9
EXIT_MMAP_TOKEN_FAILURE = 10

DEFAULT_TIMEOUT = 20*1000


class XpraClientBase(object):
    """ Base class for Xpra clients.
        Provides the glue code for:
        * sending packets via Protocol
        * handling packets received via _process_packet
        For an actual implementation, look at:
        * GObjectXpraClient
        * xpra.client.gtk2.client
        * xpra.client.gtk3.client
    """

    def __init__(self):
        self.exit_code = None
        self.compression_level = 0
        self.password = None
        self.password_file = None
        self.password_sent = False
        self.encryption = None
        self.quality = -1
        self.min_quality = 0
        self.speed = 0
        self.min_speed = -1
        #protocol stuff:
        self._protocol = None
        self._priority_packets = []
        self._ordinary_packets = []
        self._mouse_position = None
        self._aliases = {}
        self._reverse_aliases = {}
        #server state and caps:
        self.server_capabilities = {}
        self._remote_version = None
        self._remote_revision = None
        self._remote_platform = None
        self._remote_platform_release = None
        self._remote_platform_platform = None
        self._remote_platform_linux_distribution = None
        self.make_uuid()
        self.init_packet_handlers()

    def init(self, opts):
        self.compression_level = opts.compression_level
        self.password_file = opts.password_file
        self.encryption = opts.encryption
        self.quality = opts.quality
        self.min_quality = opts.min_quality
        self.speed = opts.speed
        self.min_speed = opts.min_speed

    def timeout_add(self, *args):
        raise Exception("override me!")

    def idle_add(self, *args):
        raise Exception("override me!")

    def source_remove(self, *args):
        raise Exception("override me!")


    def install_signal_handlers(self):
        def deadly_signal(signum, frame):
            sys.stderr.write("\ngot deadly signal %s, exiting\n" % SIGNAMES.get(signum, signum))
            sys.stderr.flush()
            self.cleanup()
            os._exit(128 + signum)
        def app_signal(signum, frame):
            sys.stderr.write("\ngot signal %s, exiting\n" % SIGNAMES.get(signum, signum))
            sys.stderr.flush()
            signal.signal(signal.SIGINT, deadly_signal)
            signal.signal(signal.SIGTERM, deadly_signal)
            self.timeout_add(0, self.quit, 128 + signum)
        signal.signal(signal.SIGINT, app_signal)
        signal.signal(signal.SIGTERM, app_signal)


    def client_type(self):
        #overriden in subclasses!
        return "Python"

    def setup_connection(self, conn):
        log.debug("setup_connection(%s)", conn)
        self._protocol = Protocol(conn, self.process_packet, self.next_packet)
        self._protocol.large_packets.append("keymap-changed")
        self._protocol.large_packets.append("server-settings")
        self._protocol.set_compression_level(self.compression_level)
        self.have_more = self._protocol.source_has_more

    def init_packet_handlers(self):
        self._packet_handlers = {
            "hello": self._process_hello,
            }
        self._ui_packet_handlers = {
            "challenge": self._process_challenge,
            "disconnect": self._process_disconnect,
            "set_deflate": self._process_set_deflate,
            Protocol.CONNECTION_LOST: self._process_connection_lost,
            Protocol.GIBBERISH: self._process_gibberish,
            }

    def init_aliases(self):
        packet_types = list(self._packet_handlers.keys())
        packet_types += list(self._ui_packet_handlers.keys())
        i = 1
        for key in packet_types:
            self._aliases[i] = key
            self._reverse_aliases[key] = i
            i += 1

    def send_hello(self, challenge_response=None):
        hello = self.make_hello_base(challenge_response)
        if challenge_response or not self.password_file:
            #could be the real hello, so we need all the details:
            hello.update(self.make_hello())
        log.debug("send_hello(%s) packet=%s", binascii.hexlify(challenge_response or ""), hello)
        self.send("hello", hello)

    def make_hello_base(self, challenge_response=None):
        capabilities = {"namespace"             : True,
                        "raw_packets"           : True,
                        "chunked_compression"   : True,
                        "digest"                : ("hmac", "xor"),
                        "rencode"               : has_rencode,
                        "lz4"                   : has_lz4,
                        "hostname"              : socket.gethostname(),
                        "uuid"                  : self.uuid,
                        "username"              : self.username,
                        "name"                  : get_name(),
                        "platform"              : sys.platform,
                        "platform.release"      : python_platform.release(),
                        "platform.machine"      : python_platform.machine(),
                        "platform.processor"    : python_platform.processor(),
                        "client_type"           : self.client_type(),
                        "python.version"        : sys.version_info[:3],
                        }
        if has_rencode:
            capabilities["rencode.version"] = rencode_version
        add_version_info(capabilities)

        if challenge_response:
            assert self.password
            capabilities["challenge_response"] = challenge_response
        if self.encryption:
            assert self.encryption in ENCRYPTION_CIPHERS
            capabilities["cipher"] = self.encryption
            iv = get_hex_uuid()[:16]
            capabilities["cipher.iv"] = iv
            key_salt = get_hex_uuid()
            capabilities["cipher.key_salt"] = key_salt
            iterations = 1000
            capabilities["cipher.key_stretch_iterations"] = iterations
            key = self.get_encryption_key()
            if key is None:
                self.warn_and_quit(EXIT_ENCRYPTION, "encryption key is missing")
                return
            self._protocol.set_cipher_in(self.encryption, iv, key, key_salt, iterations)
            log("encryption capabilities: %s", [(k,v) for k,v in capabilities.items() if k.startswith("cipher")])
        return capabilities

    def make_hello(self):
        capabilities = {
                        "randr_notify"        : False,        #only client.py cares about this
                        "windows"            : False,        #only client.py cares about this
                       }
        if self._reverse_aliases:
            capabilities["aliases"] = self._reverse_aliases
        return capabilities

    def make_uuid(self):
        try:
            import hashlib
            u = hashlib.sha1()
        except:
            #try python2.4 variant:
            import sha
            u = sha.new()
        def uupdate(ustr):
            u.update(ustr.encode("utf-8"))
        uupdate(get_machine_id())
        if os.name=="posix":
            uupdate(u"/")
            uupdate(str(os.getuid()))
            uupdate(u"/")
            uupdate(str(os.getgid()))
        self.uuid = u.hexdigest()

    def send(self, *parts):
        self._ordinary_packets.append(parts)
        self.have_more()

    def send_now(self, *parts):
        self._priority_packets.append(parts)
        self.have_more()

    def send_positional(self, packet):
        self._ordinary_packets.append(packet)
        self._mouse_position = None
        self.have_more()

    def send_mouse_position(self, packet):
        self._mouse_position = packet
        self.have_more()

    def have_more(self):
        #this function is overridden in setup_protocol()
        p = self._protocol
        if p and p.source:
            p.source_has_more()

    def next_packet(self):
        if self._priority_packets:
            packet = self._priority_packets.pop(0)
        elif self._ordinary_packets:
            packet = self._ordinary_packets.pop(0)
        elif self._mouse_position is not None:
            packet = self._mouse_position
            self._mouse_position = None
        else:
            packet = None
        has_more = packet is not None and \
                (bool(self._priority_packets) or bool(self._ordinary_packets) \
                 or self._mouse_position is not None)
        return packet, None, None, has_more


    def cleanup(self):
        log("XpraClientBase.cleanup() protocol=%s", self._protocol)
        if self._protocol:
            self._protocol.close()
            self._protocol = None

    def glib_init(self):
        try:
            glib = import_glib()
            try:
                glib.threads_init()
            except AttributeError:
                #old versions of glib may not have this method
                pass
        except ImportError:
            pass

    def run(self):
        self._protocol.start()

    def quit(self, exit_code=0):
        raise Exception("override me!")

    def warn_and_quit(self, exit_code, warning):
        log.warn(warning)
        self.quit(exit_code)

    def _process_disconnect(self, packet):
        if len(packet)==2:
            info = packet[1]
        else:
            info = packet[1:]
        e = EXIT_OK
        if self.server_capabilities is None or len(self.server_capabilities)==0:
            #server never sent hello to us - so disconnect is an error
            #(but we don't know which one - the info message may help)
            e = EXIT_FAILURE
        self.warn_and_quit(e, "server requested disconnect: %s" % info)

    def _process_connection_lost(self, packet):
        self.warn_and_quit(EXIT_CONNECTION_LOST, "Connection lost")

    def _process_challenge(self, packet):
        if not self.password_file and not self.password:
            self.warn_and_quit(EXIT_PASSWORD_REQUIRED, "password is required by the server")
            return
        if not self.password:
            if not self.load_password():
                return
            assert self.password
        salt = packet[1]
        if self.encryption:
            assert len(packet)>=3, "challenge does not contain encryption details to use for the response"
            server_cipher = packet[2]
            self.set_server_encryption(server_cipher)
        import hmac
        challenge_response = hmac.HMAC(self.password, salt)
        password_hash = challenge_response.hexdigest()
        self.password_sent = True
        self.send_hello(password_hash)

    def set_server_encryption(self, capabilities):
        def get(key, default=None):
            return capabilities.get(strtobytes(key), default)
        cipher = get("cipher")
        cipher_iv = get("cipher.iv")
        key_salt = get("cipher.key_salt")
        iterations = get("cipher.key_stretch_iterations")
        if not cipher or not cipher_iv:
            self.warn_and_quit(EXIT_ENCRYPTION, "the server does not use or support encryption/password, cannot continue with %s cipher" % self.encryption)
            return False
        if cipher not in ENCRYPTION_CIPHERS:
            self.warn_and_quit(EXIT_ENCRYPTION, "unsupported server cipher: %s, allowed ciphers: %s" % (cipher, ", ".join(ENCRYPTION_CIPHERS)))
            return False
        self._protocol.set_cipher_out(cipher, cipher_iv, self.get_password(), key_salt, iterations)


    def get_password(self):
        if self.password is None:
            self.load_password()
        return self.password

    def load_password(self):
        try:
            filename = os.path.expanduser(self.password_file)
            passwordFile = open(filename, "rU")
            self.password = passwordFile.read()
            passwordFile.close()
            while self.password.endswith("\n") or self.password.endswith("\r"):
                self.password = self.password[:-1]
        except IOError, e:
            self.warn_and_quit(EXIT_PASSWORD_FILE_ERROR, "failed to open password file %s: %s" % (self.password_file, e))
            return False
        log("password read from file %s is %s", self.password_file, self.password)
        return True

    def _process_hello(self, packet):
        if not self.password_sent and self.password_file:
            log.warn("Warning: the server did not request our password!")
        self.server_capabilities = packet[1]
        c = typedict(self.server_capabilities)
        self.parse_server_capabilities(c)

    def capsget(self, capabilities, key, default):
        v = capabilities.get(strtobytes(key), default)
        if sys.version >= '3' and type(v)==bytes:
            v = bytestostr(v)
        return v

    def parse_server_capabilities(self, c):
        self._remote_version = c.strget("version")
        self._remote_revision = c.strget("revision")
        self._remote_revision = c.strget("build.revision", self._remote_revision)
        self._remote_platform = c.strget("platform")
        self._remote_platform_release = c.strget("platform.release")
        self._remote_platform_platform = c.strget("platform.platform")
        self._remote_platform_linux_distribution = c.get("platform.linux_distribution")
        verr = version_compat_check(self._remote_version)
        if verr is not None:
            self.warn_and_quit(EXIT_INCOMPATIBLE_VERSION, "incompatible remote version %s: %s" % (self._remote_version, verr))
            return False

        self._protocol.chunked_compression = c.boolget("chunked_compression")
        if use_rencode and c.boolget("rencode"):
            self._protocol.enable_rencode()
        if c.boolget("lz4") and has_lz4 and self._protocol.chunked_compression and self.compression_level>0 and self.compression_level<3:
            self._protocol.enable_lz4()
        if self.encryption:
            #server uses a new cipher after second hello:
            self.set_server_encryption(c)
        self._protocol.aliases = c.dictget("aliases", {})
        if self.pings:
            self.timeout_add(1000, self.send_ping)
        else:
            self.timeout_add(10*1000, self.send_ping)
        return True

    def _process_set_deflate(self, packet):
        #legacy, should not be used for anything
        pass

    def _process_gibberish(self, packet):
        (_, data) = packet
        log.info("Received uninterpretable nonsense: %s", repr(data))
        if str(data).find("assword")>0:
            log.warn("Your ssh program appears to be asking for a password."
                             + GOT_PASSWORD_PROMPT_SUGGESTION)
            self.quit(EXIT_SSH_FAILURE)
        elif str(data).find("login")>=0:
            log.warn("Your ssh program appears to be asking for a username.\n"
                             "Perhaps try using something like 'ssh:USER@host:display'?")
            self.quit(EXIT_SSH_FAILURE)
        else:
            self.quit(EXIT_PACKET_FAILURE)

    def process_packet(self, proto, packet):
        try:
            handler = None
            packet_type = packet[0]
            if type(packet_type)==int:
                packet_type = self._aliases.get(packet_type)
            handler = self._packet_handlers.get(packet_type)
            if handler:
                handler(packet)
                return
            handler = self._ui_packet_handlers.get(packet_type)
            if not handler:
                log.error("unknown packet type: %s", packet_type)
                return
            self.idle_add(handler, packet)
        except KeyboardInterrupt:
            raise
        except:
            log.error("Unhandled error while processing a '%s' packet from peer using %s", packet_type, handler, exc_info=True)
