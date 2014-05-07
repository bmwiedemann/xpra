# This file is part of Xpra.
# Copyright (C) 2010-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

%define version 0.13.0
%{!?python_sitelib: %define python_sitelib %(%{__python} -c "from distutils.sysconfig import get_python_lib; print get_python_lib()")}
%if 0%{?build_no} == 0
%define build_no 0
%endif
%define include_egg 1
%define old_xdg 0

%define requires_lz4 python-lz4
%define requires_fakexinerama libfakeXinerama
%define avcodec_build_args %{nil}
%define webp_build_args --with-webp
%define server_build_args --with-server
%define opencl_build_args --with-csc_opencl
#if building a generic rpm: exclude anything that requires cython modules:
%if 0%{?generic}
%define webp_build_args --without-webp
%define server_build_args --without-server
%define no_video 1
%define no_sound 1
%define no_pulseaudio 1
%endif

%if 0%{?static_video_libs}
%define static_vpx 1
%define static_x264 1
%define static_ffmpeg 1
%endif


#leave it to auto-detect by default:
%define dummy %{nil}
#python and gtk bits:
%define requires_python_gtk ,pygtk2, python-imaging, dbus-python
#Vfb (Xvfb or Xdummy):
%define requires_xorg , xorg-x11-server-utils, xorg-x11-server-Xvfb, xorg-x11-xauth, libXfont
#OpenGL bits:
%define requires_opengl %{nil}
#Anything extra (distro specific):
%define requires_extra %{nil}
%define requires_vpx , libvpx
%define requires_x264 , libx264
%define requires_webp , libwebp
%define requires_sound , gstreamer, gstreamer-plugins-base, gstreamer-plugins-good, gstreamer-plugins-ugly, gstreamer-python, pulseaudio, pulseaudio-utils
%define xim gtk2-immodule-xim

# distro-specific creative land of wonderness
%if %{defined Fedora}
%define requires_x264 , x264-libs
%define requires_xorg , xorg-x11-server-utils, xorg-x11-drv-dummy, xorg-x11-drv-void, xorg-x11-xauth
%define requires_opengl , PyOpenGL, pygtkglext, python-numeric, numpy
%endif

%if 0%{?el7}
#version shipped is good enough for dynamic linking:
%define static_vpx 0
%define requires_vpx libvpx
%define requires_x264 %{nil}
%define requires_webp libwebp
%define webp_build_args --with-webp
%define requires_sound %{nil}
#do not disable sound support, but do not declare deps for it either
#(so it can be installed if desired):
%define no_sound 0
%define requires_sound %{nil}
#OpenGL packages are not available yet:
#%define requires_opengl , PyOpenGL, pygtkglext
%define requires_opengl %{nil}
#7.x has dummy support
%define dummy --with-Xdummy
%define requires_xorg , xorg-x11-server-utils, xorg-x11-drv-dummy, xorg-x11-drv-void, xorg-x11-xauth
%endif

%if 0%{?el6}
%define requires_vpx %{nil}
%define requires_x264 %{nil}
%define requires_webp %{nil}
%define webp_build_args --without-webp
%define requires_sound %{nil}
#do not disable sound support, but do not declare deps for it either
#(so it can be installed if desired):
%define no_sound 0
%define requires_sound %{nil}
#opengl is supported from 6.4 onwards:
%if %(egrep -q 'release 6.4|release 6.5|release 6.6|release 6.7|release 6.8|release 6.9' /etc/redhat-release && echo 1 || echo 0)
%define requires_opengl , PyOpenGL, pygtkglext
#6.4 also has Xdummy support, but detection fails because of console ownership issues..
#so override and set it for 6.4 and later
%define dummy --with-Xdummy
%define requires_xorg , xorg-x11-server-utils, xorg-x11-drv-dummy, xorg-x11-drv-void, xorg-x11-xauth
%endif
%endif

%if 0%{?el5}
%define requires_lz4 %{nil}
%define requires_vpx %{nil}
%define requires_x264 %{nil}
%define requires_webp %{nil}
%define webp_build_args --without-webp
%define opencl_build_args --without-csc_opencl
%define requires_sound %{nil}
%define xim %{nil}
%define no_sound 1
%define no_pulseaudio 1
%define no_strict 1
%define old_xdg 1
#uuidgen is in e2fsprogs! (no we don't do any fs stuff)
%define requires_extra , e2fsprogs, python-ctypes
%define include_egg 0
%endif


%define ffmpeg_build_args %{nil}
%define vpx_build_args %{nil}
%define x264_build_args %{nil}
%if 0%{?no_video}
%define ffmpeg_build_args --without-dec_avcodec --without-dec_avcodec2 --without-csc_swscale
%define x264_build_args --without-x264
%define vpx_build_args --without-vpx
%else
%if 0%{?static_ffmpeg}
%define ffmpeg_build_args --without-dec_avcodec --with-dec_avcodec2 --with-avcodec2_static --with-csc_swscale --with-swscale_static
%endif
%if 0%{?static_vpx}
%define vpx_build_args --with-vpx --with-vpx_static
%endif
%if 0%{?static_x264}
%define x264_build_args --with-enc_x264 --with-x264_static
%endif
%endif


#remove dependency on webp for now since it leaks memory:
%define requires_webp %{nil}
%if 0%{?no_sound}
%define requires_sound %{nil}
%endif


