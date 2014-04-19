# This file is part of Xpra.
# Copyright (C) 2008, 2009 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2010-2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import time

from xpra.util import dump_exc, AdHocStruct
from xpra.log import Logger
log = Logger("x11", "bindings", "core")


include "constants.pxi"

###################################
# Headers, python magic
###################################
cdef extern from "stdlib.h":
    void* malloc(size_t __size)
    void free(void* mem)

cdef extern from "X11/Xutil.h":
    pass

######
# Xlib primitives and constants
######

ctypedef unsigned long CARD32

cdef extern from "X11/Xlib.h":
    ctypedef struct Display:
        pass
    # To make it easier to translate stuff in the X header files into
    # appropriate pyrex declarations, without having to untangle the typedefs
    # over and over again, here are some convenience typedefs.  (Yes, CARD32
    # really is 64 bits on 64-bit systems.  Why?  I have no idea.)
    ctypedef CARD32 XID
    ctypedef CARD32 Time

    ctypedef int Bool
    ctypedef int Status
    ctypedef CARD32 Atom

    Atom XInternAtom(Display * display, char * atom_name, Bool only_if_exists)

    int XFree(void * data)

    void XGetErrorText(Display * display, int code, char * buffer_return, int length)

    int XUngrabKeyboard(Display * display, Time time)
    int XUngrabPointer(Display * display, Time time)


from display_source cimport get_display
from display_source import get_display_name

cdef class X11CoreBindings:

    def __cinit__(self):
        self.display = get_display()
        assert self.display!=NULL, "display is not set!"
        dn = get_display_name()
        self.display_name = dn

    cdef xatom(self, str_or_int):
        """Returns the X atom corresponding to the given Python string or Python
        integer (assumed to already be an X atom)."""
        cdef char* string
        if isinstance(str_or_int, (int, long)):
            return <Atom> str_or_int
        string = str_or_int
        return XInternAtom(self.display, string, False)

    def get_xatom(self, str_or_int):
        return self.xatom(str_or_int)

    def get_error_text(self, code):
        if type(code)!=int:
            return code
        cdef char[128] buffer
        XGetErrorText(self.display, code, buffer, 128)
        return str(buffer[:128])

    def UngrabKeyboard(self, time=CurrentTime):
        return XUngrabKeyboard(self.display, time)

    def UngrabPointer(self, time=CurrentTime):
        return XUngrabPointer(self.display, time)
