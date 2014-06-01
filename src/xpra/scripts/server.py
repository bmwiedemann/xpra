# This file is part of Xpra.
# Copyright (C) 2010-2014 Antoine Martin <antoine@devloop.org.uk>
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

# DO NOT IMPORT GTK HERE: see
#  http://lists.partiwm.org/pipermail/parti-discuss/2008-September/000041.html
#  http://lists.partiwm.org/pipermail/parti-discuss/2008-September/000042.html
# (also do not import anything that imports gtk)
import gobject
import glib
import subprocess
import sys
import os.path
import atexit
import signal
import socket
import getpass
import select
import time

from xpra.scripts.main import TCP_NODELAY
from xpra.dotxpra import DotXpra, ServerSockInUse

o0117 = 79
o0177 = 127
o0666 = 438
o0700 = 448

_cleanups = []
def run_cleanups():
    global _cleanups
    cleanups = _cleanups
    _cleanups = []
    for c in cleanups:
        try:
            c()
        except:
            print("error running cleanup %s" % c)
            import traceback
            traceback.print_exception(*sys.exc_info())

_when_ready = []

def deadly_signal(signum, frame):
    print("got deadly signal %s, exiting" % {signal.SIGINT:"SIGINT", signal.SIGTERM:"SIGTERM"}.get(signum, signum))
    run_cleanups()
    # This works fine in tests, but for some reason if I use it here, then I
    # get bizarre behavior where the signal handler runs, and then I get a
    # KeyboardException (?!?), and the KeyboardException is handled normally
    # and exits the program (causing the cleanup handlers to be run again):
    #signal.signal(signum, signal.SIG_DFL)
    #kill(os.getpid(), signum)
    os._exit(128 + signum)

# Note that this class has async subtleties -- e.g., it is possible for a
# child to exit and us to receive the SIGCHLD before our fork() returns (and
# thus before we even know the pid of the child).  So be careful:
class ChildReaper(object):
    def __init__(self, quit_cb):
        self._quit = quit_cb
        self._children_pids = {}
        self._dead_pids = set()
        from xpra.log import Logger
        self._logger = Logger("server", "util")
        old_python = sys.version_info < (2, 7) or sys.version_info[:2] == (3, 0)
        if old_python:
            POLL_DELAY = int(os.environ.get("XPRA_POLL_DELAY", 2))
            self._logger.warn("Warning: outdated/buggy version of Python: %s", ".".join(str(x) for x in sys.version_info))
            self._logger.warn("switching to process polling every %s seconds to support 'exit-with-children'", POLL_DELAY)
            #keep track of process objects:
            self.processes = []
            def check_procs():
                for proc in self.processes:
                    if proc.poll() is not None:
                        self.add_dead_pid(proc.pid)
                        self.check()
                self.processes = [proc for proc in self.processes if proc.poll() is None]
                return True
            gobject.timeout_add(POLL_DELAY*1000, check_procs)
        else:
            #with a less buggy python, we can just check the list of pids
            #whenever we get a SIGCHLD
            #however.. subprocess.Popen will no longer work as expected
            #see: http://bugs.python.org/issue9127
            #so we must ensure certain things that exec happen first:
            from xpra.version_util import get_platform_info_cache
            get_platform_info_cache()

            signal.signal(signal.SIGCHLD, self.sigchld)
            # Check once after the mainloop is running, just in case the exit
            # conditions are satisfied before we even enter the main loop.
            # (Programming with unix the signal API sure is annoying.)
            def check_once():
                self.check()
                return False # Only call once
            gobject.timeout_add(0, check_once)

    def add_process(self, process, command):
        process.command = command
        self._children_pids[process.pid] = process

    def check(self):
        if self._children_pids:
            for pid, proc in self._children_pids.items():
                if proc.poll() is not None:
                    self.add_dead_pid(pid)
            pids = set(self._children_pids.keys())
            if pids.issubset(self._dead_pids):
                self._quit()

    def sigchld(self, signum, frame):
        self.reap()

    def add_dead_pid(self, pid):
        if pid not in self._dead_pids:
            proc = self._children_pids.get(pid)
            if proc:
                self._logger.info("child '%s' with pid %s has terminated", proc.command, pid)
            self._dead_pids.add(pid)
            self.check()

    def reap(self):
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except OSError:
                break
            if pid == 0:
                break
            self.add_dead_pid(pid)

def save_xvfb_pid(pid):
    import gtk
    from xpra.x11.gtk_x11.prop import prop_set
    prop_set(gtk.gdk.get_default_root_window(),
                           "_XPRA_SERVER_PID", "u32", pid)