Summary: Xpra gives you "persistent remote applications" for X.
Vendor: http://xpra.org/
Name: xpra
Version: %{version}
Release: %{build_no}%{dist}
License: GPL
Requires: %{requires_python_gtk} %{requires_xorg} %{requires_extra} %{requires_vpx} %{requires_x264} %{requires_webp} %{requires_opengl} %{requires_sound} %{requires_lz4} %{requires_fakexinerama}
Group: Networking
Packager: Antoine Martin <antoine@devloop.org.uk>
URL: http://xpra.org/
Source: xpra-%{version}.tar.gz
BuildRoot: %{_tmppath}/%{name}-%{version}-root
%if %{defined fedora}
BuildRequires: python, setuptool
BuildRequires: ffmpeg-devel
BuildRequires: libvpx-devel
BuildRequires: x264-devel
BuildRequires: pkgconfig
BuildRequires: Cython
BuildRequires: pygtk2-devel
BuildRequires: pygobject2-devel
BuildRequires: libXtst-devel
BuildRequires: libXfixes-devel
BuildRequires: libXcomposite-devel
BuildRequires: libXdamage-devel
BuildRequires: libXrandr-devel
%endif
BuildRequires: desktop-file-utils
Requires(post): desktop-file-utils
Requires(postun): desktop-file-utils

### Patches ###
Patch8: old-libav.patch
Patch9: old-libav-pixfmtconsts.patch
Patch10: old-libav-no0RGB.patch
Patch12: old-xdg-desktop.patch


%description
Xpra gives you "persistent remote applications" for X. That is, unlike normal X applications, applications run with xpra are "persistent" -- you can run them remotely, and they don't die if your connection does. You can detach them, and reattach them later -- even from another computer -- with no loss of state. And unlike VNC or RDP, xpra is for remote applications, not remote desktops -- individual applications show up as individual windows on your screen, managed by your window manager. They're not trapped in a box.

So basically it's screen for remote X apps.



%changelog
* Sat May 03 2014 Antoine Martin <antoine@devloop.org.uk> 0.13.0-1
- Python3 / GTK3 client support
- NVENC module included in binary builds
- support for enhanced dummy driver with DPI option
- better build system with features auto-detection
- removed unsupported CUDA csc module
- improved buffer support
- improved automatic encoding selection
- support running MS Windows installer under wine
- support for window opacity forwarding
- support for webp encoding via native codec, python-webm or Pillow

* Sat May 03 2014 Antoine Martin <antoine@devloop.org.uk> 0.12.5-1
- fix error when clients supply invalid screen dimensions
- fix MS Windows build without ffmpeg
- fix cairo backing alternative
- fix keyboard and sound test tools initialization and cleanup
- fix gcc version test used for enabling sanitizer build options
- fix exception handling in client when called from the launcher
- fix libav dependencies for Debian and Ubuntu builds 

* Wed Apr 23 2014 Antoine Martin <antoine@devloop.org.uk> 0.12.4-1
- fix xpra shadow subcommand
- fix xpra shadow keyboard mapping support for non-posix clients
- avoid Xorg dummy warning in log

* Wed Apr 09 2014 Antoine Martin <antoine@devloop.org.uk> 0.12.3-1
- fix mispostioned windows
- fix quickly disappearing windows (often menus)
- fix server errors when closing windows
- fix NVENC server initialization crash with driver version mismatch
- fix rare invalid memory read with XShm
- fix webp decoder leak
- fix memory leak on client disconnection
- fix focus errors if windows disappear
- fix mmap errors on window close
- fix incorrect x264 encoder speed reported via "xpra info"
- fix potential use of mmap as an invalid fallback for video encoding
- fix logging errors in debug mode
- fix timer expired warning

* Sun Mar 30 2014 Antoine Martin <antoine@devloop.org.uk> 0.12.2-1
- fix switching to RGB encoding via client tray
- fix remote server start via SSH
- fix workspace change detection causing slow screen updates

* Thu Mar 27 2014 Antoine Martin <antoine@devloop.org.uk> 0.12.1-1
- fix 32-bit server timestamps
- fix client PNG handling on installations without PIL / Pillow

* Sun Mar 23 2014 Antoine Martin <antoine@devloop.org.uk> 0.12.0-1
- NVENC support for YUV444 mode, support for automatic bitrate tuning
- NVENC and CUDA load balancing for multiple cards
- proxy encoding: ability to encode on proxy server
- fix fullscreen on multiple monitors via fakeXinerama
- OpenGL rendering improvements (for transparent windows, etc)
- support window grabs (drop down menus, etc)
- support specifying the SSH port number more easily
- enabled TCP_NODELAY socket option by default (lower latency)
- add ability to easily select video encoders and csc modules
- add local unix domain socket support to proxy server instances
- add "xpra control" commands to control encoding speed and quality
- improved handling of window resizing
- improved compatibility with command line tools (xdotool, wmctrl)
- ensure windows on other workspaces do not waste bandwidth
- ensure iconified windows do not waste bandwidth
- ensure maximized and fullscreen windows are prioritised
- ensure we reset xsettings when client disconnects
- better bandwidth utilization of jittery connections
- faster network code (larger receive buffers)
- better automatic encoding selection for smaller regions
- improved command line options (add ability to enable options which are disabled in the config file)
- trimmed all the ugly PyOpenGL warnings on startup
- much improved logging and debugging tools
- make it easier to distinguish xpra windows from local windows (border command line option)
- improved build system: smaller and more correct build output (much smaller OSX images)
- automatically stop remote shadow servers when client disconnects

* Tue Mar 18 2014 Antoine Martin <antoine@devloop.org.uk> 0.11.6-1
- correct fix for system tray forwarding

* Tue Mar 18 2014 Antoine Martin <antoine@devloop.org.uk> 0.11.5-1
- fix "xpra info" with bencoder
- ensure we re-sanitize window size hints when they change
- workaround applications with nonsensical size hints (ie: handbrake)
- fix 32-bit painting with GTK pixbuf loader (when PIL is not installed or disabled)
- fix system tray forwarding geometry issues
- fix workspace restore
- fix compilation warning
- remove spurious cursor warnings

* Sat Mar 01 2014 Antoine Martin <antoine@devloop.org.uk> 0.11.4-1
- fix NVENC GPU memory leak
- fix video compatibility with ancient clients
- fix vpx decoding in ffmpeg decoders
- fix transparent system tray image with RGB encoding
- fix client crashes with system tray forwarding
- fix webp codec loader error handler

