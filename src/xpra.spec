# This file is part of Parti.
# Copyright (C) 2010-2012 Antoine Martin <antoine@devloop.org.uk>
# Parti is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

%define version 0.5.0
%{!?python_sitelib: %define python_sitelib %(%{__python} -c "from distutils.sysconfig import get_python_lib; print get_python_lib()")}
%if 0%{?build_no} == 0
%define build_no 0
%endif
%define is_suse %(test -e /etc/SuSE-release && echo 1 || echo 0)
%define include_egg 1

%define requires pygtk2, xorg-x11-server-utils, xorg-x11-server-Xvfb, python-imaging, dbus-python
%define requires_opengl %{nil}
%define requires_extra %{nil}
%define requires_vpx , libvpx
%define requires_x264 , libx264

# distro-specific creative land of wonderness
%if %{defined fedora}
%define requires_x264 , x264-libs
%if 0%{?opengl}
%define requires_opengl , PyOpenGL, pygtkglext, python-numeric
%endif
%endif

%if 0%{?el6}
%define requires_vpx %{nil}
%define requires_x264 %{nil}
%if 0%{?opengl}
%define requires_opengl , PyOpenGL, pygtkglext
%endif
%if 0%{?static_video_libs}
%define static_x264 1
%define static_vpx 1
%endif
%endif

%if 0%{?el5}
%define requires_vpx %{nil}
%define requires_x264 %{nil}
%define requires_extra , python-uuid, python-ctypes
%define include_egg 0
%if 0%{?static_video_libs}
%define static_x264 1
%define static_vpx 1
%endif
%endif

%if %is_suse
%define requires python-gtk, xorg-x11-server, xorg-x11-server-extra, libpng12-0, dbus-1-python
%define requires_extra %{nil}
%endif


Summary: Xpra gives you "persistent remote applications" for X.
Vendor: http://xpra.org/
Name: xpra
Version: %{version}
Release: %{build_no}%{dist}
License: GPL
Requires: %{requires} %{requires_extra} %{requires_vpx} %{requires_x264} %{requires_opengl}
Group: Networking
Packager: Antoine Martin <antoine@nagafix.co.uk>
URL: http://xpra.org/
Source: parti-all-%{version}.tar.gz
BuildRoot: %{_tmppath}/%{name}-%{version}-root
%if %{defined fedora}
BuildRequires: python, setuptool
%endif

### Patches ###
# if building a generic rpm (without .so) which works as client only
Patch0: disable-posix-server.patch
Patch1: disable-x264.patch
Patch2: disable-vpx.patch
Patch3: use-static-x264lib.patch
Patch4: use-static-vpxlib.patch


%description
Xpra gives you "persistent remote applications" for X. That is, unlike normal X applications, applications run with xpra are "persistent" -- you can run them remotely, and they don't die if your connection does. You can detach them, and reattach them later -- even from another computer -- with no loss of state. And unlike VNC or RDP, xpra is for remote applications, not remote desktops -- individual applications show up as individual windows on your screen, managed by your window manager. They're not trapped in a box.

So basically it's screen for remote X apps.


%changelog
* Mon Jul 23 2012 Antoine Martin <antoine@nagafix.co.uk> 0.5.0-1
- TODO

* Mon Jul 23 2012 Antoine Martin <antoine@nagafix.co.uk> 0.4.0-1
- fix client application resizing its own window
- fix window dimensions hints not applied
- fix memleak in x264 cleanup code 
- fix xpra command exit code (more complete fix)
- fix latency bottleneck in processing of damage requests
- fix free uninitialized pointers in video decoder initialization error codepath
- fix x264 related crash when resizing windows to one pixel width or height
- fix accounting of client decode time: ignore figure in case of decoding error
- fix subversion build information detection on MS Windows
- fix some binary packages which were missing some menu icons
- restore keyboard compatiblity code for MS Windows and OSX clients
- use padded buffers to prevent colourspace conversion from reading random memory
- release Python's GIL during vpx and x264 compression and colourspace conversion
- better UI launcher: UI improvements, detect encodings, fix standalone/win32 usage, minimize window once the client has started
- "xpra stop" disconnects all potential clients cleanly before exiting
- use memory aligned buffer for better performance with x264
- avoid vpx/x264 overhead for very small damage regions
- detect dead connection with ping packets: disconnect if echo not received
- force a full refresh when the encoding is changed
- more dynamic framerate performance adjustments, based on more metrics
- new menu option to toggle keyboard sync at runtime
- vpx/x264 runtime imports: detect broken installations and warn, but ignore when the codec is simply not installed
- enable environment debugging for damage batching via "XPRA_DEBUG_LATENCY" env variable
- simplify build by using setup file to generate all constants
- text clients now ignore packets they are not meant to handle
- removed compression menu since the default is good enough
- "xpra info" reports all build version information
- report server pygtk/gtk versions and show them on session info dialog and "xpra info"
- ignore dependency issues during sdist/clean phase of build
- record more statistics (mostly latency) in test reports
- documentation and logging added to code, moved test code out of main packages
- include distribution name in RPM version/filename
- CentOS 6 RPMs now depends on libvpx rather than a statically linked library
- CentOS static ffmpeg build with memalign for better performance
- no longer bundle parti window manager