def get_xvfb_pid():
    import gtk
    from xpra.x11.gtk_x11.prop import prop_get
    return prop_get(gtk.gdk.get_default_root_window(),
                                  "_XPRA_SERVER_PID", "u32")

def sh_quotemeta(s):
    safe = ("abcdefghijklmnopqrstuvwxyz"
            + "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            + "0123456789"
            + "/._:,-+")
    quoted_chars = []
    for char in s:
        if char not in safe:
            quoted_chars.append("\\")
        quoted_chars.append(char)
    return "\"%s\"" % ("".join(quoted_chars),)

def xpra_runner_shell_script(xpra_file, starting_dir, socket_dir):
    script = []
    script.append("#!/bin/sh\n")
    for var, value in os.environ.items():
        # these aren't used by xpra, and some should not be exposed
        # as they are either irrelevant or simply do not match
        # the new environment used by xpra
        # TODO: use a whitelist
        if var in ["XDG_SESSION_COOKIE", "LS_COLORS", "DISPLAY"]:
            continue
        #XPRA_SOCKET_DIR is a special case, it is handled below
        if var=="XPRA_SOCKET_DIR":
            continue
        # :-separated envvars that people might change while their server is
        # going:
        if var in ("PATH", "LD_LIBRARY_PATH", "PYTHONPATH"):
            script.append("%s=%s:\"$%s\"; export %s\n"
                          % (var, sh_quotemeta(value), var, var))
        else:
            script.append("%s=%s; export %s\n"
                          % (var, sh_quotemeta(value), var))
    #XPRA_SOCKET_DIR is a special case, we want to honour it
    #when it is specified, but the client may override it:
    if socket_dir:
        script.append('if [ -z "${XPRA_SOCKET_DIR}" ]; then\n');
        script.append('    XPRA_SOCKET_DIR=%s; export XPRA_SOCKET_DIR\n' % sh_quotemeta(os.path.expanduser(socket_dir)))
        script.append('fi\n');
    # We ignore failures in cd'ing, b/c it's entirely possible that we were
    # started from some temporary directory and all paths are absolute.
    script.append("cd %s\n" % sh_quotemeta(starting_dir))
    script.append("_XPRA_PYTHON=%s\n" % (sh_quotemeta(sys.executable),))
    script.append("_XPRA_SCRIPT=%s\n" % (sh_quotemeta(xpra_file),))
    script.append("""
if which "$_XPRA_PYTHON" > /dev/null && [ -e "$_XPRA_SCRIPT" ]; then
    # Happypath:
    exec "$_XPRA_PYTHON" "$_XPRA_SCRIPT" "$@"
else
    cat >&2 <<END
    Could not find one or both of '$_XPRA_PYTHON' and '$_XPRA_SCRIPT'
    Perhaps your environment has changed since the xpra server was started?
    I'll just try executing 'xpra' with current PATH, and hope...
END
    exec xpra "$@"
fi
""")
    return "".join(script)

def write_runner_shell_script(dotxpra, contents):
    # This used to be given a display-specific name, but now we give it a
    # single fixed name and if multiple servers are started then the last one
    # will clobber the rest.  This isn't great, but the tradeoff is that it
    # makes it possible to use bare 'ssh:hostname' display names and
    # autodiscover the proper numeric display name when only one xpra server
    # is running on the remote host.  Might need to revisit this later if
    # people run into problems or autodiscovery turns out to be less useful
    # than expected.
    scriptpath = os.path.join(dotxpra.confdir(), "run-xpra")
    # Write out a shell-script so that we can start our proxy in a clean
    # environment:
    scriptfile = open(scriptpath, "w")
    # Unix is a little silly sometimes:
    umask = os.umask(0)
    os.umask(umask)
    if hasattr(os, "fchmod"):
        os.fchmod(scriptfile.fileno(), o0700 & ~umask)
    else:
        os.chmod(scriptpath, o0700 & ~umask)
    scriptfile.write(contents)
    scriptfile.close()


def display_name_check(display_name):
    """ displays a warning
        when a low display number is specified """
    if not display_name.startswith(":"):
        return
    n = display_name[1:].split(".")[0]    #ie: ":0.0" -> "0"
    try:
        dno = int(n)
        if dno>=0 and dno<10:
            sys.stderr.write("WARNING: low display number: %s\n" % dno)
            sys.stderr.write("You are attempting to run the xpra server against what seems to be a default X11 display '%s'.\n" % display_name)
            sys.stderr.write("This is generally not what you want.\n")
            sys.stderr.write("You should probably use a higher display number just to avoid any confusion (and also this warning message).\n")
    except:
        pass