* Fri Feb 14 2014 Antoine Martin <antoine@devloop.org.uk> 0.11.3-1
- fix compatibility with ancient versions of GTK
- fix crashes with malformed socket names
- fix server builds without client modules
- honour mdns flag set in config file
- blacklist VMware OpenGL driver which causes client crashes
- ensure all "control" subcommands run in UI thread

* Wed Jan 29 2014 Antoine Martin <antoine@devloop.org.uk> 0.11.2-1
- fix Cython 0.20 compatibility
- fix OpenGL pixel upload alignment code
- fix xpra command line help page tokens
- fix compatibility with old versions of the python glib library

* Fri Jan 24 2014 Antoine Martin <antoine@devloop.org.uk> 0.11.1-1
- fix compatibility with old/unsupported servers
- fix shadow mode
- fix paint issue with transparent tooltips on OSX and MS Windows
- fix pixel format typo in OpenGL logging

* Mon Jan 20 2014 Antoine Martin <antoine@devloop.org.uk> 0.11.0-1
- NVENC hardware h264 encoding acceleration
- OpenCL and CUDA colourspace conversion acceleration
- proxy server mode for serving multiple sessions through one port
- support for sharing a TCP port with a web server
- server control command for modifying settings at runtime
- server exit command, which leaves Xvfb running
- publish session via mDNS
- OSX client two way clipboard support
- support for transparency with OpenGL window rendering
- support for transparency with 8-bit PNG modes
- support for more authentication mechanisms
- support remote shadow start via ssh
- support faster lz4 compression
- faster bencoder, rewritten in Cython
- builtin fallback colourspace conversion module
- real time frame latency graphs
- improved system tray forwarding support and native integration
- removed most of the Cython/C code duplication
- stricter and safer value parsing
- more detailed status information via UI and "xpra info"
- experimental HTML5 client
- drop non xpra clients with a more friendly response

* Tue Jan 14 2014 Antoine Martin <antoine@devloop.org.uk> 0.10.12-1
- fix missing auto-refresh with lossy colourspace conversion
- fix spurious warning from Nvidia OpenGL driver
- fix OpenGL client crash with some drivers (ie: VirtualBox)
- fix crash in bencoder caused by empty data to encode
- fix ffmpeg2 h264 decoding (ie: Fedora 20+)
- big warnings about webp leaking memory
- generated debuginfo RPMs

* Tue Jan 07 2014 Antoine Martin <antoine@devloop.org.uk> 0.10.11-1
- fix popup windows focus issue
- fix "xpra upgrade" subcommand
- fix server backtrace in error handler
- restore server target information in tray tooltip
- fix bencoder error with no-windows switch (missing encoding)
- add support for RGBX pixel format required by some clients
- avoid ffmpeg "data is not aligned" warning on client

* Wed Dec 04 2013 Antoine Martin <antoine@devloop.org.uk> 0.10.10-1
- fix focus regression
- fix MS Windows clipboard copy including null byte
- fix h264 decoding with old versions of avcodec
- fix potential invalid read past the end of the buffer
- fix static vpx build arguments
- fix RGB modes exposed for transparent windows
- fix crash on clipboard loops: detect and disable clipboard
- support for ffmpeg version 2.x
- support for video encoding of windows bigger than 4k
- support video encoders that re-start the stream
- fix crash in decoding error path
- forward compatibility with namespace changes
- forward compatibility with the new generic encoding names

* Tue Nov 05 2013 Antoine Martin <antoine@devloop.org.uk> 0.10.9-1
- fix h264 decoding of padded images
- fix plain RGB encoding with very old clients
- fix "xpra info" error when old clients are connected
- remove warning when "help" is specified as encoding

* Tue Oct 22 2013 Antoine Martin <antoine@devloop.org.uk> 0.10.8-1
- fix misapplied patch breaking all windows with transparency

* Tue Oct 22 2013 Antoine Martin <antoine@devloop.org.uk> 0.10.7-1
- fix client crash on Linux with AMD cards and fglrx driver
- fix missing WM_CLASS on X11 clients
- fix "xpra info" on shadow servers
- add usable 1366x768 dummy resolution

* Tue Oct 15 2013 Antoine Martin <antoine@devloop.org.uk> 0.10.6-1
- fix window titles reverting to "unknown host"
- fix tray forwarding bug causing client disconnections
- replace previous rencode fix with warning

* Thu Oct 10 2013 Antoine Martin <antoine@devloop.org.uk> 0.10.5-1
- fix client time out when the initial connection fails
- fix shadow mode
- fix connection failures when some system information is missing
- fix client disconnection requests
- fix encryption cipher error messages
- fix client errors when some features are disabled
- fix potential rencode bug with unhandled data types
- error out if the client requests authentication and none is available

* Tue Sep 10 2013 Antoine Martin <antoine@devloop.org.uk> 0.10.4-2
- fix modifier key handling (was more noticeable with MS Windows clients)
- fix auto-refresh

* Fri Sep 06 2013 Antoine Martin <antoine@devloop.org.uk> 0.10.3-2
- fix transient windows with no parent
- fix metadata updates handling (maximize, etc)

* Thu Aug 29 2013 Antoine Martin <antoine@devloop.org.uk> 0.10.2-2
- fix connection error with unicode user name
- fix vpx compilation warning
- fix python 2.4 compatibility
- fix handling of scaling attribute via environment override
- build fix: ensure all builds include source information


* Tue Aug 20 2013 Antoine Martin <antoine@devloop.org.uk> 0.10.1-1
- fix avcodec buffer pointer errors on some 32-bit Linux
- fix invalid time convertion
- fix OpenGL scaling with fractions
- compilation fix for some newer versions of libav
- honour scaling at high quality settings
- add ability to disable transparency via environment variable
- silence PyOpenGL warnings we can do nothing about
- fix CentOS 6.3 packaging dependencies

