# This file is part of Xpra.
# Copyright (C) 2013 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
# Copyright (C) 2012, 2013 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

#only works with gtk2:
from gtk import gdk
assert gdk
import gtk.gdkgl, gtk.gtkgl         #@UnresolvedImport
assert gtk.gdkgl is not None and gtk.gtkgl is not None
import gobject

from xpra.log import Logger, debug_if_env
log = Logger()
debug = debug_if_env(log, "XPRA_OPENGL_DEBUG")

from xpra.codecs.codec_constants import get_subsampling_divs
from xpra.client.gl.gl_check import get_DISPLAY_MODE
from xpra.client.gl.gl_colorspace_conversions import YUV2RGB_shader, RGBP2RGB_shader
from xpra.client.gtk2.window_backing import GTK2WindowBacking, fire_paint_callbacks
from OpenGL.GL import GL_PROJECTION, GL_MODELVIEW, \
    GL_UNPACK_ROW_LENGTH, GL_UNPACK_ALIGNMENT, \
    GL_TEXTURE_MAG_FILTER, GL_TEXTURE_MIN_FILTER, GL_NEAREST, \
    GL_UNSIGNED_BYTE, GL_LUMINANCE, GL_RGB, GL_LINEAR, \
    GL_TEXTURE0, GL_TEXTURE1, GL_TEXTURE2, GL_QUADS, GL_COLOR_BUFFER_BIT, \
    GL_DONT_CARE, GL_TRUE,\
    glActiveTexture, glTexSubImage2D, \
    glGetString, glViewport, glMatrixMode, glLoadIdentity, glOrtho, \
    glGenTextures, glDisable, \
    glBindTexture, glPixelStorei, glEnable, glBegin, glFlush, \
    glTexParameteri, \
    glTexImage2D, \
    glMultiTexCoord2i, \
    glTexCoord2i, glVertex2i, glEnd, \
    glClear, glClearColor
from OpenGL.GL.ARB.texture_rectangle import GL_TEXTURE_RECTANGLE_ARB
from OpenGL.GL.ARB.vertex_program import glGenProgramsARB, \
    glBindProgramARB, glProgramStringARB, GL_PROGRAM_ERROR_STRING_ARB, GL_PROGRAM_FORMAT_ASCII_ARB
from OpenGL.GL.ARB.fragment_program import GL_FRAGMENT_PROGRAM_ARB
from OpenGL.GL.ARB.framebuffer_object import GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, glGenFramebuffers, glBindFramebuffer, glFramebufferTexture2D
try:
    from OpenGL.GL.KHR.debug import GL_DEBUG_OUTPUT, GL_DEBUG_OUTPUT_SYNCHRONOUS, glDebugMessageControl, glDebugMessageCallback, glInitDebugKHR
except ImportError:
    log.warn("Unable to import GL_KHR_debug OpenGL extension. Debug output will be more limited.")
    GL_DEBUG_OUTPUT = None
try:
    from OpenGL.GL.GREMEDY.string_marker import glInitStringMarkerGREMEDY, glStringMarkerGREMEDY
    from OpenGL.GL.GREMEDY.frame_terminator import glInitFrameTerminatorGREMEDY, glFrameTerminatorGREMEDY
    from OpenGL.GL import GLDEBUGPROC #@UnresolvedImport
    def py_gl_debug_callback(source, error_type, error_id, severity, length, message, param):
        log.error("src %x type %x id %x severity %x length %d message %s", source, error_type, error_id, severity, length, message)
    gl_debug_callback = GLDEBUGPROC(py_gl_debug_callback)
except ImportError:
    # This is normal- GREMEDY_string_marker is only available with OpenGL debuggers
    gl_debug_callback = None
    glInitStringMarkerGREMEDY = None
    glStringMarkerGREMEDY = None
    glInitFrameTerminatorGREMEDY = None
    glFrameTerminatorGREMEDY = None
