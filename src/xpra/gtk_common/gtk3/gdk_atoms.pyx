# This file is part of Xpra.
# Copyright (C) 2017-2018 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

# cython code for manipulating GdkAtoms

#cython: wraparound=False, language_level=3

from gi.repository import Gdk

from xpra.os_util import bytestostr
from libc.stdint cimport uintptr_t  #pylint: disable=syntax-error


cdef extern from "Python.h":
    int PyObject_AsReadBuffer(object obj, void ** buffer, Py_ssize_t * buffer_len) except -1

cdef extern from "gtk-3.0/gdk/gdk.h":
    ctypedef void* GdkAtom
    GdkAtom GDK_NONE

cdef extern from "gtk-3.0/gdk/gdkproperty.h":
    ctypedef char gchar
    ctypedef int gint
    ctypedef gint gboolean
    gchar* gdk_atom_name(GdkAtom atom)
    GdkAtom gdk_atom_intern(const gchar *atom_name, gboolean only_if_exists)


def gdk_atom_objects_from_gdk_atom_array(atom_string):
    cdef const GdkAtom * array = <GdkAtom*> NULL
    cdef GdkAtom atom
    cdef gchar*  name
    cdef Py_ssize_t array_len_bytes = 0
    cdef uintptr_t gdk_atom_value = 0
    assert PyObject_AsReadBuffer(atom_string, <const void**> &array, &array_len_bytes)==0
    cdef unsigned int array_len = array_len_bytes // sizeof(GdkAtom)
    objects = []
    cdef unsigned int i
    for i in range(array_len):
        atom = array[i]
        if atom==GDK_NONE:
            continue
        #round-trip via the name,
        #inefficient but what other constructor is there?
        name = gdk_atom_name(atom)
        if name:
            str_name = bytestostr(name)
            gdk_atom = Gdk.Atom.intern(str_name, False)
            objects.append(gdk_atom)
    return objects

def gdk_atom_array_from_atoms(atoms):
    cdef GdkAtom gdk_atom
    cdef uintptr_t gdk_atom_value
    atom_array = []
    for atom in atoms:
        gdk_atom = gdk_atom_intern(atom, False)
        if gdk_atom!=GDK_NONE:
            gdk_atom_value = <uintptr_t> gdk_atom
            atom_array.append(gdk_atom_value)
    return atom_array