def get_ssh_port():
    #FIXME: how do we find out which port ssh is on?
    return 22

#warn just once:
MDNS_WARNING = False
def mdns_publish(display_name, mode, listen_on, text_dict={}):
    try:
        from xpra.net.avahi_publisher import AvahiPublishers
    except Exception, e:
        global MDNS_WARNING
        if not MDNS_WARNING:
            MDNS_WARNING = True
            from xpra.log import Logger
            log = Logger("mdns")
            log.error("failed to load the mdns avahi publisher: %s", e)
            log.error("either fix your installation or use the '--no-mdns' flag")
        return
    d = text_dict.copy()
    d["mode"] = mode
    ap = AvahiPublishers(listen_on, "Xpra %s %s" % (mode, display_name), text_dict=d)
    _when_ready.append(ap.start)
    _cleanups.append(ap.stop)


def create_unix_domain_socket(sockpath, mmap_group):
    listener = socket.socket(socket.AF_UNIX)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    #bind the socket, using umask to set the correct permissions
    if mmap_group:
        orig_umask = os.umask(o0117) #660
    else:
        orig_umask = os.umask(o0177) #600
    listener.bind(sockpath)
    os.umask(orig_umask)
    return listener

def create_tcp_socket(host, iport):
    if host.find(":")<0:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sockaddr = (host, iport)
    else:
        assert socket.has_ipv6, "specified an IPv6 address but this is not supported"
        res = socket.getaddrinfo(host, iport, socket.AF_INET6, socket.SOCK_STREAM, 0, socket.SOL_TCP)
        listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sockaddr = res[0][-1]
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, TCP_NODELAY)
    listener.bind(sockaddr)
    return listener

def setup_tcp_socket(host, iport):
    from xpra.log import Logger
    log = Logger("network")
    tcp_socket = create_tcp_socket(host, iport)
    def cleanup_tcp_socket():
        log.info("closing tcp socket %s:%s", host, iport)
        try:
            tcp_socket.close()
        except:
            pass
    _cleanups.append(cleanup_tcp_socket)
    return "tcp", tcp_socket

def parse_bind_tcp(bind_tcp):
    tcp_sockets = set()
    if bind_tcp:
        for spec in bind_tcp:
            if ":" not in spec:
                raise Exception("TCP port must be specified as [HOST]:PORT")
            host, port = spec.rsplit(":", 1)
            if host == "":
                host = "127.0.0.1"
            try:
                iport = int(port)
            except:
                raise Exception("invalid port number: %s" % port)
            tcp_sockets.add((host, iport))
    return tcp_sockets


def setup_local_socket(dotxpra, display_name, clobber, mmap_group):
    if sys.platform.startswith("win"):
        return None, None
    from xpra.log import Logger
    log = Logger("network")
    #print("creating server socket %s" % sockpath)
    try:
        display_name = dotxpra.normalize_local_display_name(display_name)
        sockpath = dotxpra.server_socket_path(display_name, clobber, wait_for_unknown=5)
    except ServerSockInUse:
        raise Exception("You already have an xpra server running at %s\n"
                     "  (did you want 'xpra upgrade'?)"
                     % (display_name,))
    except Exception, e:
        raise Exception("socket path error: %s" % e)
    sock = create_unix_domain_socket(sockpath, mmap_group)
    def cleanup_socket():
        log.info("removing socket %s", sockpath)
        try:
            os.unlink(sockpath)
        except:
            pass
    _cleanups.append(cleanup_socket)
    return ("unix-domain", sock), cleanup_socket

def close_all_fds(exceptions=[]):
    fd_dirs = ["/dev/fd", "/proc/self/fd"]
    for fd_dir in fd_dirs:
        if os.path.exists(fd_dir):
            for fd_str in os.listdir(fd_dir):
                try:
                    fd = int(fd_str)
                    if fd not in exceptions:
                        os.close(fd)
                except OSError:
                    # This exception happens inevitably, because the fd used
                    # by listdir() is already closed.
                    pass
            return
    print("Uh-oh, can't close fds, please port me to your system...")

def shellsub(s, subs={}):
    """ shell style string substitution using the dictionary given """
    for var,value in subs.items():
        s = s.replace("$%s" % var, value)
        s = s.replace("${%s}" % var, value)
    return s

