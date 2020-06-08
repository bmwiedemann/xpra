#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2012 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2012-2019 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import sys
import logging

from xpra.util import envbool, envint, csv
from xpra.os_util import POSIX, OSX, bytestostr
from xpra.log import Logger, CaptureHandler
from xpra.client.gl.gl_drivers import WHITELIST, GREYLIST, VERSION_REQ, BLACKLIST, OpenGLFatalError

log = Logger("opengl")

required_extensions = ["GL_ARB_texture_rectangle", "GL_ARB_vertex_program"]


GL_ALPHA_SUPPORTED = envbool("XPRA_ALPHA", True)
DOUBLE_BUFFERED = envbool("XPRA_OPENGL_DOUBLE_BUFFERED", True)

CRASH = envbool("XPRA_OPENGL_FORCE_CRASH", False)
TIMEOUT = envint("XPRA_OPENGL_FORCE_TIMEOUT", 0)


#by default, we raise an ImportError as soon as we find something missing:
def raise_error(msg):
    raise ImportError(msg)
def raise_fatal_error(msg):
    raise OpenGLFatalError(msg)
gl_check_error = raise_error
gl_fatal_error = raise_fatal_error

_version_warning_shown = False
#support for memory views requires Python 2.7 and PyOpenGL 3.1
def is_pyopengl_memoryview_safe(pyopengl_version, accel_version) -> bool:
    if accel_version is not None and pyopengl_version!=accel_version:
        #mismatch is not safe!
        return False
    vsplit = pyopengl_version.split('.')
    if vsplit[:2]<['3','1']:
        #requires PyOpenGL >= 3.1, earlier versions will not work
        return False
    if vsplit[:2]>=['3','2']:
        #assume that newer versions are OK too
        return True
    #at this point, we know we have a 3.1.x version, but which one?
    if len(vsplit)<3:
        #not enough parts to know for sure, assume it's not supported
        return False
    micro = vsplit[2]
    #ie: '0', '1' or '0b2'
    if micro=='0':
        return True     #3.1.0 is OK
    if micro>='1':
        return True     #3.1.1 onwards should be too
    return False        #probably something like '0b2' which is broken


def check_functions(force_enable, *functions):
    missing = []
    available = []
    for x in functions:
        try:
            name = x.__name__
        except AttributeError:
            name = str(x)
        if not bool(x):
            missing.append(name)
        else:
            available.append(name)
    if missing:
        if not force_enable:
            raise_fatal_error("some required OpenGL functions are missing:\n%s" % csv(missing))
        log("some functions are missing: %s", csv(missing))
    else:
        log("All the required OpenGL functions are available: %s " % csv(available))

def get_max_texture_size() -> int:
    from OpenGL.GL import glGetInteger, GL_MAX_TEXTURE_SIZE
    texture_size = glGetInteger(GL_MAX_TEXTURE_SIZE)
    log("GL_MAX_TEXTURE_SIZE=%s", texture_size)
    #this one may be missing?
    rect_texture_size = texture_size
    try:
        from OpenGL.GL import GL_MAX_RECTANGLE_TEXTURE_SIZE
        rect_texture_size = glGetInteger(GL_MAX_RECTANGLE_TEXTURE_SIZE)
    except ImportError as e:
        log("OpenGL: %s", e)
        log("using GL_MAX_TEXTURE_SIZE=%s as default", texture_size)
    except Exception as e:
        log("failed to query GL_MAX_RECTANGLE_TEXTURE_SIZE: %s", e)
    else:
        log("Texture size GL_MAX_RECTANGLE_TEXTURE_SIZE=%s", rect_texture_size)
    return min(rect_texture_size, texture_size)