* Tue Aug 13 2013 Antoine Martin <antoine@devloop.org.uk> 0.10.0-3
- performance: X11 shared memory (XShm) pixels transfers
- performance: zero-copy window pixels to picture encoders
- performance: zero copy decoded pixels to window (but not with OpenGL..)
- performance: multi-threaded x264 encoding and decoding
- support for speed tuning (latency vs bandwidth) with more encodings (png, jpeg, rgb)
- support for grayscale and palette based png encoding
- support for window and tray transparency
- support webp lossless
- support x264's "ultrafast" preset
- support forwarding of group-leader application window information
- prevent slow encoding from creating backlogs
- OpenGL accelerated client rendering enabled by default wherever supported
- register as a generic URL handler
- fullscreen toggle support
- stricter Cython code
- better handling of sound buffering and overruns
- experimental support for a Qt based client
- support for different window layouts with custom widgets
- don't try to synchronize with clipboards that do not exist (for shadow servers mostly)
- refactoring: move features and components to sub-modules
- refactoring: split X11 bindings from pure gtk code
- refactoring: codecs split encoding and decoding side
- refactoring: move more common code to utility classes
- refactoring: remove direct dependency on gobject in many places
- refactoring: platform code better separated
- refactoring: move wimpiggy inside xpra, delete parti
- export and expose more version information (x264/vpx/webp/PIL, OpenGL..)
- export compiler information with build (Cython, C compiler, etc)
- export much more debugging information about system state and statistics
- simplify non-UI subcommands and their packets, also use rencode ("xpra info", "xpra version", etc)

* Mon Jul 29 2013 Antoine Martin <antoine@devloop.org.uk> 0.9.8-1
- fix client workarea size change detection (again)
- fix crashes handling info requests
- fix server hangs due to sound cleanup deadlock
- use lockless window video decoder cleanup (much faster)
- speedup server startup when no XAUTHORITY file exists yet

* Tue Jul 16 2013 Antoine Martin <antoine@devloop.org.uk> 0.9.7-1
- fix error in sound cleanup code
- fix network threads accounting
- fix missing window icons
- fix client availibility of remote session start feature

* Sun Jun 30 2013 Antoine Martin <antoine@devloop.org.uk> 0.9.6-1
- fix lost clicks on some popup menus (mostly with MS Windows clients)
- fix client workarea size change detection
- fix reading of unique "machine-id" on posix
- fix window reference leak for windows we fail to manage
- fix compatibility with pillow (PIL fork)
- fix session-info window graphs jumping (smoother motion)
- fix webp loading code for non-Linux posix systems
- fix window group-leader attribute setting
- fix man page indentation
- fix variable test vs use (correctness only)

* Thu Jun 06 2013 Antoine Martin <antoine@devloop.org.uk> 0.9.5-1
- fix auto-refresh: don't refresh unnecessarily
- fix wrong initial timeout when ssh takes a long time to connect
- fix client monitor/resolution size change detection
- fix attributes reported to clients when encoding overrides are used
- Gentoo ebuild uses virtual to allow one to choose pillow or PIL

* Mon May 27 2013 Antoine Martin <antoine@devloop.org.uk> 0.9.4-1
- revert cursor scaling fix which broke other applications
- fix auto refresh mis-firing
- fix type (atom) of the X11 visual property we expose

* Mon May 20 2013 Antoine Martin <antoine@devloop.org.uk> 0.9.3-1
- fix clipboard for *nix clients
- fix selection timestamp parsing
- fix crash due to logging code location
- fix pixel area request dimensions for lossless edges
- fix advertized tray visual property
- fix cursors are too small with some applications
- fix crash when low level debug code is enabled
- reset cursors when disabling cursor forwarding
- workaround invalid window size hints

* Mon May 13 2013 Antoine Martin <antoine@devloop.org.uk> 0.9.2-1
- fix double error when loading build information (missing about dialog)
- fix and simplify build "clean" subcommand
- fix OpenGL rendering alignment for padded rowstrides case
- fix potential double error when tray initialization fails
- fix window static properties usage

* Wed May 08 2013 Antoine Martin <antoine@devloop.org.uk> 0.9.1-1
- honour initial client window's requested position
- fix for hidden appindicator
- fix string formatting error in non-cython fallback math code
- fix error if ping packets fail from the start
- fix for windows without a valid window-type (ie: shadows)
- fix OpenGL missing required feature detection (and add debug)
- add required CentOS RPM libXfont dependency
- tag our /etc configuration files in RPM spec file

* Thu Apr 25 2013 Antoine Martin <antoine@devloop.org.uk> 0.9.0-1
- fix focus problems with old Xvfb display servers
- fix RPM SELinux labelling of static codec builds (CentOS)
- fix CentOS 5.x compatibility
- fix Python 2.4 and 2.5 compatibility (many)
- fix failed server upgrades killing the virtual display
- fix screenshot command with "OR" windows
- fix support "OR" windows that move and resize
- IPv6 server support
- support for many more audio codecs: flac, opus, wavpack, wav, speex
- support starting remote sessions with "xpra start"
- support for Xdummy with CentOS 6.4 onwards
- add --log-file command line option
- add clipboard regex string filtering
- add clipboard transfer in progress animation via system tray
- detect broken/slow connections and temporarily grey out windows
- reduce regular packet header sizes using numeric lookup tables
- allow more options in xpra config and launcher files
- safer test for windows to ignore (window IDs starts at 1 again)
- expose more version and statistical data via xpra info
- improved OpenGL client rendering (still disabled by default)
- upgrade to rencode 1.0.2

* Thu Mar 07 2013 Antoine Martin <antoine@devloop.org.uk> 0.8.8-1
- fix server deadlock on dead connections
- fix compatibility with older versions of Python
- fix sound capture script usage via command line
- fix screen number preserve code
- fix error in logs in shadow mode