def open_log_file(logpath):
    """ renames the existing log file if it exists,
        then opens it for writing.
    """
    if os.path.exists(logpath):
        os.rename(logpath, logpath + ".old")
    return os.open(logpath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, o0666)

def select_log_file(dotxpra, log_file, display_name):
    """ returns the log file path we should be using given the parameters,
        this may return a temporary logpath if display_name is not available.
    """
    if log_file and display_name:
        if os.path.isabs(log_file):
            logpath = log_file
        else:
            logpath = os.path.join(dotxpra.sockdir(), log_file)
        logpath = shellsub(logpath, {"DISPLAY" : display_name})
    elif display_name:
        logpath = dotxpra.log_path(display_name) + ".log"
    else:
        logpath = os.path.join(dotxpra.sockdir(), "tmp_%d.log" % os.getpid())
    return logpath


def daemonize(logfd):
    os.chdir("/")
    if os.fork():
        os._exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    # save current stdout/stderr to be able to print info
    # before exiting the non-deamon process
    # and closing those file descriptors definitively
    old_fd_stdout = os.dup(1)
    old_fd_stderr = os.dup(2)
    close_all_fds(exceptions=[logfd,old_fd_stdout,old_fd_stderr])
    fd0 = os.open("/dev/null", os.O_RDONLY)
    if fd0 != 0:
        os.dup2(fd0, 0)
        os.close(fd0)
    # reopen STDIO files
    old_stdout = os.fdopen(old_fd_stdout, "w", 1)
    old_stderr = os.fdopen(old_fd_stderr, "w", 1)
    # replace standard stdout/stderr by the log file
    os.dup2(logfd, 1)
    os.dup2(logfd, 2)
    os.close(logfd)
    # Make these line-buffered:
    sys.stdout = os.fdopen(1, "w", 1)
    sys.stderr = os.fdopen(2, "w", 1)
    return (old_stdout, old_stderr)


def sanitize_env():
    def unsetenv(*varnames):
        for x in varnames:
            if x in os.environ:
                del os.environ[x]
    #we don't want client apps to think these mean anything:
    #(if set, they belong to the desktop the server was started from)
    #TODO: simply whitelisting the env would be safer/better
    unsetenv("DESKTOP_SESSION",
             "GDMSESSION",
             "SESSION_MANAGER",
             "XDG_VTNR",
             "XDG_MENU_PREFIX",
             "XDG_SEAT",
             #"XDG_RUNTIME_DIR",
             "QT_GRAPHICSSYSTEM_CHECKED",
             )
    #force 'simple' / 'xim', as 'ibus' 'immodule' breaks keyboard handling
    #unless its daemon is also running - and we don't know if it is..
    #this should override any XSETTINGS too.
    #for more information, see imsettings:
    #https://code.google.com/p/imsettings/source/browse/trunk/README
    os.environ.update({
               "DISABLE_IMSETTINGS" : "true",
               "GTK_IM_MODULE"      : "xim",                #or "gtk-im-context-simple"?
               "QT_IM_MODULE"       : "xim",                #or "simple"?
               "IMSETTINGS_MODULE"  : "none",               #or "xim"?
               "XMODIFIERS"         : ""})                  #oe "@im=none"

def start_pulseaudio(child_reaper, pulseaudio_command):
    from xpra.log import Logger
    log = Logger("sound")
    log("pulseaudio_command=%s", pulseaudio_command)
    pa_proc = subprocess.Popen(pulseaudio_command, stdin=subprocess.PIPE, shell=True, close_fds=True)
    child_reaper.add_process(pa_proc, "pulseaudio")
    log.info("pulseaudio server started with pid %s", pa_proc.pid)
    def check_pa_start():
        if pa_proc.poll() is not None or pa_proc.pid in child_reaper._dead_pids:
            log.warn("Warning: pulseaudio has terminated. Either fix the pulseaudio command line or use --no-pulseaudio to avoid this warning.")
            log.warn(" usually, only a single pulseaudio instance can be running for each user account, and one may be running already")
        return False
    gobject.timeout_add(1000*2, check_pa_start)
    def cleanup_pa():
        log("cleanup_pa() process.poll()=%s, pid=%s, dead_pids=%s", pa_proc.poll(), pa_proc.pid, child_reaper._dead_pids)
        if pa_proc.poll() is None and pa_proc.pid not in child_reaper._dead_pids:
            log.info("stopping pulseaudio with pid %s", pa_proc.pid)
            try:
                pa_proc.terminate()
            except:
                log.warn("error trying to stop pulseaudio", exc_info=True)
    _cleanups.append(cleanup_pa)