def check_PyOpenGL_support(force_enable) -> dict:
    props = {}
    def unsafe():
        props["safe"] = False
    try:
        if CRASH:
            import ctypes
            ctypes.string_at(0)
            raise Exception("should have crashed!")
        if TIMEOUT>0:
            import time
            time.sleep(TIMEOUT)
        #log redirection:
        def redirect_log(logger_name):
            logger = logging.getLogger(logger_name)
            assert logger is not None
            logger.saved_handlers = logger.handlers
            logger.saved_propagate = logger.propagate
            logger.handlers = [CaptureHandler()]
            logger.propagate = 0
            return logger
        fhlogger = redirect_log('OpenGL.formathandler')
        elogger = redirect_log('OpenGL.extensions')
        alogger = redirect_log('OpenGL.acceleratesupport')
        arlogger = redirect_log('OpenGL.arrays')
        clogger = redirect_log('OpenGL.converters')

        import OpenGL
        props["pyopengl"] = OpenGL.__version__
        from OpenGL.GL import GL_VERSION, GL_EXTENSIONS
        from OpenGL.GL import glGetString, glGetIntegerv
        gl_version_str = glGetString(GL_VERSION)
        if gl_version_str is None and not force_enable:
            raise_fatal_error("OpenGL version is missing - cannot continue")
            return props
        #b'4.6.0 NVIDIA 440.59' -> ['4', '6', '0 NVIDIA...']
        log("GL_VERSION=%s", bytestostr(gl_version_str))
        vparts = bytestostr(gl_version_str).split(" ", 1)[0].split(".")
        try:
            gl_major = int(vparts[0])
            gl_minor = int(vparts[1])
        except (IndexError, ValueError) as e:
            log("failed to parse gl version '%s': %s", bytestostr(gl_version_str), e)
            log(" assuming this is at least 1.1 to continue")
            unsafe()
        else:
            props["opengl"] = gl_major, gl_minor
            MIN_VERSION = (1,1)
            if (gl_major, gl_minor) < MIN_VERSION:
                if not force_enable:
                    raise_fatal_error("OpenGL output requires version %s or greater, not %s.%s" %
                                  (".".join([str(x) for x in MIN_VERSION]), gl_major, gl_minor))
                    return props
                unsafe()
            else:
                log("found valid OpenGL version: %s.%s", gl_major, gl_minor)

        from OpenGL import version as OpenGL_version
        pyopengl_version = OpenGL_version.__version__
        try:
            import OpenGL_accelerate            #@UnresolvedImport
            accel_version = OpenGL_accelerate.__version__
            props["accelerate"] = accel_version
            log("OpenGL_accelerate version %s", accel_version)
        except ImportError:
            log("OpenGL_accelerate not found")
            OpenGL_accelerate = None
            accel_version = None

        if accel_version is not None and pyopengl_version!=accel_version:
            global _version_warning_shown
            if not _version_warning_shown:
                log.warn("Warning: version mismatch between PyOpenGL and PyOpenGL-accelerate")
                log.warn(" %s vs %s", pyopengl_version, accel_version)
                log.warn(" this may cause crashes")
                _version_warning_shown = True
                gl_check_error("PyOpenGL vs accelerate version mismatch: %s vs %s" % (pyopengl_version, accel_version))
        vsplit = pyopengl_version.split('.')
        #we now require PyOpenGL 3.1 or later
        if vsplit[:3]<['3','1']:
            if not force_enable:
                raise_fatal_error("PyOpenGL version %s is too old and buggy" % pyopengl_version)
                return {}
            unsafe()
        props["zerocopy"] = bool(OpenGL_accelerate) and is_pyopengl_memoryview_safe(pyopengl_version, accel_version)

        try:
            extensions = glGetString(GL_EXTENSIONS).decode().split(" ")
            log("OpenGL extensions found: %s", csv(extensions))
            props["extensions"] = extensions
        except Exception:
            log("error querying extensions", exc_info=True)
            extensions = []
            if not force_enable:
                raise_fatal_error("OpenGL could not find the list of GL extensions -"+
                                  " does the graphics driver support OpenGL?")
            unsafe()

        from OpenGL.arrays.arraydatatype import ArrayDatatype
        try:
            log("found the following array handlers: %s", set(ArrayDatatype.getRegistry().values()))
        except Exception:
            pass

        from OpenGL.GL import GL_RENDERER, GL_VENDOR, GL_SHADING_LANGUAGE_VERSION
        def fixstring(v):
            try:
                return str(v).strip()
            except Exception:
                return str(v)
        for d,s,fatal in (("vendor",     GL_VENDOR,      True),
                          ("renderer",   GL_RENDERER,    True),
                          ("shading-language-version", GL_SHADING_LANGUAGE_VERSION, False)):
            try:
                v = glGetString(s)
                v = fixstring(v.decode())
                log("%s: %s", d, v)
            except Exception:
                if fatal and not force_enable:
                    gl_check_error("OpenGL property '%s' is missing" % d)
                else:
                    log("OpenGL property '%s' is missing", d)
                v = ""
            props[d] = v
        vendor = props["vendor"]
        version_req = VERSION_REQ.get(vendor)
        if version_req:
            req_maj, req_min = version_req
            if gl_major<req_maj or (gl_major==req_maj and gl_minor<req_min):
                if force_enable:
                    log.warn("Warning: '%s' OpenGL driver requires version %i.%i", vendor, req_maj, req_min)
                    log.warn(" version %i.%i was found", gl_major, gl_minor)
                    unsafe()
                else:
                    gl_check_error("OpenGL version %i.%i is too old, %i.%i is required for %s" % (
                        gl_major, gl_minor, req_maj, req_min, vendor))

        from OpenGL.GLU import gluGetString, GLU_VERSION, GLU_EXTENSIONS
        #maybe we can continue without?
        if not bool(gluGetString):
            raise_fatal_error("no OpenGL GLU support")
        for d,s in {"GLU.version": GLU_VERSION, "GLU.extensions":GLU_EXTENSIONS}.items():
            v = gluGetString(s)
            v = v.decode()
            log("%s: %s", d, v)
            props[d] = v

        def match_list(thelist, listname):
            for k,vlist in thelist.items():
                v = props.get(k)
                matches = [x for x in vlist if v.find(x)>=0]
                if matches:
                    log("%s '%s' found in %s: %s", k, v, listname, vlist)
                    return (k, v)
                log("%s '%s' not found in %s: %s", k, v, listname, vlist)
            return None
        blacklisted = match_list(BLACKLIST, "blacklist")
        greylisted = match_list(GREYLIST, "greylist")
        whitelisted = match_list(WHITELIST, "whitelist")
        if blacklisted:
            if whitelisted:
                log.info("%s '%s' enabled (found in both blacklist and whitelist)", *whitelisted)
            elif force_enable:
                log.warn("Warning: %s '%s' is blacklisted!", *blacklisted)
                log.warn(" force enabled by option")
            else:
                if force_enable:
                    log.warn("%s '%s' is blacklisted!" % (blacklisted))
                else:
                    raise_fatal_error("%s '%s' is blacklisted!" % (blacklisted))
        safe = bool(whitelisted) or not bool(blacklisted)
        if greylisted and not whitelisted:
            log.warn("Warning: %s '%s' is greylisted,", *greylisted)
            log.warn(" you may want to turn off OpenGL if you encounter bugs")
        if props.get("safe") is None:
            props["safe"] = safe

        #check for specific functions we need:
        from OpenGL.GL import (
            glActiveTexture, glTexSubImage2D, glTexCoord2i,
            glViewport, glMatrixMode, glLoadIdentity, glOrtho,
            glEnableClientState, glGenTextures, glDisable,
            glBindTexture, glPixelStorei, glEnable, glBegin, glFlush,
            glTexParameteri, glTexEnvi, glHint, glBlendFunc, glLineStipple,
            glTexImage2D,
            glMultiTexCoord2i,
            glVertex2i, glEnd,
            )
        check_functions(force_enable,
            glActiveTexture, glTexSubImage2D, glTexCoord2i,
            glViewport, glMatrixMode, glLoadIdentity, glOrtho,
            glEnableClientState, glGenTextures, glDisable,
            glBindTexture, glPixelStorei, glEnable, glBegin, glFlush,
            glTexParameteri, glTexEnvi, glHint, glBlendFunc, glLineStipple,
            glTexImage2D,
            glMultiTexCoord2i,
            glVertex2i, glEnd)
        #check for framebuffer functions we need:
        from OpenGL.GL.ARB.framebuffer_object import (
            GL_FRAMEBUFFER,
            GL_COLOR_ATTACHMENT0,
            glGenFramebuffers, glBindFramebuffer, glFramebufferTexture2D,
            )
        check_functions(force_enable,
            GL_FRAMEBUFFER,
            GL_COLOR_ATTACHMENT0,
            glGenFramebuffers, glBindFramebuffer, glFramebufferTexture2D,
            )

        glEnablei = None
        try:
            from OpenGL.GL import glEnablei
        except ImportError:
            pass
        if not bool(glEnablei):
            log.warn("OpenGL glEnablei is not available, disabling transparency")
            global GL_ALPHA_SUPPORTED
            GL_ALPHA_SUPPORTED = False
        props["transparency"] = GL_ALPHA_SUPPORTED

        missing_extensions = [ext for ext in required_extensions if ext not in extensions]
        if missing_extensions:
            if not force_enable:
                raise_fatal_error("OpenGL driver lacks support for extension: %s" % csv(missing_extensions))
            log("some extensions are missing: %s", csv(missing_extensions))
            unsafe()
        else:
            log("All required extensions are present: %s", required_extensions)

        #this allows us to do CSC via OpenGL:
        #see http://www.opengl.org/registry/specs/ARB/fragment_program.txt
        from OpenGL.GL.ARB.fragment_program import glInitFragmentProgramARB
        from OpenGL.GL.ARB.texture_rectangle import glInitTextureRectangleARB
        for name, fn in {
            "glInitFragmentProgramARB"  : glInitFragmentProgramARB,
            "glInitTextureRectangleARB" : glInitTextureRectangleARB,
            }.items():
            if not fn():
                if not force_enable:
                    raise_fatal_error("OpenGL output requires %s" % name)
                log("%s missing", name)
                unsafe()
            else:
                log("%s found", name)

        from OpenGL.GL.ARB.vertex_program import (
            glGenProgramsARB, glDeleteProgramsARB,
            glBindProgramARB, glProgramStringARB,
            )
        check_functions(force_enable,
                        glGenProgramsARB, glDeleteProgramsARB, glBindProgramARB, glProgramStringARB)

        texture_size_limit = get_max_texture_size()
        props["texture-size-limit"] = int(texture_size_limit)

        try:
            from OpenGL.GL import GL_MAX_VIEWPORT_DIMS
            v = glGetIntegerv(GL_MAX_VIEWPORT_DIMS)
            max_viewport_dims = int(v[0]), int(v[1])
            assert max_viewport_dims[0]>=texture_size_limit and max_viewport_dims[1]>=texture_size_limit
            log("GL_MAX_VIEWPORT_DIMS=%s", max_viewport_dims)
        except ImportError as e:
            log.error("Error querying max viewport dims: %s", e)
            max_viewport_dims = texture_size_limit, texture_size_limit
        props["max-viewport-dims"] = max_viewport_dims
        return props
    finally:
        for x in alogger.handlers[0].records:
            #strip default message prefix:
            msg = x.getMessage().replace("No OpenGL_accelerate module loaded: ", "")
            if msg=="No module named OpenGL_accelerate":
                msg = "missing accelerate module"
            if msg=="OpenGL_accelerate module loaded":
                log.info(msg)
            else:
                log.warn("PyOpenGL warning: %s", msg)

        #format handler messages:
        STRIP_LOG_MESSAGE = "Unable to load registered array format handler "
        missing_handlers = []
        for x in fhlogger.handlers[0].records:
            msg = x.getMessage()
            p = msg.find(STRIP_LOG_MESSAGE)
            if p<0:
                #unknown message, log it:
                log.info(msg)
                continue
            format_handler = msg[p+len(STRIP_LOG_MESSAGE):]
            p = format_handler.find(":")
            if p>0:
                format_handler = format_handler[:p]
                missing_handlers.append(format_handler)
        if missing_handlers:
            log.warn("PyOpenGL warning: missing array format handlers: %s", csv(missing_handlers))

        for x in elogger.handlers[0].records:
            msg = x.getMessage()
            #ignore extension messages:
            p = msg.startswith("GL Extension ") and msg.endswith("available")
            if not p:
                log.info(msg)

        missing_accelerators = []
        STRIP_AR_HEAD = "Unable to load"
        STRIP_AR_TAIL = "from OpenGL_accelerate"
        for x in arlogger.handlers[0].records+clogger.handlers[0].records:
            msg = x.getMessage()
            if msg.startswith(STRIP_AR_HEAD) and msg.endswith(STRIP_AR_TAIL):
                m = msg[len(STRIP_AR_HEAD):-len(STRIP_AR_TAIL)].strip()
                m = m.replace("accelerators", "").replace("accelerator", "").strip()
                missing_accelerators.append(m)
                continue
            elif msg.startswith("Using accelerated"):
                log(msg)
            else:
                log.info(msg)
        if missing_accelerators:
            log.info("OpenGL accelerate missing: %s", csv(missing_accelerators))

        def restore_logger(logger):
            logger.handlers = logger.saved_handlers
            logger.propagate = logger.saved_propagate
        restore_logger(fhlogger)
        restore_logger(elogger)
        restore_logger(alogger)
        restore_logger(arlogger)
        restore_logger(clogger)