* Tue Jul 10 2012 Antoine Martin <antoine@nagafix.co.uk> 0.3.3-1
- do not try to free the empty x264/vpx buffers after a decompression failure
- fix xpra command exit code (zero) when no error occurred
- fix Xvfb deadlock on shutdown
- fix wrongly removing unix domain socket on startup failure
- fix wrongly killing Xvfb on startup failure
- fix race in network code and meta data packets
- ensure clients use raw_packets if the server supports it (fixes 'gibberish' compressed packet errors)
- fix screen resolution reported by the server
- fix maximum packet size check wrongly dropping valid connections
- honour the --no-tray command line argument
- detect Xvfb startup failures and avoid taking over other displays
- don't record invalid placeholder value for "server latency"
- fix missing "damage-sequence" packet for sequence zero
- fix window focus with some Tk based application (ie: git gui)
- prevent large clipboard packets from causing the connection to drop
- fix for connection with older clients and server without raw packet support and rgb24 encoding
- high latency fix: reduce batch delay when screen updates slow down
- non-US keyboard layout fix
- correctly calculate min_batch_delay shown in statistics via "xpra info"
- require x264-libs for x264 support on Fedora

* Mon Jun 06 2012 Antoine Martin <antoine@nagafix.co.uk> 0.3.2-1
- fix missing 'a' key using OS X clients
- fix debian packaging for xpra_launcher
- fix unicode decoding problems in window title
- fix latency issue

* Tue May 29 2012 Antoine Martin <antoine@nagafix.co.uk> 0.3.1-1
- fix DoS in network connections setup code
- fix for non-ascii characters in source file
- log remote IP or socket address
- more graceful disconnection of invalid clients
- updates to the man page and xpra command help page
- support running the automated tests against older versions
- "xpra info" to report the number of clients connected
- use xpra's own icon for its own windows (about and info dialogs)

* Sun May 20 2012 Antoine Martin <antoine@nagafix.co.uk> 0.3.0-1
- zero-copy network code, per packet compression
- fix race causing DoS in threaded network protocol setup
- fix vpx encoder memory leak
- fix vpx/x264 decoding: recover from frame failures
- fix small per-window memory leak in server
- per-window update batching auto-tuning, which is fairer
- windows update batching now takes into account the number of pixels rather than just the number of regions to update
- support --socket-dir option over ssh
- IPv6 support using the syntax: ssh/::ffff:192.168.1.100/10 or tcp/::ffff:192.168.1.100/10000
- all commands now return a non-zero exit code in case of failure
- new "xpra info" command to report server statistics
- prettify some of the logging and error messages
- avoid doing most of the keyboard setup code when clients are in read-only mode
- automated regression and performance tests
- remove compatibility code for versions older than 0.1

* Fri Apr 20 2012 Antoine Martin <antoine@nagafix.co.uk> 0.2.0-1
- x264 and vpx video encoding support
- gtk3 and python 3 partial support (client only - no keyboard support)
- detect missing X11 server extensions and exit with error
- X11 vfb servers no longer listens on a TCP port
- clipboard fixes for Qt/KDE applications
- option for clients not to supply any keyboard mapping data (the server will no longer complain)
- show more system version information in session information dialog
- hide window decorations for openoffice splash screen (workaround)

* Wed Mar 21 2012 Antoine Martin <antoine@nagafix.co.uk> 0.1.0-1
- security: strict filtering of packet handlers until connection authenticated
- prevent DoS: limit number of concurrent connections attempting login (20)
- prevent DoS: limit initial packet size (memory exhaustion: 32KB)
- mmap: options to place sockets in /tmp and share mmap area across users via unix groups
- remove large amount of compatiblity code for older versions
- fix for Mac OS X clients sending hexadecimal keysyms
- fix for clipboard sharing and some applications (ie: Qt)
- notifications systems with dbus: re-connect if needed
- notifications: try not to interfere with existing notification services
- mmap: check for protected file access and ignore rather than error out (oops)
- clipboard: handle empty data rather than timing out
- spurious warnings: remove many harmless stacktraces/error messages
- detect and discard broken windows with invalid atoms, avoids vfb + xpra crash
- unpress keys all keys on start (if any)
- fix screen size check: also check vertical size is sufficient
- fix for invisible 0 by 0 windows: restore a minimum size
- fix for window dimensions causing enless resizing or missing window contents
- toggle cursors, bell and notifications by telling the server not to bother sending them, saves bandwidth
- build/deploy: don't modify file in source tree, generate it at build time only
- add missing GPL2 license file to show in about dialog
- Python 2.5: workarounds to restore support
- turn off compression over local connections (when mmap is enabled)
- clients can specify maximum refresh rate and screen update batching options