* Wed Feb 27 2013 Antoine Martin <antoine@devloop.org.uk> 0.8.7-1
- fix x264 crash with older versions of libav
- fix 32-bit builds breakage introduce by python2.4 fix in 0.8.6
- fix missing sound forwarding when using the GUI launcher
- fix microphone forwarding errors
- fix client window properties store
- fix first workspace not preserved and other workspace issues

* Fri Feb 22 2013 Antoine Martin <antoine@devloop.org.uk> 0.8.6-1
- fix python2.4 compatibility in icon grabbing code
- fix exit message location

* Sun Feb 17 2013 Antoine Martin <antoine@devloop.org.uk> 0.8.5-1
- fix server crash with transient windows

* Wed Feb 13 2013 Antoine Martin <antoine@devloop.org.uk> 0.8.4-1
- fix hello packet encoding bug
- fix colours in launcher and session-info windows

* Tue Feb 12 2013 Antoine Martin <antoine@devloop.org.uk> 0.8.3-1
- Python 2.4 compatiblity fixes (CentOS 5.x)
- fix static builds of vpx and x264

* Sun Feb 10 2013 Antoine Martin <antoine@devloop.org.uk> 0.8.2-1
- fix libav uninitialized structure crash
- fix warning on installations without sound libraries
- fix warning when pulseaudio utils are not installed
- fix delta compression race
- fix the return of some ghost windows
- stop pulseaudio on exit, warn if it fails to start
- re-enable system tray forwarding
- remove spurious "too many receivers" warnings

* Mon Feb 04 2013 Antoine Martin <antoine@devloop.org.uk> 0.8.1-1
- fix server daemonize on some platforms
- fix server SSH support on platforms with old versions of glib
- fix "xpra upgrade" closing applications
- fix detection of almost-lossless frames with x264
- fix starting of a duplicate pulseaudio server on upgrade
- fix compatibility with older versions of pulseaudio (pactl)
- fix session-info window when a tray is being forwarded
- remove warning on builds with limited encoding support
- disable tray forwarding by default as it causes problems with some apps
- rename "Quality" to "Min Quality" in tray menu
- fix rpm packaging: remove unusable modules

* Thu Jan 31 2013 Antoine Martin <antoine@devloop.org.uk> 0.8.0-9
- fix modal windows support
- fix default mouse cursor: now uses the client's default cursor
- fix short lived windows: avoid doing unnecessary work, avoid re-registering handlers
- fix limit the number of raw packets per client to prevent DoS via memory exhaustion
- fix authentication: ensure salt is per connection
- fix for ubuntu global application menus
- fix proxy handling of deadly signals
- fix pixel queue size calculations used for performance tuning decisions
- edge resistance for colourspace conversion level changes to prevent yoyo effect
- more aggressive picture quality tuning
- better CPU utilization
- new command line options and tray menu to trade latency for bandwidth
- x264 disable unecessary I-frames and avoid IDR frames
- performance and latency optimizations in critical sections
- avoid server loops: prevent the client from connecting to itself
- group windows according to the remote application they belong to
- sound forwarding (initial code, high latency)
- faster and more reliable client and server exit (from signal or otherwise)
- "xpra shadow" mode to clone an existing X11 display (compositors not supported yet)
- support for delta pixels mode (most useful for shadow mode)
- avoid warnings and X11 errors with the screenshot command
- better mouse cursor support: send cursors by name so their size matches the client's settings
- mitigate bandwidth eating cursor change storms: introduce simple cursor update batching
- support system tray icon forwarding (limited)
- preserve window workspace
- AES packet encryption for TCP mode (without key secure exchange for now)
- launcher entry box for username in SSH mode
- launcher improvements: highlight the password field if needed, prevent warnings, etc
- better window manager specification compatibility (for broken applications or toolkits)
- use lossless encoders more aggressively when possible
- new x264 tuning options: profiles to use and thresholds
- better detection of dead server sockets: retry and remove them if needed
- improved session information dialog and graphs
- more detailed hierarchical per-window details via "xpra info"
- send window icons in dedicated compressed packet (smaller new-window packets, faster)
- detect overly large main packets
- partial/initial Java/AWT keyboard support


* Mon Oct 08 2012 Antoine Martin <antoine@devloop.org.uk> 0.7.0-1
- fix "AltGr" key handling with MS Windows clients (and others)
- fix crash with x264 encoding
- fix crash with fast disappearing tooltip windows
- avoid storing password in a file when using the launcher (except on MS Windows)
- many latency fixes and improvements: lower latency, better line congestion handling, etc
- lower client latency: decompress pictures in a dedicated thread (including rgb24+zlib)
- better launcher command feedback
- better automatic compression heuristics
- support for Xdummy on platforms with only a suid binary installed
- support for 'webp' lossy picture encoding (better and faster than jpeg)
- support fixed picture quality with x264, webp and jpeg (via command line and tray menu)
- support for multiple "start-child" options in config files or command line
- more reliable auto-refresh
- performance optimizations: caching results, avoid unnecessary video encoder re-initialization
- faster re-connection (skip keyboard re-configuration)
- better isolation of the virtual display process and child processes
- show performance statistics graphs on session info dialog (click to save)
- start with compression enabled, even for initial packet
- show more version and client information in logs and via "xpra info"
- client launcher improvements: prevent logging conflict, add version info
- large source layout cleanup, compilation warnings fixed

* Fri Oct 05 2012 Antoine Martin <antoine@devloop.org.uk> 0.6.4-1
- fix bencoder to properly handle dicts with non-string keys
- fix swscale bug with windows that are too small by switch encoding
- fix locking of video encoder resizing leading to missing video frames
- fix crash with compression turned off: fix unicode encoding
- fix lack of locking sometimes causing errors with "xpra info"
- fix password file handling: exceptions and ignore carriage returns
- prevent races during setup and cleanup of network connections
- take shortcut if there is nothing to send