def start_Xvfb(xvfb_str, display_name):
    # We need to set up a new server environment
    xauthority = os.environ.get("XAUTHORITY", os.path.expanduser("~/.Xauthority"))
    if not os.path.exists(xauthority):
        try:
            open(xauthority, 'wa').close()
        except Exception, e:
            #trying to continue anyway!
            sys.stderr.write("Error trying to create XAUTHORITY file %s: %s\n" % (xauthority, e))

    #identify logfile argument if it exists and if we may have to rename it:
    xvfb_cmd = xvfb_str.split()
    if '-logfile' in xvfb_cmd and display_name[0]=='S':
        xvfb_cmd = xvfb_str.split()
        logfile_argindex = xvfb_cmd.index('-logfile') + 1
        assert logfile_argindex<len(xvfb_cmd), "invalid xvfb command string: -logfile should not be last"
        xorg_log_file = xvfb_cmd[logfile_argindex]
    else:
        xorg_log_file = None

    #apply string substitutions: 
    subs = {"XAUTHORITY"    : xauthority,
            "USER"          : os.environ.get("USER", "unknown-user"),
            "HOME"          : os.environ.get("HOME", os.getcwd()),
            "DISPLAY"       : display_name}
    xvfb_str = shellsub(xvfb_str, subs)

    def setsid():
        #run in a new session
        if os.name=="posix":
            os.setsid()

    xvfb_cmd = xvfb_str.split()
    xvfb_executable = xvfb_cmd[0]
    if display_name[0]=='S':
        # 'S' means that we allocate the display automatically
        r_pipe, w_pipe = os.pipe()
        xvfb_cmd += ["-displayfd", str(w_pipe)]
        xvfb_cmd[0] = "%s-for-Xpra-%s" % (xvfb_executable, display_name)
        def preexec():
            setsid()
            #duplicate python's _close_fds() function
            #(taking care to exclude the pipe)
            try:
                MAXFD = os.sysconf("SC_OPEN_MAX")
            except:
                MAXFD = 256
            for i in xrange(3, MAXFD):
                if i not in (r_pipe, w_pipe):
                    try:
                        os.close(i)
                    except:
                        pass
        xvfb = subprocess.Popen(xvfb_cmd, executable=xvfb_executable, close_fds=False,
                                stdin=subprocess.PIPE, preexec_fn=preexec)
        # Read the display number from the pipe we gave to Xvfb
        # waiting up to 10 seconds for it to show up
        limit = time.time()+10
        buf = ""
        while time.time()<limit and len(buf)<8:
            r, _, _ = select.select([r_pipe], [], [], max(0, limit-time.time()))
            if r_pipe in r:
                buf += os.read(r_pipe, 8)
                if buf[-1] == '\n':
                    break
        os.close(r_pipe)
        os.close(w_pipe)
        if len(buf) == 0:
            raise OSError("%s did not provide a display number using -displayfd" % xvfb_executable)
        if buf[-1] != '\n':
            raise OSError("%s output not terminated by newline: %s" % (xvfb_executable, buf))
        try:
            n = int(buf[:-1])
        except:
            raise OSError("%s display number is not a valid number: %s" % (xvfb_executable, buf[:-1]))
        if n<0 or n>=2**16:
            raise OSError("%s provided an invalid display number: %s" % (xvfb_executable, n))
        new_display_name = ":%s" % n
        sys.stdout.write("Using display number provided by %s: %s\n" % (xvfb_executable, new_display_name))
        if xorg_log_file != None:
            #ie: ${HOME}/.xpra/Xorg.${DISPLAY}.log -> /home/antoine/.xpra/Xorg.S14700.log
            f0 = shellsub(xorg_log_file, subs)
            subs["DISPLAY"] = new_display_name
            #ie: ${HOME}/.xpra/Xorg.${DISPLAY}.log -> /home/antoine/.xpra/Xorg.:1.log
            f1 = shellsub(xorg_log_file, subs)
            if f0 != f1:
                os.rename(f0, f1)
        display_name = new_display_name
    else:
        # use display specified
        xvfb_cmd[0] = "%s-for-Xpra-%s" % (xvfb_executable, display_name)
        xvfb_cmd.append(display_name)
        xvfb = subprocess.Popen(xvfb_cmd, executable=xvfb_executable, close_fds=True,
                                stdin=subprocess.PIPE, preexec_fn=setsid)

    from xpra.os_util import get_hex_uuid
    xauth_cmd = ["xauth", "add", display_name, "MIT-MAGIC-COOKIE-1", get_hex_uuid()]
    try:
        code = subprocess.call(xauth_cmd)
        if code != 0:
            raise OSError("non-zero exit code: %s" % code)
    except OSError, e:
        #trying to continue anyway!
        sys.stderr.write("Error running \"%s\": %s\n" % (" ".join(xauth_cmd), e))
    return xvfb, display_name