def main():
    from xpra.platform import program_context
    from xpra.platform.gui import init as gui_init
    from xpra.util import print_nested_dict
    from xpra.log import enable_color
    with program_context("OpenGL-Check"):
        gui_init()
        enable_color()
        verbose = "-v" in sys.argv or "--verbose" in sys.argv
        if verbose:
            log.enable_debug()
        if POSIX and not OSX:
            from xpra.x11.gtk_x11.gdk_display_source import init_gdk_display_source
            init_gdk_display_source()
        force_enable = "-f" in sys.argv or "--force" in sys.argv
        from xpra.platform.gl_context import GLContext
        log("testing %s", GLContext)
        gl_context = GLContext()  #pylint: disable=not-callable
        log("GLContext=%s", gl_context)
        #replace ImportError with a log message:
        global gl_check_error, gl_fatal_error
        errors = []
        def log_error(msg):
            log.error("ERROR: %s", msg)
            errors.append(msg)
        gl_check_error = log_error
        gl_fatal_error = log_error
        try:
            props = gl_context.check_support(force_enable)
        except Exception as e:
            props = {}
            log("check_support", exc_info=True)
            errors.append(e)
        log.info("")
        if errors:
            log.info("OpenGL errors:")
            for e in errors:
                log.info("  %s", e)
        if props:
            log.info("")
            log.info("OpenGL properties:")
            print_nested_dict(props)
        return len(errors)


if __name__ == "__main__":
    sys.exit(main())