* Thu Sep 27 2012 Antoine Martin <antoine@devloop.org.uk> 0.6.3-1
- fix memory leak in server after client disconnection
- fix launcher: clear socket timeout once connected and add missing options
- fix potential bug in network code (prevent disconnection)
- enable auto-refresh by default since we now use a lossy encoder by default

* Tue Sep 25 2012 Antoine Martin <antoine@devloop.org.uk> 0.6.2-1
- fix missing key frames with x264/vpx: always reset the video encoder when we skip some frames (forces a new key frame)
- fix server crash on invalid keycodes (zero or negative)
- fix latency: isolate per-window latency statistics from each other
- fix latency: ensure we never record zero or even negative decode time
- fix refresh: server error was causing refresh requests to be ignored
- fix window options handling: using it for more than one value would fail
- fix video encoder/windows dimensions mismatch causing missing key frames
- fix damage options merge code (options were being squashed)
- ensure that small lossless regions do not cancel the auto-refresh timer
- restore protocol main packet compression and single chunk sending
- drop unnecessary OpenGL dependencies from some deb/rpm packages

* Fri Sep 14 2012 Antoine Martin <antoine@devloop.org.uk> 0.6.1-1
- fix compress clipboard data (previous fix was ineffectual)

* Sat Sep 08 2012 Antoine Martin <antoine@devloop.org.uk> 0.6.0-1
- fix launcher: don't block the UI whilst connecting, and use a lower timeout, fix icon lookup on *nix
- fix clipboard contents too big (was causing connection drops): try to compress them and just drop them if they are still too big
- x264 or vpx are now the default encodings (if available)
- compress rgb24 pixel data with zlib from the damage thread (rather than later in the network layer)
- better build environment detection
- experimental multi-user support (see --enable-sharing)
- better, more accurate "xpra info" statistics (per encoding, etc)
- tidy up main source directory
- simplify video encoders/decoders setup and cleanup code
- remove 'nogil' switch (as 'nogil' is much faster)
- test all socket types with automated tests

* Sat Sep 08 2012 Antoine Martin <antoine@devloop.org.uk> 0.5.4-1
- fix man page typo
- fix non bash login shell compatibility
- fix xpra screenshot argument parsing error handling
- fix video encoding mismatch when switching encoding
- fix ssh mode on OpenBSD

* Wed Sep 05 2012 Antoine Martin <antoine@devloop.org.uk> 0.5.3-1
- zlib compatibility fix: use chunked decompression when supported (newer versions)

* Wed Aug 29 2012 Antoine Martin <antoine@devloop.org.uk> 0.5.2-1
- fix xpra launcher icon lookup on *nix
- fix big clipboard packets causing disconnection: just drop them instead
- fix zlib compression in raw packet mode: ensure we always flush the buffer for each chunk
- force disconnection after irrecoverable network parsing error
- fix window refresh: do not skip all windows after a hidden one!

* Mon Aug 27 2012 Antoine Martin <antoine@devloop.org.uk> 0.5.1-6
- fix xpra_launcher
- build against rpmfusion repository, with build fix for Fedora 16

* Sat Aug 25 2012 Antoine Martin <antoine@devloop.org.uk> 0.5.1-1
- fix DPI issue with Xdummy: set virtual screen to 96dpi by default
- avoid looping forever doing maths on 'infinity' value
- fix incomplete cloning of attributes causing default values to be used for batch configuration
- damage data queue batch factor was being calculated but not used
- ensure we update the data we use for calculations (was always using zero value)
- ensure "send_bell" is initialized before use
- add missing path string in warning message
- fix test code compatibility with older xpra versions
- statistics shown for 'damage_packet_queue_pixels' were incorrect

* Mon Aug 20 2012 Antoine Martin <antoine@devloop.org.uk> 0.5.0-1
- new packet encoder written in C (much faster and data is now smaller too)
- read provided /etc/xpra/xpra.conf and user's own ~/.xpra/xpra.conf
- support Xdummy out of the box on platforms with recent enough versions of Xorg (and not installed suid)
- pass dpi to server and allow clients to specify dpi on the command line
- fix xsettings endianness problems
- fix clipboard tokens sent twice on start
- new command line options and UI to disable notifications forwarding, cursors and bell
- x264: adapt colourspace conversion, encoding speed and picture quality according to link and encoding/decoding performance
- automatically change video encoding: handle small region updates (ie: blinking cursor or spinner) without doing a full video frame refresh
- fairer window batching calculations, better performance over low latency links and bandwidth constrained links
- lower tcp socket connection timeout (10 seconds)
- better compression of cursor data
- log date and time with messages, better log messages (ie: "Ignoring ClientMessage..")
- send more client and server version information (python, gtk, etc)
- build cleanups: let distutils clean take care of removing all generated .c files
- code cleanups: move all win32 specific headers to win32 tree, fix vpx compilation warnings, whitespace, etc
- removed old "--no-randr" option
- drop compatibility with versions older than 0.3: we now assume the "raw_packets" feature is supported

* Mon Jul 23 2012 Antoine Martin <antoine@devloop.org.uk> 0.4.0-1
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

* Tue Jul 10 2012 Antoine Martin <antoine@devloop.org.uk> 0.3.3-1
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

* Wed Jun 06 2012 Antoine Martin <antoine@devloop.org.uk> 0.3.2-1
- fix missing 'a' key using OS X clients
- fix debian packaging for xpra_launcher
- fix unicode decoding problems in window title
- fix latency issue

* Tue May 29 2012 Antoine Martin <antoine@devloop.org.uk> 0.3.1-1
- fix DoS in network connections setup code
- fix for non-ascii characters in source file
- log remote IP or socket address
- more graceful disconnection of invalid clients
- updates to the man page and xpra command help page
- support running the automated tests against older versions
- "xpra info" to report the number of clients connected
- use xpra's own icon for its own windows (about and info dialogs)

* Sun May 20 2012 Antoine Martin <antoine@devloop.org.uk> 0.3.0-1
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