def check_xvfb_process(xvfb=None):
    if xvfb is None:
        #we don't have a process to check
        return True
    if xvfb.poll() is None:
        #process is running
        return True
    from xpra.log import Logger
    log = Logger("server")
    log.error("")
    log.error("Xvfb command has terminated! xpra cannot continue")
    log.error("")
    return False

def verify_display_ready(xvfb, display_name, shadowing):
    from xpra.log import Logger
    log = Logger("server")
    from xpra.x11.bindings.wait_for_x_server import wait_for_x_server        #@UnresolvedImport
    # Whether we spawned our server or not, it is now running -- or at least
    # starting.  First wait for it to start up:
    try:
        wait_for_x_server(display_name, 3) # 3s timeout
    except Exception, e:
        sys.stderr.write("%s\n" % e)
        return  None
    # Now we can safely load gtk and connect:
    assert "gtk" not in sys.modules
    import gtk.gdk          #@Reimport
    try:
        glib.threads_init()
    except:
        #old versions do not have this method
        pass
    display = gtk.gdk.Display(display_name)
    manager = gtk.gdk.display_manager_get()
    default_display = manager.get_default_display()
    if default_display is not None:
        default_display.close()
    manager.set_default_display(display)
    if not shadowing and not check_xvfb_process(xvfb):
        #if we're here, there is an X11 server, but it isn't the one we started!
        log.error("There is an X11 server already running on display %s:" % display_name)
        log.error("You may want to use:")
        log.error("  'xpra upgrade %s' if an instance of xpra is still connected to it" % display_name)
        log.error("  'xpra --use-display start %s' to connect xpra to an existing X11 server only" % display_name)
        log.error("")
        return  None
    return display

def find_lib(libname):
    #it would be better to rely on dlopen to find the paths
    #but I cannot find a way of getting ctypes to tell us the path
    #it found the library in
    libpaths = os.environ.get("LD_LIBRARY_PATH", "").split(":")
    libpaths.append("/usr/lib64")
    libpaths.append("/usr/lib")
    for libpath in libpaths:
        if not libpath or not os.path.exists(libpath):
            continue
        libname_so = os.path.join(libpath, libname)
        if os.path.exists(libname_so):
            return libname_so
    return None

def find_fakeXinerama():
    return find_lib("libfakeXinerama.so.1")


def start_children(child_reaper, commands, fake_xinerama):
    assert os.name=="posix"
    from xpra.log import Logger
    log = Logger("server")
    env = os.environ.copy()
    #add fake xinerama:
    if fake_xinerama:
        libfakeXinerama_so = find_fakeXinerama()
        if libfakeXinerama_so:
            env["LD_PRELOAD"] = libfakeXinerama_so
    #disable ubuntu's global menu using env vars:
    env.update({
        "UBUNTU_MENUPROXY"          : "",
        "QT_X11_NO_NATIVE_MENUBAR"  : "1"})
    for child_cmd in commands:
        if not child_cmd:
            continue
        try:
            proc = subprocess.Popen(child_cmd, stdin=subprocess.PIPE, env=env, shell=True, close_fds=True)
            child_reaper.add_process(proc, child_cmd)
            log.info("started child '%s' with pid %s", child_cmd, proc.pid)
        except OSError, e:
            sys.stderr.write("Error spawning child '%s': %s\n" % (child_cmd, e))