from ctypes import c_char_p


# Texture number assignment
#  1 = Y plane
#  2 = U plane
#  3 = V plane
#  4 = RGB updates
#  5 = FBO texture (guaranteed up-to-date window contents)
TEX_Y = 0
TEX_U = 1
TEX_V = 2
TEX_RGB = 3
TEX_FBO = 4

"""
This is the gtk2 + OpenGL version.
The logic is as follows:

We create an OpenGL framebuffer object, which will be always up-to-date with the latest windows contents.
This framebuffer object is updated with YUV painting and RGB painting. It is presented on screen by drawing a
textured quad when requested, that is: after each YUV or RGB painting operation, and upon receiving an expose event.
The use of a intermediate framebuffer object is the only way to guarantee that the client keeps an always fully up-to-date
window image, which is critical because of backbuffer content losses upon buffer swaps or offscreen window movement.
"""
class GLPixmapBacking(GTK2WindowBacking):

    def __init__(self, wid, w, h, has_alpha):
        GTK2WindowBacking.__init__(self, wid, w, h, has_alpha)
        display_mode = get_DISPLAY_MODE()
        try:
            self.glconfig = gtk.gdkgl.Config(mode=display_mode)
        except gtk.gdkgl.NoMatches:
            display_mode &= ~gtk.gdkgl.MODE_DOUBLE
            self.glconfig = gtk.gdkgl.Config(mode=display_mode)
        self.glarea = gtk.gtkgl.DrawingArea(self.glconfig)
        #restoring missed masks:
        self.glarea.set_events(self.glarea.get_events() | gdk.POINTER_MOTION_MASK | gdk.POINTER_MOTION_HINT_MASK)
        self.glarea.show()
        self.glarea.connect("expose_event", self.gl_expose_event)
        self.textures = None # OpenGL texture IDs
        self.shaders = None
        self.pixel_format = None
        self.size = 0, 0
        self.texture_size = 0, 0
        self.gl_setup = False
        self.paint_screen = False
        self._video_use_swscale = False
        self.draw_needs_refresh = False
        self.offscreen_fbo = None

    def __str__(self):
        return "GLPixmapBacking(%s, %s)" % (self.size, self.pixel_format)

    def init(self, w, h):
        #re-init gl projection with new dimensions
        #(see gl_init)
        if self.size!=(w, h):
            self.gl_setup = False
            self.size = w, h

    def gl_marker(self, msg):
        if not bool(glStringMarkerGREMEDY):
            return
        c_string = c_char_p(msg)
        glStringMarkerGREMEDY(0, c_string)

    def gl_frame_terminator(self):
        # Mark the end of the frame
        # This makes the debug output more readable especially when doing single-buffered rendering
        if not bool(glFrameTerminatorGREMEDY):
            return
        glFrameTerminatorGREMEDY()

    def gl_init(self):
        drawable = self.gl_begin()
        w, h = self.size
        debug("GL Pixmap backing size: %d x %d, drawable=%s", w, h, drawable)
        if not drawable:
            return  None
        if not self.gl_setup:
            #ensure python knows which scope we're talking about:
            global glInitStringMarkerGREMEDY, glStringMarkerGREMEDY
            global glInitFrameTerminatorGREMEDY, glFrameTerminatorGREMEDY
            # Ask GL to send us all debug messages
            if GL_DEBUG_OUTPUT and gl_debug_callback and glInitDebugKHR() == True:
                glEnable(GL_DEBUG_OUTPUT)
                glEnable(GL_DEBUG_OUTPUT_SYNCHRONOUS)
                glDebugMessageCallback(gl_debug_callback, None)
                glDebugMessageControl(GL_DONT_CARE, GL_DONT_CARE, GL_DONT_CARE, 0, None, GL_TRUE)
            # Initialize string_marker GL debugging extension if available
            if glInitStringMarkerGREMEDY and glInitStringMarkerGREMEDY() == True:
                log.info("Extension GL_GREMEDY_string_marker available. Will output detailed information about each frame.")
            else:
                # General case - running without debugger, extension not available
                glStringMarkerGREMEDY = None
                #don't bother trying again for another window:
                glInitStringMarkerGREMEDY = None
            # Initialize frame_terminator GL debugging extension if available
            if glInitFrameTerminatorGREMEDY and glInitFrameTerminatorGREMEDY() == True:
                log.info("Enabling GL frame terminator debugging.")
            else:
                glFrameTerminatorGREMEDY = None
                #don't bother trying again for another window:
                glInitFrameTerminatorGREMEDY = None



            self.gl_marker("Initializing GL context for window size %d x %d" % (w, h))
            # Initialize viewport and matrices for 2D rendering
            glViewport(0, 0, w, h)
            glMatrixMode(GL_PROJECTION)
            glLoadIdentity()
            glOrtho(0.0, w, h, 0.0, -1.0, 1.0)
            glMatrixMode(GL_MODELVIEW)
            #TODO glEnableClientState(GL_VERTEX_ARRAY)
            #TODO glEnableClientState(GL_TEXTURE_COORD_ARRAY)

            # Clear to white
            glClearColor(1.0, 1.0, 1.0, 1.0)

            # Default state is good for YUV painting:
            #  - fragment program enabled
            #  - YUV fragment program bound
            #  - render to offscreen FBO
            glEnable(GL_FRAGMENT_PROGRAM_ARB)
            if self.textures is None:
                self.textures = glGenTextures(5)
                debug("textures for wid=%s of size %s : %s", self.wid, self.size, self.textures)
            if self.offscreen_fbo is None:
                self.offscreen_fbo = glGenFramebuffers(1)

            # Define empty FBO texture and set rendering to FBO
            glBindTexture(GL_TEXTURE_RECTANGLE_ARB, self.textures[TEX_FBO])
            glTexImage2D(GL_TEXTURE_RECTANGLE_ARB, 0, GL_RGB, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, None)
            glBindFramebuffer(GL_FRAMEBUFFER, self.offscreen_fbo)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_RECTANGLE_ARB, self.textures[TEX_FBO], 0)
            glClear(GL_COLOR_BUFFER_BIT)

            # Create and assign fragment programs
            if not self.shaders:
                self.shaders = [ 1, 2 ]
                glGenProgramsARB(2, self.shaders)
                for progid, progstr in ((0, YUV2RGB_shader), (1, RGBP2RGB_shader)):
                    glBindProgramARB(GL_FRAGMENT_PROGRAM_ARB, self.shaders[progid])
                    glProgramStringARB(GL_FRAGMENT_PROGRAM_ARB, GL_PROGRAM_FORMAT_ASCII_ARB, len(progstr), progstr)
                    err = glGetString(GL_PROGRAM_ERROR_STRING_ARB)
                    if err:
                        #FIXME: maybe we should do something else here?
                        log.error(err)

            # Bind program 0 for YUV painting by default
            glBindProgramARB(GL_FRAGMENT_PROGRAM_ARB, self.shaders[0])

            self.gl_setup = True
        return drawable

    def close(self):
        GTK2WindowBacking.close(self)
        self.glarea = None
        self.glconfig = None

    def gl_begin(self):
        if self.glarea is None:
            return None     #closed already
        drawable = self.glarea.get_gl_drawable()
        context = self.glarea.get_gl_context()
        if drawable is None or context is None:
            log.error("OpenGL error: no drawable or context!")
            return None
        if not drawable.gl_begin(context):
            log.error("OpenGL error: cannot create rendering context!")
            return None
        return drawable

    def set_rgb24_paint_state(self):
        # Set GL state for RGB24 painting:
        #    no fragment program
        #    only tex unit #0 active
        self.gl_marker("Switching to RGB24 paint state")
        glDisable(GL_FRAGMENT_PROGRAM_ARB);
        for texture in (GL_TEXTURE1, GL_TEXTURE2):
            glActiveTexture(texture)
            glDisable(GL_TEXTURE_RECTANGLE_ARB)
        glActiveTexture(GL_TEXTURE0);
        glEnable(GL_TEXTURE_RECTANGLE_ARB)

    def unset_rgb24_paint_state(self):
        # Reset state to our default
        self.gl_marker("Switching back to YUV paint state")
        glEnable(GL_FRAGMENT_PROGRAM_ARB)

    def set_rgbP_paint_state(self):
        # Set GL state for planar RGB:
        #   change fragment program
        glBindProgramARB(GL_FRAGMENT_PROGRAM_ARB, self.shaders[1])

    def unset_rgbP_paint_state(self):
        # Reset state to our default (YUV painting):
        #   change fragment program
        glBindProgramARB(GL_FRAGMENT_PROGRAM_ARB, self.shaders[0])

    def present_fbo(self, drawable):
        debug("present_fbo(%s)", drawable)
        self.gl_marker("Presenting FBO on screen")
        assert drawable
        # Change state to target screen instead of our FBO
        glBindFramebuffer(GL_FRAMEBUFFER, 0)

        # Draw FBO texture on screen
        self.set_rgb24_paint_state()

        glBindTexture(GL_TEXTURE_RECTANGLE_ARB, self.textures[TEX_FBO])

        w, h = self.size
        glBegin(GL_QUADS)
        glTexCoord2i(0, h)
        glVertex2i(0, 0)
        glTexCoord2i(0, 0)
        glVertex2i(0, h)
        glTexCoord2i(w, 0)
        glVertex2i(w, h)
        glTexCoord2i(w, h)
        glVertex2i(w, 0)
        glEnd()

        # Show the backbuffer on screen
        if drawable.is_double_buffered():
            debug("GL swapping buffers now")
            drawable.swap_buffers()
            # Clear the new backbuffer to illustrate that its contents are undefined
            glClear(GL_COLOR_BUFFER_BIT)
        else:
            glFlush()
        self.gl_frame_terminator()

        self.unset_rgb24_paint_state()
        glBindFramebuffer(GL_FRAMEBUFFER, self.offscreen_fbo)

    def gl_expose_event(self, glarea, event):
        debug("gl_expose_event(%s, %s)", glarea, event)
        drawable = self.gl_init()
        if not drawable:
            return
        try:
            self.present_fbo(drawable)
        finally:
            drawable.gl_end()

    def _do_paint_rgb24(self, img_data, x, y, width, height, rowstride, options, callbacks):
        debug("_do_paint_rgb24(x=%d, y=%d, width=%d, height=%d rowstride=%d)", x, y, width, height, rowstride)
        drawable = self.gl_init()
        if not drawable:
            debug("OpenGL cannot paint rgb24, drawable is not set")
            return False

        try:
            self.set_rgb24_paint_state()
    
            # Compute alignment and row length
            row_length = 0
            alignment = 1
            for a in [2, 4, 8]:
                # Check if we are a-aligned - ! (var & 0x1) means 2-aligned or better, 0x3 - 4-aligned and so on
                if (rowstride & a-1) == 0:
                    alignment = a
            # If number of extra bytes is greater than the alignment value,
            # then we also have to set row_length
            # Otherwise it remains at 0 (= width implicitely)
            if (rowstride - width * 3) > a:
                row_length = width + (rowstride - width * 3) / 3
    
            self.gl_marker("Painting RGB24 update at %d,%d, size %d,%d, stride is %d, row length %d, alignment %d" % (x, y, width, height, rowstride, row_length, alignment))
            # Upload data as temporary RGB texture
            glBindTexture(GL_TEXTURE_RECTANGLE_ARB, self.textures[TEX_RGB])
            glPixelStorei(GL_UNPACK_ROW_LENGTH, row_length)
            glPixelStorei(GL_UNPACK_ALIGNMENT, alignment)
            glTexParameteri(GL_TEXTURE_RECTANGLE_ARB, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_RECTANGLE_ARB, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            glTexImage2D(GL_TEXTURE_RECTANGLE_ARB, 0, 4, width, height, 0, GL_RGB, GL_UNSIGNED_BYTE, img_data)
    
            # Draw textured RGB quad at the right coordinates
            glBegin(GL_QUADS)
            glTexCoord2i(0, 0)
            glVertex2i(x, y)
            glTexCoord2i(0, height)
            glVertex2i(x, y+height)
            glTexCoord2i(width, height)
            glVertex2i(x+width, y+height)
            glTexCoord2i(width, 0)
            glVertex2i(x+width, y)
            glEnd()
    
            # Present update to screen
            self.present_fbo(drawable)
            # present_fbo has reset state already

        finally:
            drawable.gl_end()
        return True

    def do_video_paint(self, img, x, y, enc_width, enc_height, width, height, options, callbacks):
        #we need to run in UI thread from here on!
        #ideally, the decoder would manage buffers properly...
        #instead of relying on us making a copy before leaving the decoding thread
        img.clone_pixel_data()
        gobject.idle_add(self.gl_paint_planar, img, x, y, enc_width, enc_height, width, height, callbacks)

    def gl_paint_planar(self, img, x, y, enc_width, enc_height, width, height, callbacks):
        #this function runs in the UI thread, no video_decoder lock held
        pixel_format = img.get_pixel_format()
        assert pixel_format in ("YUV420P", "YUV422P", "YUV444P", "GBRP"), "sorry the GL backing does not handle pixel format %s yet!" % (pixel_format)
        drawable = self.gl_init()
        if not drawable:
            debug("OpenGL cannot paint planar, drawable is not set")
            fire_paint_callbacks(callbacks, False)
            return
        try:
            try:
                self.update_planar_textures(x, y, enc_width, enc_height, img, pixel_format, scaling=(enc_width!=width or enc_height!=height))
                if self.paint_screen:
                    # Update FBO texture
                    self.render_planar_update(x, y, enc_width, enc_height, x_scale=width/enc_width, y_scale=height/enc_height)
                    # Present it on screen
                    self.present_fbo(drawable)
                fire_paint_callbacks(callbacks, True)
            except Exception, e:
                log.error("OpenGL paint error: %s", e, exc_info=True)
                fire_paint_callbacks(callbacks, False)
        finally:
            drawable.gl_end()

    def update_planar_textures(self, x, y, width, height, img, pixel_format, scaling=False):
        assert x==0 and y==0
        assert self.textures is not None, "no OpenGL textures!"
        debug("update_planar_textures(%s)", (x, y, width, height, img, pixel_format))

        divs = get_subsampling_divs(pixel_format)
        if self.pixel_format is None or self.pixel_format!=pixel_format or self.texture_size!=(width, height):
            self.pixel_format = pixel_format
            self.texture_size = (width, height)
            debug("GL creating new planar textures for pixel format %s using divs=%s", pixel_format, divs)
            self.gl_marker("Creating new planar textures, pixel format %s" % (pixel_format))
            # Create textures of the same size as the window's
            glEnable(GL_TEXTURE_RECTANGLE_ARB)

            for texture, index in ((GL_TEXTURE0, 0), (GL_TEXTURE1, 1), (GL_TEXTURE2, 2)):
                (div_w, div_h) = divs[index]
                glActiveTexture(texture)
                glBindTexture(GL_TEXTURE_RECTANGLE_ARB, self.textures[index])
                glEnable(GL_TEXTURE_RECTANGLE_ARB)
                mag_filter = GL_NEAREST
                if scaling or (div_w > 1 or div_h > 1):
                    mag_filter = GL_LINEAR
                glTexParameteri(GL_TEXTURE_RECTANGLE_ARB, GL_TEXTURE_MAG_FILTER, mag_filter)
                glTexParameteri(GL_TEXTURE_RECTANGLE_ARB, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
                glTexImage2D(GL_TEXTURE_RECTANGLE_ARB, 0, GL_LUMINANCE, width/div_w, height/div_h, 0, GL_LUMINANCE, GL_UNSIGNED_BYTE, None)


        debug("Updating planar textures: %sx%s %s", width, height, pixel_format)
        self.gl_marker("Updating planar textures: %sx%s %s" % (width, height, pixel_format))
        U_width = 0
        U_height = 0
        rowstrides = img.get_rowstride()
        img_data = img.get_pixels()
        assert len(rowstrides)==3
        assert len(img_data)==3
        for texture, index in ((GL_TEXTURE0, 0), (GL_TEXTURE1, 1), (GL_TEXTURE2, 2)):
            (div_w, div_h) = divs[index]
            glActiveTexture(texture)
            glBindTexture(GL_TEXTURE_RECTANGLE_ARB, self.textures[index])
            glPixelStorei(GL_UNPACK_ROW_LENGTH, rowstrides[index])
            pixel_data = img_data[index]
            debug("texture %s: div=%s, rowstride=%s, %sx%s, data=%s bytes", index, divs[index], rowstrides[index], width/div_w, height/div_h, len(pixel_data))
            glTexSubImage2D(GL_TEXTURE_RECTANGLE_ARB, 0, x, y, width/div_w, height/div_h, GL_LUMINANCE, GL_UNSIGNED_BYTE, pixel_data)
            if index == 1:
                U_width = width/div_w
                U_height = height/div_h
            elif index == 2:
                if width/div_w != U_width:
                    log.error("Width of V plane is %d, differs from width of corresponding U plane (%d), pixel_format is %d", width/div_w, U_width, pixel_format)
                if height/div_h != U_height:
                    log.error("Height of V plane is %d, differs from height of corresponding U plane (%d), pixel_format is %d", height/div_h, U_height, pixel_format)

    def render_planar_update(self, rx, ry, rw, rh, x_scale=1, y_scale=1):
        debug("render_planar_update%s pixel_format=%s", (rx, ry, rw, rh, x_scale, y_scale), self.pixel_format)
        if self.pixel_format not in ("YUV420P", "YUV422P", "YUV444P", "GBRP"):
            #not ready to render yet
            return
        assert rx==0 and ry==0
        if self.pixel_format == "GBRP":
            self.set_rgbP_paint_state()
        self.gl_marker("Painting planar update, format %s" % (self.pixel_format))
        divs = get_subsampling_divs(self.pixel_format)
        glEnable(GL_FRAGMENT_PROGRAM_ARB)
        for texture, index in ((GL_TEXTURE0, 0), (GL_TEXTURE1, 1), (GL_TEXTURE2, 2)):
            glActiveTexture(texture)
            glBindTexture(GL_TEXTURE_RECTANGLE_ARB, self.textures[index])

        tw, th = self.texture_size
        debug("render_planar_update texture_size=%s, size=%s", self.texture_size, self.size)
        glBegin(GL_QUADS)
        for x,y in ((rx, ry), (rx, ry+rh), (rx+rw, ry+rh), (rx+rw, ry)):
            ax = min(tw, x)
            ay = min(th, y)
            for texture, index in ((GL_TEXTURE0, 0), (GL_TEXTURE1, 1), (GL_TEXTURE2, 2)):
                (div_w, div_h) = divs[index]
                glMultiTexCoord2i(texture, ax/div_w, ay/div_h)
            glVertex2i(int(ax*x_scale), int(ay*y_scale))
        glEnd()
        if self.pixel_format == "GBRP":
            self.unset_rgbP_paint_state()