* Fri Apr 20 2012 Antoine Martin <antoine@devloop.org.uk> 0.2.0-1
- x264 and vpx video encoding support
- gtk3 and python 3 partial support (client only - no keyboard support)
- detect missing X11 server extensions and exit with error
- X11 vfb servers no longer listens on a TCP port
- clipboard fixes for Qt/KDE applications
- option for clients not to supply any keyboard mapping data (the server will no longer complain)
- show more system version information in session information dialog
- hide window decorations for openoffice splash screen (workaround)

* Wed Mar 21 2012 Antoine Martin <antoine@devloop.org.uk> 0.1.0-1
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

* Wed Feb 08 2012 Antoine Martin <antoine@devloop.org.uk> 0.0.7.36-1
- fix clipboard bug which was causing Java applications to crash
- ensure we always properly disconnect previous client when new connection is accepted
- avoid warnings with Java applications, focus errors, etc

* Wed Feb 01 2012 Antoine Martin <antoine@devloop.org.uk> 0.0.7.35-1
- ssh password input fix
- ability to take screenshots ("xpra screenshot")
- report server version ("xpra version")
- slave windows (drop down menus, etc) now move with their parent window
- show more session statistics: damage regions per second
- posix clients no longer interfere with the GTK/X11 main loop
- ignore missing properties when they are changed, and report correct source of the problem
- code style cleanups and improvements

* Thu Jan 19 2012 Antoine Martin <antoine@devloop.org.uk> 0.0.7.34-1
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

* Fri Jan 13 2012 Antoine Martin <antoine@devloop.org.uk> 0.0.7.33-1
- readonly command line option
- correctly stop all network related threads on disconnection
- faster pixel data transfers for large areas
- fix auto-refresh jpeg quality
- fix potential exhaustion of mmap area
- fix potential race in packet compression setup code
- keyboard: better modifiers detection, synchronization of capslock and numlock
- keyboard: support all modifiers correctly with and without keyboard-sync option

* Wed Dec 28 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.32-1
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

* Mon Nov 28 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.31-1
- threaded server for much lower latency
- fast memory mapped transfers for local connections
- adaptive damage batching, fixes window refresh
- xpra "detach" command
- fixed system tray for Ubuntu clients
- fixed maximized windows on Ubuntu clients

* Tue Nov 01 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.30-1
- fix for update batching causing screen corruption
- fix AttributeError jpegquality: make PIL (aka python-imaging) truly optional
- fix for jitter compensation code being a little bit too trigger-happy

* Wed Oct 26 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.29-2
- fix partial packets on boundary causing connection to drop (properly this time)

* Tue Oct 25 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.29-1
- fix partial packets on boundary causing connection to drop
- improve disconnection diagnostic messages
- scale cursor down to the client's default size
- better handling of right click on system tray icon
- posix: detect when there is no DISPLAY and error out
- support ubuntu's appindicator (yet another system tray implementation)
- remove harmless warnings about missing properties on startup

* Tue Oct 18 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.28-2
- fix password mode - oops

* Tue Oct 18 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.28-1
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

* Thu Sep 22 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.27-1
- compatibility fix for python 2.4 (remove "with" statement)
- slow down updates from windows that refresh continuously

* Tue Sep 20 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.26-1
- minor changes to support the Android client (work in progress)
- allow keyboard shortcuts to be specified, default is meta+shift+F4 to quit (disconnects client)
- clear modifiers when applying new keymaps to prevent timeouts
- reduce context switching in the network read loop code
- try harder to close connections cleanly
- removed some unused code, fixed some old test code

* Wed Aug 31 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.25-1
- Use xmodmap to grab the exact keymap, this should ensure all keys are mapped correctly
- Reset modifiers whenever we gain or lose focus, or when the keymap changes

* Mon Aug 15 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.24-1
- Use raw keycodes whenever possible, should fix keymapping issues for all Unix-like clients
- Keyboard fixes for AltGr and special keys for non Unix-like clients

* Wed Jul 27 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.23-2
- More keymap fixes..

* Wed Jul 20 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.23-1
- Try to use setxkbmap before xkbcomp to setup the matching keyboard layout
- Handle keyval level (shifted keys) explicitly, should fix missing key mappings
- More generic option for setting window titles
- Exit if the server dies

* Thu Jun 02 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.22-1
- minor fixes: jpeg, man page, etc

* Fri May 20 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.21-1
- ability to bind to an existing display with --use-display
- --xvfb now specifies the full command used. The default is unchanged
- --auto-refresh-delay does automatic refresh of idle displays in a lossless fashion

* Wed May 04 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.20-1
- more reliable fix for keyboard mapping issues

* Mon Apr 25 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.19-1
- xrandr support when running against Xdummy, screen resizes on demand
- fixes for keyboard mapping issues: multiple keycodes for the same key

* Mon Apr 4 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.18-2
- Fix for older distros (like CentOS) with old versions of pycairo

* Mon Mar 28 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.18-1
- Fix jpeg compression on MS Windows
- Add ability to disable clipboard code
- Updated man page

* Wed Jan 19 2011 Antoine Martin <antoine@devloop.org.uk> 0.0.7.17-1
- Honour the pulseaudio flag on client

* Wed Aug 25 2010 Antoine Martin <antoine@devloop.org.uk> 0.0.7.16-1
- Merged upstream changes.

* Thu Jul 01 2010 Antoine Martin <antoine@devloop.org.uk> 0.0.7.15-1
- Add option to disable Pulseaudio forwarding as this can be a real network hog.
- Use logging rather than print statements.

* Tue May 04 2010 Antoine Martin <antoine@devloop.org.uk> 0.0.7.13-1
- Ignore minor version differences in the future (must bump to 0.0.8 to cause incompatibility error)

* Tue Apr 13 2010 Antoine Martin <antoine@devloop.org.uk> 0.0.7.12-1
- bump screen resolution