def run_server(parser, opts, mode, xpra_file, extra_args):
    if opts.encoding and opts.encoding=="help":
        #avoid errors and warnings:
        opts.encoding = ""
        opts.clipboard = False
        opts.notifications = False
        print("xpra server supports the following encodings:")
        print("(please wait, encoder initialization may take a few seconds)")
        #disable info logging which would be confusing here
        from xpra.log import get_all_loggers, set_default_level
        import logging
        set_default_level(logging.WARN)
        logging.root.setLevel(logging.WARN)
        for x in get_all_loggers():
            x.logger.setLevel(logging.WARN)
        from xpra.server.server_base import ServerBase
        sb = ServerBase()
        sb.init(opts)
        #ensures that the threaded video helper init has completed
        #(by running it again, which will block on the init lock)
        from xpra.codecs.video_helper import getVideoHelper
        getVideoHelper().init()
        sb.init_encodings()
        from xpra.codecs.loader import encoding_help
        for e in sb.encodings:
            print(" * %s" % encoding_help(e))
        return 0

    assert mode in ("start", "upgrade", "shadow", "proxy")
    starting  = mode == "start"
    upgrading = mode == "upgrade"
    shadowing = mode == "shadow"
    proxying  = mode == "proxy"
    clobber   = upgrading or opts.use_display

    #get the display name:
    if shadowing and len(extra_args)==0:
        from xpra.scripts.main import guess_X11_display
        display_name = guess_X11_display()
    else:
        if len(extra_args) > 1:
            parser.error("too many extra arguments: only expected a display number")
        if len(extra_args) == 1:
            display_name = extra_args[0]
            display_name_check(display_name)
        else:
            if not opts.displayfd:
                parser.error("displayfd support is not enabled on this system, you must specify the display to use")
            # We will try to find one automaticaly
            # Use the temporary magic value 'S' as marker:
            display_name = 'S' + str(os.getpid())

    if not shadowing and not proxying and opts.exit_with_children and not opts.start_child:
        sys.stderr.write("--exit-with-children specified without any children to spawn; exiting immediately")
        return  1

    atexit.register(run_cleanups)
    #the server class will usually override those:
    signal.signal(signal.SIGINT, deadly_signal)
    signal.signal(signal.SIGTERM, deadly_signal)

    dotxpra = DotXpra(opts.socket_dir)

    # Generate the script text now, because os.getcwd() will
    # change if/when we daemonize:
    script = xpra_runner_shell_script(xpra_file, os.getcwd(), opts.socket_dir)

    stdout = sys.stdout
    stderr = sys.stderr
    # Daemonize:
    if opts.daemon:
        #daemonize will chdir to "/", so try to use an absolute path:
        if opts.password_file:
            opts.password_file = os.path.abspath(opts.password_file)
        # At this point we may not know the display name,
        # so log_filename0 may point to a temporary file which we will rename later
        log_filename0 = select_log_file(dotxpra, opts.log_file, display_name)
        logfd = open_log_file(log_filename0)
        assert logfd > 2
        stdout, stderr = daemonize(logfd)
        stderr.write("Entering daemon mode; "
                 + "any further errors will be reported to:\n"
                 + ("  %s\n" % log_filename0))

    # Write out a shell-script so that we can start our proxy in a clean
    # environment:
    write_runner_shell_script(dotxpra, script)

    from xpra.log import Logger
    log = Logger("server")

    mdns_recs = []
    sockets = []
    try:
        # Initialize the TCP sockets before the display,
        # That way, errors won't make us kill the Xvfb
        # (which may not be ours to kill at that point)
        bind_tcp = parse_bind_tcp(opts.bind_tcp)
        for host, iport in bind_tcp:
            socket = setup_tcp_socket(host, iport)
            sockets.append(socket)
        if opts.mdns:
            mdns_recs.append(("tcp", bind_tcp))
    except Exception, e:
        log.error("cannot start server: failed to setup sockets: %s", e)
        return 1

    # Do this after writing out the shell script:
    if display_name[0] != 'S':
        os.environ["DISPLAY"] = display_name
    sanitize_env()

    # Start the Xvfb server first to get the display_name if needed
    xvfb = None
    xvfb_pid = None
    if not shadowing and not proxying and not clobber:
        try:
            xvfb, display_name = start_Xvfb(opts.xvfb, display_name)
        except OSError, e:
            log.error("Error starting Xvfb: %s\n", e)
            return  1
        xvfb_pid = xvfb.pid
        #always update as we may now have the "real" display name:
        os.environ["DISPLAY"] = display_name

    if opts.daemon:
        log_filename1 = select_log_file(dotxpra, opts.log_file, display_name)
        if log_filename0 != log_filename1:
            # we now have the correct log filename, so use it:
            os.rename(log_filename0, log_filename1)
            stderr.write("Actual log file name is now: %s" % log_filename1)
        stdout.close()
        stderr.close()

    if not check_xvfb_process(xvfb):
        #xvfb problem: exit now
        return  1

    #setup unix domain socket:
    socket, cleanup_socket = setup_local_socket(dotxpra, display_name, clobber, opts.mmap_group)
    if socket:      #win32 returns None!
        sockets.append(socket)
        if opts.mdns:
            ssh_port = get_ssh_port()
            if ssh_port:
                mdns_recs.append(("ssh", [("", ssh_port)]))

    #publish mdns records:
    if opts.mdns:
        mdns_info = {"display" : display_name,
                     "username": getpass.getuser()}
        if opts.session_name:
            mdns_info["session"] = opts.session_name
        for mode, listen_on in mdns_recs:
            mdns_publish(display_name, mode, listen_on, mdns_info)

    if not check_xvfb_process(xvfb):
        #xvfb problem: exit now
        return  1

    display = None
    if not sys.platform.startswith("win") and not sys.platform.startswith("darwin") and not proxying:
        display = verify_display_ready(xvfb, display_name, shadowing)
        if not display:
            return 1
    elif not proxying:
        assert "gtk" not in sys.modules
        import gtk          #@Reimport
        assert gtk

    if shadowing:
        from xpra.platform.shadow_server import ShadowServer
        app = ShadowServer()
        app.init(opts)
    elif proxying:
        from xpra.server.proxy_server import ProxyServer
        app = ProxyServer()
        try:
            app.init(opts)
        except Exception, e:
            log.error("Error: cannot start the proxy server")
            log.error(str(e))
            log.info("")
            return 1
    else:
        assert starting or upgrading
        from xpra.x11.gtk_x11 import gdk_display_source
        assert gdk_display_source
        #(now we can access the X11 server)

        if clobber:
            #get the saved pid (there should be one):
            xvfb_pid = get_xvfb_pid()
        elif xvfb_pid is not None:
            #save the new pid (we should have one):
            save_xvfb_pid(xvfb_pid)

        #check for an existing window manager:
        from xpra.x11.gtk_x11.wm import wm_check
        if not wm_check(display, upgrading):
            return 1
        try:
            # This import is delayed because the module depends on gtk:
            from xpra.x11.server import XpraServer
            from xpra.x11.bindings.window_bindings import X11WindowBindings     #@UnresolvedImport
            X11Window = X11WindowBindings()
        except ImportError, e:
            log.error("Failed to load Xpra server components, check your installation: %s" % e)
            return 1
        if not X11Window.displayHasXComposite():
            log.error("Xpra is a compositing manager, it cannot use a display which lacks the XComposite extension!")
            return 1
        app = XpraServer()
        app.init(clobber, opts)
        _cleanups.insert(0, app.cleanup)

    app.init_sockets(sockets)
    app.init_when_ready(_when_ready)

    #we got this far so the sockets have initialized and
    #the server should be able to manage the display
    #from now on, if we exit without upgrading we will also kill the Xvfb
    def kill_xvfb():
        # Close our display(s) first, so the server dying won't kill us.
        log.info("killing xvfb with pid %s" % xvfb_pid)
        import gtk  #@Reimport
        for display in gtk.gdk.display_manager_get().list_displays():
            display.close()
        os.kill(xvfb_pid, signal.SIGTERM)
    if xvfb_pid is not None and not opts.use_display and not shadowing:
        _cleanups.append(kill_xvfb)

    if os.name=="posix" and not proxying:
        def reaper_quit():
            if opts.exit_with_children:
                log.info("all children have exited and --exit-with-children was specified, exiting")
                gobject.idle_add(app.clean_quit)
        child_reaper = ChildReaper(reaper_quit)
        if not upgrading and not shadowing and opts.pulseaudio and len(opts.pulseaudio_command)>0:
            start_pulseaudio(child_reaper, opts.pulseaudio_command)
        if opts.exit_with_children:
            assert opts.start_child, "exit-with-children was specified but start-child is missing!"
        if opts.start_child:
            assert os.name=="posix", "start-child cannot be used on %s" % os.name
            start_children(child_reaper, opts.start_child, (opts.fake_xinerama and not shadowing))

    try:
        e = app.run()
    except KeyboardInterrupt:
        e = 0
    except Exception, e:
        log.error("server error", exc_info=True)
        e = -128
    if e>0:
        # Upgrading/exiting, so leave X server running
        if kill_xvfb in _cleanups:
            _cleanups.remove(kill_xvfb)
        from xpra.server.server_core import ServerCore
        if e==ServerCore.EXITING_CODE:
            log.info("exiting: not cleaning up Xvfb")
        else:
            log.info("upgrading: not cleaning up Xvfb or socket")
            # don't delete the new socket (not ours)
            _cleanups.remove(cleanup_socket)
        log.info("cleanups=%s", _cleanups)
        e = 0
    return e