* Thu Feb 08 2012 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.36-1
- fix clipboard bug which was causing Java applications to crash
- ensure we always properly disconnect previous client when new connection is accepted
- avoid warnings with Java applications, focus errors, etc

* Wed Feb 01 2012 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.35-1
- ssh password input fix
- ability to take screenshots ("xpra screenshot")
- report server version ("xpra version")
- slave windows (drop down menus, etc) now move with their parent window
- show more session statistics: damage regions per second
- posix clients no longer interfere with the GTK/X11 main loop
- ignore missing properties when they are changed, and report correct source of the problem
- code style cleanups and improvements

* Thu Jan 19 2012 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.34-1
- security: restrict access to run-xpra script (chmod)
- security: cursor data sent to the client was too big (exposing server memory)
- fix thread leak - properly this time, SIGUSR1 now dumps all threads
- off-by-one keyboard mapping error could cause modifiers to be lost
- pure python/cython method for finding modifier mappings (faster and more reliable)
- retry socket read/write after temporary error EINTR
- avoid warnings when asked to refresh windows which are now hidden
- auto-refresh was using an incorrect window size
- logging formatting fixes (only shown with logging on)
- hide picture encoding menu when mmap in use (since it is then ignored)

* Fri Jan 13 2012 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.33-1
- readonly command line option
- correctly stop all network related threads on disconnection
- faster pixel data transfers for large areas
- fix auto-refresh jpeg quality
- fix potential exhaustion of mmap area
- fix potential race in packet compression setup code
- keyboard: better modifiers detection, synchronization of capslock and numlock
- keyboard: support all modifiers correctly with and without keyboard-sync option

* Thu Dec 28 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.32-1
- bug fix: disconnection could leave the server (and X11 server) in a broken state due to threaded UI calls
- bug fix: don't remove window focus when just any connection is lost, only when the real client goes away
- bug fix: initial windows should get focus (partial fix)
- support key repeat latency workaround without needing raw keycodes (OS X and MS Windows)
- command line switch to enable client side key repeat: "--no-keyboard-sync" (for high latency/jitter links)
- session info dialog: shows realtime connection and server details
- menu entry in system tray to raise all managed windows
- key mappings: try harder to unpress all keys before setting the new keymap
- key mappings: try to reset modifier keys as well as regular keys
- key mappings: apply keymap using Cython code rather than execing xmodmap
- key mappings: fire change callbacks only once when all the work is done
- use dbus for tray notifications if available, prefered to pynotify
- show full version information in about dialog

* Wed Nov 28 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.31-1
- threaded server for much lower latency
- fast memory mapped transfers for local connections
- adaptive damage batching, fixes window refresh
- xpra "detach" command
- fixed system tray for Ubuntu clients
- fixed maximized windows on Ubuntu clients

* Tue Nov 01 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.30-1
- fix for update batching causing screen corruption
- fix AttributeError jpegquality: make PIL (aka python-imaging) truly optional
- fix for jitter compensation code being a little bit too trigger-happy

* Wed Oct 26 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.29-2
- fix partial packets on boundary causing connection to drop (properly this time)

* Tue Oct 25 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.29-1
- fix partial packets on boundary causing connection to drop
- improve disconnection diagnostic messages
- scale cursor down to the client's default size
- better handling of right click on system tray icon
- posix: detect when there is no DISPLAY and error out
- support ubuntu's appindicator (yet another system tray implementation)
- remove harmless warnings about missing properties on startup

* Tue Oct 18 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.28-2
- fix password mode - oops

* Tue Oct 18 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.28-1
- much more efficient and backwards compatible network code, prevents a CPU bottleneck on the client
- forwarding of system notifications, system bell and custom cursors
- system tray menu to make it easier to change settings and disconnect
- automatically resize Xdummy to match the client's screen size whenever it changes
- PNG image compression support
- JPEG and PNG compression are now optional, only available if the Python Imaging Library is installed
- scale window icons before sending if they are too big
- fixed keyboard mapping for OSX and MS Windows clients
- compensate for line jitter causing keys to repeat
- fixed cython warnings, unused variables, etc