* Mon Jan 11 2010 Antoine Martin <antoine@devloop.org.uk> 0.0.7.11-1
- first rpm spec file

%prep
rm -rf $RPM_BUILD_DIR/xpra-%{version}
zcat $RPM_SOURCE_DIR/xpra-%{version}.tar.gz | tar -xvf -
cd xpra-%{version}
%if 0%{?no_strict}
(sed -e -i s'/strict_ENABLED = True/strict_ENABLED = False/g' setup.py)
(echo "setup.py" >> %{S:ignored_changed_files.txt})
%endif
%if 0%{?old_libav}
%patch8 -p1
(echo "xpra/codecs/dec_avcodec/decoder.pyx" > %{S:ignored_changed_files.txt})
%endif
%if 0%{?old_libav}%{?old_pixfmt}
%patch9 -p1
%patch10 -p1
(echo "xpra/codecs/csc_swscale/colorspace_converter.pyx" >> %{S:ignored_changed_files.txt})
(echo "xpra/codecs/csc_swscale/constants.txt" >> %{S:ignored_changed_files.txt})
(echo "xpra/codecs/dec_avcodec/decoder.pyx" >> %{S:ignored_changed_files.txt})
(echo "xpra/codecs/dec_avcodec/constants.txt" >> %{S:ignored_changed_files.txt})
%endif
%if 0%{?no_pulseaudio}
(sed -e -i s'/sound_ENABLED = True/sound_ENABLED = False/g' setup.py)
(echo "setup.py" >> %{S:ignored_changed_files.txt})
(echo "etc/*/xpra.conf" >> %{S:ignored_changed_files.txt})
%endif
%if 0%{?old_xdg}
%patch12 -p1
(echo "xdg/*.desktop" >> %{S:ignored_changed_files.txt})
%endif

%debug_package

%build
cd xpra-%{version}
rm -rf build install
CFLAGS=-O2 python setup.py build %{ffmpeg_build_args} %{vpx_build_args} %{x264_build_args} %{opencl_build_args} %{webp_build_args} %{server_build_args} %{avcodec_build_args}

%install
rm -rf $RPM_BUILD_ROOT
cd xpra-%{version}
%{__python} setup.py install -O1 %{dummy} --prefix /usr --skip-build --root %{buildroot}

#we should pass arguments to setup.py but rpm macros make this too difficult
#so we delete after installation (ugly but this works)
rm -fr ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/platform/win32
rm -fr ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/platform/darwin

%if 0%{?opengl}
#included by default
%else
rm -fr ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/client/gl
%endif

%if 0%{?generic}
# remove anything relying on dynamic libraries (not suitable for a generic RPM):
rm -f ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/gtk_common/gdk_atoms.so
rm -f ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/x11/gtk_x11/*.so
rm -f ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/x11/bindings/*.so
rm -f ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/net/rencode/_rencode.so
rm -f ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/codecs/*/*.so
rm -f ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/server/stats/cymaths.so
rm -fr ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/codecs/csc_swscale
rm -fr ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/codecs/enc_x264
rm -fr ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/codecs/dec_avcodec*
rm -fr ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/codecs/vpx
rm -fr ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/codecs/webm

%else
#not a generic RPM
%ifarch x86_64
mv -f "${RPM_BUILD_ROOT}/usr/lib64" "${RPM_BUILD_ROOT}/usr/lib"
%endif
#exclude list for non-generic RPMs:
%if 0%{?no_video}
rm -fr ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/codecs/vpx
rm -fr ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/codecs/enc_x264
rm -fr ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/codecs/dec_avcodec*
%endif
%if 0%{?no_webp}
rm -fr ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/codecs/webm
%endif
%if 0%{?no_sound}
rm -fr ${RPM_BUILD_ROOT}/usr/lib/python2.*/site-packages/xpra/sound
%endif
%endif


%clean
rm -rf $RPM_BUILD_ROOT

%files
%defattr(-,root,root)
%{_bindir}/xpra*
%{python_sitelib}/xpra
%if %{include_egg}
%{python_sitelib}/xpra-*.egg-info
%endif
/usr/share/xpra
/usr/share/man/man1/xpra*
/usr/share/applications/xpra_launcher.desktop
/usr/share/applications/xpra.desktop
/usr/share/icons/xpra.png
%dir %{_sysconfdir}/xpra
%config(noreplace) %{_sysconfdir}/xpra/xorg.conf
%config(noreplace) %{_sysconfdir}/xpra/xpra.conf

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/xpra_launcher.desktop
desktop-file-validate %{buildroot}%{_datadir}/applications/xpra.desktop


%post
%if 0%{?static_ffmpeg}
chcon -t texrel_shlib_t %{python_sitelib}/xpra/codecs/csc_swscale/colorspace_converter.so
chcon -t texrel_shlib_t %{python_sitelib}/xpra/codecs/dec_avcodec*/decoder.so
%endif
%if 0%{?static_vpx}
chcon -t texrel_shlib_t %{python_sitelib}/xpra/codecs/vpx/encoder.so
chcon -t texrel_shlib_t %{python_sitelib}/xpra/codecs/vpx/decoder.so
%endif
%if 0%{?static_x264}
chcon -t texrel_shlib_t %{python_sitelib}/xpra/codecs/enc_x264/encoder.so
%endif
%if %{defined Fedora}
update-desktop-database &> /dev/null || :
touch --no-create %{_datadir}/icons/hicolor &>/dev/null || :
%endif


%postun
%if %{defined Fedora}
update-desktop-database &> /dev/null || :
if [ $1 -eq 0 ] ; then
    /bin/touch --no-create %{_datadir}/icons/hicolor &>/dev/null
    /usr/bin/gtk-update-icon-cache %{_datadir}/icons/hicolor &>/dev/null || :
fi
%endif


%posttrans
/usr/bin/gtk-update-icon-cache %{_datadir}/icons/hicolor &>/dev/null || :


###
### eof
###