* Fri Sep 22 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.27-1
- compatibility fix for python 2.4 (remove "with" statement)
- slow down updates from windows that refresh continuously

* Wed Sep 20 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.26-1
- minor changes to support the Android client (work in progress)
- allow keyboard shortcuts to be specified, default is meta+shift+F4 to quit (disconnects client)
- clear modifiers when applying new keymaps to prevent timeouts
- reduce context switching in the network read loop code
- try harder to close connections cleanly
- removed some unused code, fixed some old test code

* Wed Aug 31 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.25-1
- Use xmodmap to grab the exact keymap, this should ensure all keys are mapped correctly
- Reset modifiers whenever we gain or lose focus, or when the keymap changes

* Mon Aug 15 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.24-1
- Use raw keycodes whenever possible, should fix keymapping issues for all Unix-like clients
- Keyboard fixes for AltGr and special keys for non Unix-like clients

* Fri Jul 27 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.23-2
- More keymap fixes..

* Wed Jul 20 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.23-1
- Try to use setxkbmap before xkbcomp to setup the matching keyboard layout
- Handle keyval level (shifted keys) explicitly, should fix missing key mappings
- More generic option for setting window titles
- Exit if the server dies

* Thu Jun 02 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.22-1
- minor fixes: jpeg, man page, etc

* Fri May 20 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.21-1
- ability to bind to an existing display with --use-display
- --xvfb now specifies the full command used. The default is unchanged
- --auto-refresh-delay does automatic refresh of idle displays in a lossless fashion

* Wed May 04 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.20-1
- more reliable fix for keyboard mapping issues

* Mon Apr 25 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.19-1
- xrandr support when running against Xdummy, screen resizes on demand
- fixes for keyboard mapping issues: multiple keycodes for the same key

* Mon Apr 4 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.18-2
- Fix for older distros (like CentOS) with old versions of pycairo

* Sat Mar 28 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.18-1
- Fix jpeg compression on MS Windows
- Add ability to disable clipboard code
- Updated man page

* Wed Jan 19 2011 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.17-1
- Honour the pulseaudio flag on client

* Thu Aug 25 2010 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.16-1
- Merged upstream changes.

* Thu Jul 01 2010 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.15-1
- Add option to disable Pulseaudio forwarding as this can be a real network hog.
- Use logging rather than print statements.

* Mon May 04 2010 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.13-1
- Ignore minor version differences in the future (must bump to 0.0.8 to cause incompatibility error)

* Tue Apr 13 2010 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.12-1
- bump screen resolution

* Sun Jan 11 2010 Antoine Martin <antoine@nagafix.co.uk> 0.0.7.11-1
- first rpm spec file

%prep
rm -rf $RPM_BUILD_DIR/parti-all-%{version}
zcat $RPM_SOURCE_DIR/parti-all-%{version}.tar.gz | tar -xvf -
cd parti-all-%{version}
%if %{defined generic_rpm}
%patch0 -p1
%endif
%if 0%{?no_video}
%patch1 -p1
%patch2 -p1
%endif
%if 0%{?static_x264}
%patch3 -p1
%endif
%if 0%{?static_vpx}
%patch4 -p1
%endif

%build
cd parti-all-%{version}
rm -rf build install
CFLAGS=-O2 python setup.py build

%install
rm -rf $RPM_BUILD_ROOT
cd parti-all-%{version}
%{__python} setup.py install -O1  --prefix /usr --skip-build --root %{buildroot}
%if %{defined generic_rpm}
# remove .so (not suitable for a generic RPM)
rm -f "${RPM_BUILD_ROOT}/usr/lib/python2.6/site-packages/wimpiggy/bindings.so"
rm -f "${RPM_BUILD_ROOT}/usr/lib/python2.6/site-packages/xpra/wait_for_x_server.so"
%else
%ifarch x86_64
mv -f "${RPM_BUILD_ROOT}/usr/lib64" "${RPM_BUILD_ROOT}/usr/lib"
%endif
%endif

%clean
rm -rf $RPM_BUILD_ROOT

%files
%defattr(-,root,root)
%{_bindir}/xpra
%{_bindir}/xpra_launcher
%{python_sitelib}/xpra
%{python_sitelib}/wimpiggy
%if %{include_egg}
%{python_sitelib}/parti_all-*.egg-info
%endif
/usr/share/xpra
/usr/share/wimpiggy
/usr/share/man/man1/xpra*
/usr/share/applications/xpra_launcher.desktop
/usr/share/icons/xpra.png

# we no longer ship parti in rpms:
%exclude %{_bindir}/parti
%exclude %{_bindir}/parti-repl
%exclude %{python_sitelib}/parti
%exclude /usr/share/parti
%exclude /usr/share/man/man1/parti.*

###
### eof
###
