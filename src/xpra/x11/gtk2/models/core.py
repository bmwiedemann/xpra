# This file is part of Xpra.
# Copyright (C) 2008, 2009 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2011-2015 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import glib
import gobject
from gtk import gdk
import signal

from xpra.x11.gtk2.models import Unmanageable
from xpra.gtk_common.gobject_util import AutoPropGObjectMixin, one_arg_signal
from xpra.gtk_common.error import XError, xsync, xswallow
from xpra.x11.bindings.window_bindings import X11WindowBindings, constants, SHAPE_KIND #@UnresolvedImport
from xpra.x11.gtk_x11.prop import prop_get, prop_set
from xpra.x11.gtk_x11.send_wm import send_wm_delete_window
from xpra.x11.gtk2.composite import CompositeHelper
from xpra.x11.gtk2.gdk_bindings import (
                add_event_receiver,                         #@UnresolvedImport
                remove_event_receiver,                      #@UnresolvedImport
               )

from xpra.log import Logger
log = Logger("x11", "window")
metalog = Logger("x11", "window", "metadata")
shapelog = Logger("x11", "window", "shape")
grablog = Logger("x11", "window", "grab")
framelog = Logger("x11", "window", "frame")
geomlog = Logger("x11", "window", "geometry")


X11Window = X11WindowBindings()
ADDMASK = gdk.STRUCTURE_MASK | gdk.PROPERTY_CHANGE_MASK | gdk.FOCUS_CHANGE_MASK
USE_XSHM = os.environ.get("XPRA_XSHM", "1")=="1"

# grab stuff:
NotifyNormal        = constants["NotifyNormal"]
NotifyGrab          = constants["NotifyGrab"]
NotifyUngrab        = constants["NotifyUngrab"]
NotifyWhileGrabbed  = constants["NotifyWhileGrabbed"]
NotifyNonlinearVirtual = constants["NotifyNonlinearVirtual"]
GRAB_CONSTANTS = {
                  NotifyNormal          : "NotifyNormal",
                  NotifyGrab            : "NotifyGrab",
                  NotifyUngrab          : "NotifyUngrab",
                  NotifyWhileGrabbed    : "NotifyWhileGrabbed",
                 }
DETAIL_CONSTANTS    = {}
for x in ("NotifyAncestor", "NotifyVirtual", "NotifyInferior",
          "NotifyNonlinear", "NotifyNonlinearVirtual", "NotifyPointer",
          "NotifyPointerRoot", "NotifyDetailNone"):
    DETAIL_CONSTANTS[constants[x]] = x
grablog("pointer grab constants: %s", GRAB_CONSTANTS)
grablog("detail constants: %s", DETAIL_CONSTANTS)

#these properties are not handled, and we don't want to spam the log file
#whenever an app decides to change them:
PROPERTIES_IGNORED = os.environ.get("XPRA_X11_PROPERTIES_IGNORED", "_NET_WM_OPAQUE_REGION").split(",")
#make it easier to debug property changes, just add them here:
#ie: {"WM_PROTOCOLS" : ["atom"]}
PROPERTIES_DEBUG = {}


def sanestr(s):
    return (s or "").strip("\0").replace("\0", " ")


class CoreX11WindowModel(AutoPropGObjectMixin, gobject.GObject):
    """
        The utility superclass for all GTK2 / X11 window models,
        it wraps an X11 window (the "client-window").
        Defines the common properties and signals,
        sets up the composite helper so we get the damage events.
        The x11_property_handlers sync X11 window properties into Python objects,
        the py_property_handlers do it in the other direction.
    """
    __common_properties__ = {
        #the actual X11 client window
        "client-window": (gobject.TYPE_PYOBJECT,
                "gtk.gdk.Window representing the client toplevel", "",
                gobject.PARAM_READABLE),
        #the X11 window id
        "xid": (gobject.TYPE_INT,
                "X11 window id", "",
                -1, 65535, -1,
                gobject.PARAM_READABLE),
        #FIXME: this is an ugly virtual property
        "geometry": (gobject.TYPE_PYOBJECT,
                "current (border-corrected, relative to parent) coordinates (x, y, w, h) for the window", "",
                gobject.PARAM_READABLE),
        #if the window depth is 32 bit
        "has-alpha": (gobject.TYPE_BOOLEAN,
                "Does the window use transparency", "",
                False,
                gobject.PARAM_READABLE),
        #from WM_CLIENT_MACHINE
        "client-machine": (gobject.TYPE_PYOBJECT,
                "Host where client process is running", "",
                gobject.PARAM_READABLE),
        #from _NET_WM_PID
        "pid": (gobject.TYPE_INT,
                "PID of owning process", "",
                -1, 65535, -1,
                gobject.PARAM_READABLE),
        #from _NET_WM_NAME or WM_NAME
        "title": (gobject.TYPE_PYOBJECT,
                "Window title (unicode or None)", "",
                gobject.PARAM_READABLE),
        #from WM_WINDOW_ROLE
        "role" : (gobject.TYPE_PYOBJECT,
                "The window's role (ICCCM session management)", "",
                gobject.PARAM_READABLE),
        #from WM_PROTOCOLS via XGetWMProtocols
        "protocols": (gobject.TYPE_PYOBJECT,
                "Supported WM protocols", "",
                gobject.PARAM_READABLE),
        #from WM_COMMAND
        "command": (gobject.TYPE_PYOBJECT,
                "Command used to start or restart the client", "",
                gobject.PARAM_READABLE),
        #from WM_CLASS via getClassHint
        "class-instance": (gobject.TYPE_PYOBJECT,
                "Classic X 'class' and 'instance'", "",
                gobject.PARAM_READABLE),
        #ShapeNotify events will populate this using XShapeQueryExtents
        "shape": (gobject.TYPE_PYOBJECT,
                "Window XShape data", "",
                gobject.PARAM_READABLE),
        #synced to "_NET_FRAME_EXTENTS"
        "frame": (gobject.TYPE_PYOBJECT,
                "Size of the window frame, as per _NET_FRAME_EXTENTS", "",
                gobject.PARAM_READWRITE),
        #synced to "_NET_WM_ALLOWED_ACTIONS"
        "allowed-actions": (gobject.TYPE_PYOBJECT,
                "Supported WM actions", "",
                gobject.PARAM_READWRITE),
           }

    __common_signals__ = {
        #signals we emit:
        "unmanaged"                     : one_arg_signal,
        "raised"                        : one_arg_signal,
        "initiate-moveresize"           : one_arg_signal,
        "grab"                          : one_arg_signal,
        "ungrab"                        : one_arg_signal,
        "bell"                          : one_arg_signal,
        "client-contents-changed"       : one_arg_signal,
        #x11 events we catch (and often re-emit as something else):
        "xpra-property-notify-event"    : one_arg_signal,
        "xpra-xkb-event"                : one_arg_signal,
        "xpra-shape-event"              : one_arg_signal,
        "xpra-configure-event"          : one_arg_signal,
        "xpra-unmap-event"              : one_arg_signal,
        "xpra-client-message-event"     : one_arg_signal,
        "xpra-focus-in-event"           : one_arg_signal,
        "xpra-focus-out-event"          : one_arg_signal,
        }

    #things that we expose:
    _property_names         = ["xid", "has-alpha", "client-machine", "pid", "title", "role", "command", "shape", "class-instance", "protocols"]
    #exposed and changing (should be watched for notify signals):
    _dynamic_property_names = ["title", "command", "shape", "class-instance", "protocols"]
    #should not be exported to the clients:
    _internal_property_names = ["frame", "allowed-actions"]
    _initial_x11_properties = ["_NET_WM_PID", "WM_CLIENT_MACHINE",
                               "WM_NAME", "_NET_WM_NAME",        #_NET_WM_NAME is redundant, as it calls the same handler as "WM_NAME"
                               "WM_PROTOCOLS", "WM_CLASS", "WM_WINDOW_ROLE"]
    _DEFAULT_NET_WM_ALLOWED_ACTIONS = []
    _MODELTYPE = "Core"
    _scrub_x11_properties       = [
                              "WM_STATE",
                              #"_NET_WM_STATE",    # "..it should leave the property in place when it is shutting down"
                              "_NET_FRAME_EXTENTS", "_NET_WM_ALLOWED_ACTIONS"]

    def __init__(self, client_window):
        log("new window %#x", client_window.xid)
        super(CoreX11WindowModel, self).__init__()
        self.xid = client_window.xid
        self.client_window = client_window
        self.client_window_saved_events = self.client_window.get_events()
        self._managed = False
        self._managed_handlers = []
        self._setup_done = False
        self._geometry = None
        self._composite = None
        self._damage_forward_handle = None
        self._kill_count = 0
        self._internal_set_property("client-window", client_window)


    def __repr__(self):
        try:
            return "%s(%#x)" % (type(self).__name__, self.xid)
        except:
            return repr(self)


    #########################################
    # Setup and teardown
    #########################################

    def is_managed(self):
        return self._managed


    def call_setup(self):
        """
            Call this method to prepare the window:
            * makes sure it still exists
              (by querying its geometry which may raise an XError)
            * setup composite redirection
            * calls setup
            The difficulty comes from X11 errors and synchronization:
            we want to catch errors and undo what we've done.
            The mix of GTK and pure-X11 calls is not helping.
        """
        try:
            with xsync:
                self._geometry = X11Window.geometry_with_border(self.xid)
                self._read_initial_X11_properties()
        except XError as e:
            raise Unmanageable(e)
        add_event_receiver(self.client_window, self)
        # Keith Packard says that composite state is undefined following a
        # reparent, so I'm not sure doing this here in the superclass,
        # before we reparent, actually works... let's wait and see.
        try:
            self._composite = CompositeHelper(self.client_window, False, USE_XSHM)
            with xsync:
                self._composite.setup()
                if X11Window.displayHasXShape():
                    X11Window.XShapeSelectInput(self.xid)
        except XError as e:
            remove_event_receiver(self.client_window, self)
            log("%s %#x does not support compositing: %s", self._MODELTYPE, self.xid, e)
            with xswallow:
                self._composite.destroy()
            self._composite = None
            raise Unmanageable(e)
        #compositing is now enabled,
        #from now on we must call setup_failed to clean things up
        self._managed = True
        try:
            with xsync:
                self.setup()
        except XError as e:
            try:
                with xsync:
                    self.setup_failed(e)
            except Exception as ex:
                log.error("error in cleanup handler: %s", ex)
            raise Unmanageable(e)
        self._setup_done = True

    def setup_failed(self, e):
        log("cannot manage %s %#x: %s", self._MODELTYPE, self.xid, e)
        self.do_unmanaged(False)

    def setup(self):
        # Start listening for important events.
        self.client_window.set_events(self.client_window_saved_events | ADDMASK)
        self._damage_forward_handle = self._composite.connect("contents-changed", self._forward_contents_changed)
        self._setup_property_sync()


    def unmanage(self, exiting=False):
        if self._managed:
            self.emit("unmanaged", exiting)

    def do_unmanaged(self, wm_exiting):
        if not self._managed:
            return
        self._managed = False
        log("%s.do_unmanaged(%s) damage_forward_handle=%s, composite=%s", self._MODELTYPE, wm_exiting, self._damage_forward_handle, self._composite)
        remove_event_receiver(self.client_window, self)
        glib.idle_add(self.managed_disconnect)
        if self._composite:
            if self._damage_forward_handle:
                self._composite.disconnect(self._damage_forward_handle)
                self._damage_forward_handle = None
            self._composite.destroy()
            self._composite = None
            self._scrub_x11()


    def _setup_property_sync(self):
        metalog("setup_property_sync()")
        #python properties which trigger an X11 property to be updated:
        for prop, cb in self._py_property_handlers.items():
            self.connect("notify::%s" % prop, cb)
        #initial sync:
        for cb in self._py_property_handlers.values():
            cb(self)
        #this one is special, and overriden in BaseWindow too:
        self.managed_connect("notify::protocols", self._update_can_focus)

    def _update_can_focus(self, *args):
        can_focus = "WM_TAKE_FOCUS" in self.get_property("protocols")
        self._updateprop("can-focus", can_focus)

    def _read_initial_X11_properties(self):
        """ This is called within an XSync context,
            so that X11 calls can raise XErrors,
            pure GTK calls are not allowed. (they would trap the X11 error and crash!)
            Calling _updateprop is safe, because setup has not completed yet,
            so the property update will not fire notify()
        """
        metalog("read_initial_X11_properties() core")
        #immutable ones:
        has_alpha = X11Window.get_depth(self.xid)==32
        metalog("initial X11 properties: xid=%#x, has-alpha=%s", self.xid, has_alpha)
        self._updateprop("xid", self.xid)
        self._updateprop("has-alpha", has_alpha)
        self._updateprop("allowed-actions", self._DEFAULT_NET_WM_ALLOWED_ACTIONS)
        self._updateprop("shape", self._read_xshape())
        #note: some of those are technically mutable,
        #but we don't export them as "dynamic" properties, so this won't be propagated
        #maybe we want to catch errors parsing _NET_WM_ICON ?
        metalog("initial X11_properties: querying %s", self._initial_x11_properties)
        #to make sure we don't call the same handler twice which is pointless
        #(the same handler may handle more than one X11 property)
        handlers = set()
        for mutable in self._initial_x11_properties:
            handler = self._x11_property_handlers.get(mutable)
            if not handler:
                log.error("BUG: unknown initial X11 property: %s", mutable)
            elif handler not in handlers:
                handlers.add(handler)
                handler(self)

    def _scrub_x11(self):
        metalog("scrub_x11() x11 properties=%s", self._scrub_x11_properties)
        if not self._scrub_x11_properties:
            return
        with xswallow:
            for prop in self._scrub_x11_properties:
                X11Window.XDeleteProperty(self.xid, prop)


    #########################################
    # Composite
    #########################################

    def _forward_contents_changed(self, obj, event):
        if self._managed:
            self.emit("client-contents-changed", event)

    def acknowledge_changes(self):
        c = self._composite
        assert c, "composite window destroyed outside the UI thread?"
        c.acknowledge_changes()

    def uses_XShm(self):
        c = self._composite
        return c and c.get_shm_handle() is not None

    def get_image(self, x, y, width, height, logger=log.debug):
        handle = self._composite.get_contents_handle()
        if handle is None:
            logger("get_image(..) pixmap is None for window %#x", self.xid)
            return  None

        #try XShm:
        try:
            #logger("get_image(%s, %s, %s, %s) geometry=%s", x, y, width, height, self._geometry[:4])
            shm = self._composite.get_shm_handle()
            #logger("get_image(..) XShm handle: %s, handle=%s, pixmap=%s", shm, handle, handle.get_pixmap())
            if shm is not None:
                with xsync:
                    shm_image = shm.get_image(handle.get_pixmap(), x, y, width, height)
                #logger("get_image(..) XShm image: %s", shm_image)
                if shm_image:
                    return shm_image
        except Exception as e:
            if type(e)==XError and e.msg=="BadMatch":
                logger("get_image(%s, %s, %s, %s) get_image BadMatch ignored (window already gone?)", x, y, width, height)
            else:
                log.warn("get_image(%s, %s, %s, %s) get_image %s", x, y, width, height, e, exc_info=True)

        try:
            w = min(handle.get_width(), width)
            h = min(handle.get_height(), height)
            if w!=width or h!=height:
                logger("get_image(%s, %s, %s, %s) clamped to pixmap dimensions: %sx%s", x, y, width, height, w, h)
            with xsync:
                return handle.get_image(x, y, w, h)
        except Exception as e:
            if type(e)==XError and e.msg=="BadMatch":
                logger("get_image(%s, %s, %s, %s) get_image BadMatch ignored (window already gone?)", x, y, width, height)
            else:
                log.warn("get_image(%s, %s, %s, %s) get_image %s", x, y, width, height, e, exc_info=True)
            return None


    #########################################
    # XShape
    #########################################

    def _read_xshape(self):
        if not X11Window.displayHasXShape():
            return {}
        extents = X11Window.XShapeQueryExtents(self.xid)
        if not extents:
            shapelog("read_shape for window %#x: no extents", self.xid)
            return {}
        v = {}
        #w,h = X11Window.getGeometry(xid)[2:4]
        bextents = extents[0]
        cextents = extents[1]
        if bextents[0]==0 and cextents[0]==0:
            shapelog("read_shape for window %#x: none enabled", self.xid)
            return {}
        v["Bounding.extents"] = bextents
        v["Clip.extents"] = cextents
        for kind in SHAPE_KIND.keys():
            kind_name = SHAPE_KIND[kind]
            rectangles = X11Window.XShapeGetRectangles(self.xid, kind)
            v[kind_name+".rectangles"] = rectangles
        shapelog("_read_shape()=%s", v)
        return v


    #########################################
    # Connect to signals in a "managed" way
    #########################################

    def managed_connect(self, detailed_signal, handler, *args):
        """ connects a signal handler and makes sure we will clean it up on unmanage() """
        handler_id = self.connect(detailed_signal, handler, *args)
        self._managed_handlers.append(handler_id)
        return handler_id

    def managed_disconnect(self):
        for handler_id in self._managed_handlers:
            self.disconnect(handler_id)
        self._managed_handlers = []


    ################################
    # Property reading
    ################################

    def do_get_property_geometry(self, pspec=None):
        if self._geometry is None:
            with xsync:
                self._geometry = X11Window.geometry_with_border(self.xid)
                geomlog("BaseWindowModel.do_get_property_geometry() synced update: geometry(%#x)=%s", self.xid, self._geometry)
        x, y, w, h, b = self._geometry
        return (x, y, w + 2*b, h + 2*b)


    def get_position(self):
        return self.do_get_property_geometry()[:2]

    def get_dimensions(self):
        return self.do_get_property_geometry()[2:4]


    #########################################
    # Properties we choose to expose
    #########################################

    def get_property_names(self):
        """ The properties that should be exposed to clients """
        return self._property_names

    def get_dynamic_property_names(self):
        """ The properties that may change over time """
        return self._dynamic_property_names

    def get_internal_property_names(self):
        """ The properties that should not be exposed to the client """
        return self._internal_property_names

    def _updateprop(self, name, value):
        """ Updates the property and fires notify(),
            but only if the value has changed
            and if the window has finished setting up and it is still managed.
            Can only be used for AutoPropGObjectMixin properties.
        """
        cur = self._gproperties.get(name, None)
        if name not in self._gproperties or cur!=value:
            metalog("updateprop(%s, %s) previous value=%s", name, value, cur)
            self._gproperties[name] = value
            if self._setup_done and self._managed:
                self.notify(name)
            else:
                metalog("not sending notify(%s) (setup done=%s, managed=%s)", name, self._setup_done, self._managed)
        else:
            metalog("updateprop(%s, %s) unchanged", name, value)

    def get(self, name, default_value=None):
        """ Allows us the avoid defining all the attributes we may ever query,
            returns the default value if the property does not exist.
        """
        if name in self._property_names:
            return self.get_property(name)
        return default_value


    #temporary? / convenience access methods:
    def is_OR(self):
        """ Is this an override-redirect window? """
        return self.get("override-redirect", False)

    def is_tray(self):
        """ Is this a tray window? """
        return self.get("tray", False)

    def is_shadow(self):
        """ Is this a shadow instead of a real window? """
        return False

    def has_alpha(self):
        """ Does the pixel data have an alpha channel? """
        return self.get("has-alpha", False)


    #########################################
    # Python objects synced to X11 properties
    #########################################

    def prop_set(self, key, ptype, value):
        prop_set(self.client_window, key, ptype, value)


    def _sync_allowed_actions(self, *args):
        actions = self.get_property("allowed-actions") or []
        metalog("sync_allowed_actions: setting _NET_WM_ALLOWED_ACTIONS=%s on %#x", actions, self.xid)
        with xswallow:
            prop_set(self.client_window, "_NET_WM_ALLOWED_ACTIONS", ["atom"], actions)
    def _handle_frame_changed(self, *args):
        #legacy name for _sync_frame() called from Wm
        self._sync_frame()
    def _sync_frame(self, *args):
        v = self.get_property("frame")
        framelog("sync_frame: frame(%#x)=%s", self.xid, v)
        if not v and (not self.is_OR() and not self.is_tray()):
            root = self.client_window.get_screen().get_root_window()
            v = prop_get(root, "DEFAULT_NET_FRAME_EXTENTS", ["u32"], ignore_errors=True)
        if not v:
            #default for OR, or if we don't have any other value:
            v = (0, 0, 0, 0)
        framelog("sync_frame: setting _NET_FRAME_EXTENTS=%s on %#x", v, self.xid)
        with xswallow:
            prop_set(self.client_window, "_NET_FRAME_EXTENTS", ["u32"], v)

    _py_property_handlers = {
        "allowed-actions"    : _sync_allowed_actions,
        "frame"              : _sync_frame,
        }


    #########################################
    # X11 properties synced to Python objects
    #########################################

    def prop_get(self, key, ptype, ignore_errors=None, raise_xerrors=False):
        """
            Get an X11 property from the client window,
            using the automatic type conversion code from prop.py
            Ignores property errors during setup_client.
        """
        if ignore_errors is None and (not self._setup_done or not self._managed):
            ignore_errors = True
        return prop_get(self.client_window, key, ptype, ignore_errors=bool(ignore_errors), raise_xerrors=raise_xerrors)


    def do_xpra_property_notify_event(self, event):
        #X11: PropertyNotify
        assert event.window is self.client_window
        self._handle_property_change(str(event.atom))

    def _handle_property_change(self, name):
        #ie: _handle_property_change("_NET_WM_NAME")
        metalog("Property changed on %#x: %s", self.xid, name)
        if name in PROPERTIES_DEBUG:
            metalog.info("%s=%s", name, self.prop_get(name, PROPERTIES_DEBUG[name], True, False))
        if name in PROPERTIES_IGNORED:
            return
        handler = self._x11_property_handlers.get(name)
        if handler:
            handler(self)

    #specific properties:
    def _handle_pid_change(self):
        pid = self.prop_get("_NET_WM_PID", "u32") or -1
        metalog("_NET_WM_PID=%s", pid)
        self._updateprop("pid", pid)

    def _handle_client_machine_change(self):
        client_machine = self.prop_get("WM_CLIENT_MACHINE", "latin1")
        metalog("WM_CLIENT_MACHINE=%s", client_machine)
        self._updateprop("client-machine", client_machine)

    def _handle_wm_name_change(self):
        name = self.prop_get("_NET_WM_NAME", "utf8", True)
        metalog("_NET_WM_NAME=%s", name)
        if name is None:
            name = self.prop_get("WM_NAME", "latin1", True)
            metalog("WM_NAME=%s", name)
        self._updateprop("title", sanestr(name))
        metalog("wm_name changed")

    def _handle_role_change(self):
        role = self.prop_get("WM_WINDOW_ROLE", "latin1")
        metalog("WM_WINDOW_ROLE=%s", role)
        self._updateprop("role", role)

    def _handle_protocols_change(self):
        with xsync:
            protocols = X11Window.XGetWMProtocols(self.xid)
        metalog("WM_PROTOCOLS=%s", protocols)
        self._updateprop("protocols", protocols)

    def _handle_command_change(self):
        command = self.prop_get("WM_COMMAND", "latin1")
        metalog("WM_COMMAND=%s", command)
        if command:
            command = command.strip("\0")
        self._updateprop("command", command)

    def _handle_class_change(self):
        with xswallow:
            class_instance = X11Window.getClassHint(self.xid)
            metalog("WM_CLASS=%s", class_instance)
            self._updateprop("class-instance", class_instance)

    #these handlers must not generate X11 errors (must use XSync)
    _x11_property_handlers = {
        "_NET_WM_PID"       : _handle_pid_change,
        "WM_CLIENT_MACHINE" : _handle_client_machine_change,
        "WM_NAME"           : _handle_wm_name_change,
        "_NET_WM_NAME"      : _handle_wm_name_change,
        "WM_WINDOW_ROLE"    : _handle_role_change,
        "WM_PROTOCOLS"      : _handle_protocols_change,
        "WM_COMMAND"        : _handle_command_change,
        "WM_CLASS"          : _handle_class_change,
        }


    #########################################
    # X11 Events
    #########################################

    def do_xpra_unmap_event(self, event):
        self.unmanage()

    def do_xpra_destroy_event(self, event):
        if event.delivered_to is self.client_window:
            # This is somewhat redundant with the unmap signal, because if you
            # destroy a mapped window, then a UnmapNotify is always generated.
            # However, this allows us to catch the destruction of unmapped
            # ("iconified") windows, and also catch any mistakes we might have
            # made with unmap heuristics.  I love the smell of XDestroyWindow in
            # the morning.  It makes for simple code:
            self.unmanage()


    def process_client_message_event(self, event):
        # FIXME
        # Need to listen for:
        #   _NET_CURRENT_DESKTOP
        #   _NET_WM_PING responses
        # and maybe:
        #   _NET_RESTACK_WINDOW
        #   _NET_WM_STATE (more fully)
        if event.message_type=="_NET_CLOSE_WINDOW":
            log.info("_NET_CLOSE_WINDOW received by %s", self)
            self.request_close()
            return True
        elif event.message_type=="_NET_REQUEST_FRAME_EXTENTS":
            framelog("_NET_REQUEST_FRAME_EXTENTS")
            self._handle_frame_changed()
            return True
        #not handled:
        return False

    def do_xpra_configure_event(self, event):
        if self.client_window is None or not self._managed:
            return
        #shouldn't the border width always be 0?
        geom = (event.x, event.y, event.width, event.height, event.border_width)
        geomlog("CoreX11WindowModel.do_xpra_configure_event(%s) client_window=%#x, old geometry=%s, new geometry=%s", event, self.xid, self._geometry, geom)
        if geom!=self._geometry:
            self._geometry = geom
            #X11Window.MoveResizeWindow(self.xid, )
            self.notify("geometry")


    def do_xpra_shape_event(self, event):
        shapelog("shape event: %s, kind=%s", event, SHAPE_KIND.get(event.kind, event.kind))
        cur_shape = self.get_property("shape")
        if cur_shape and cur_shape.get("serial", 0)>=event.serial:
            shapelog("same or older xshape serial no: %#x", event.serial)
            return
        #remove serial before comparing dicts:
        try:
            cur_shape["serial"]
        except:
            pass
        #read new xshape:
        with xswallow:
            v = self._read_xshape()
            if cur_shape==v:
                shapelog("xshape unchanged")
                return
            v["serial"] = int(event.serial)
            shapelog("xshape updated with serial %#x", event.serial)
            self._internal_set_property("shape", v)


    def do_xpra_xkb_event(self, event):
        #X11: XKBNotify
        log("WindowModel.do_xpra_xkb_event(%r)" % event)
        if event.type!="bell":
            log.error("WindowModel.do_xpra_xkb_event(%r) unknown event type: %s" % (event, event.type))
            return
        event.window_model = self
        self.emit("bell", event)

    def do_xpra_client_message_event(self, event):
        #X11: ClientMessage
        log("do_xpra_client_message_event(%s)", event)
        if not event.data or len(event.data)!=5:
            log.warn("invalid event data: %s", event.data)
            return
        if not self.process_client_message_event(event):
            log.warn("do_xpra_client_message_event(%s) not handled", event)


    def do_xpra_focus_in_event(self, event):
        #X11: FocusIn
        grablog("focus_in_event(%s) mode=%s, detail=%s",
            event, GRAB_CONSTANTS.get(event.mode), DETAIL_CONSTANTS.get(event.detail, event.detail))
        if event.mode==NotifyNormal and event.detail==NotifyNonlinearVirtual:
            self.emit("raised", event)
        else:
            self.may_emit_grab(event)

    def do_xpra_focus_out_event(self, event):
        #X11: FocusOut
        grablog("focus_out_event(%s) mode=%s, detail=%s",
            event, GRAB_CONSTANTS.get(event.mode), DETAIL_CONSTANTS.get(event.detail, event.detail))
        self.may_emit_grab(event)

    def may_emit_grab(self, event):
        if event.mode==NotifyGrab:
            grablog("emitting grab on %s", self)
            self.emit("grab", event)
        if event.mode==NotifyUngrab:
            grablog("emitting ungrab on %s", self)
            self.emit("ungrab", event)


    ################################
    # Actions
    ################################

    def raise_window(self):
        self.client_window.raise_()

    def set_active(self):
        root = self.client_window.get_screen().get_root_window()
        prop_set(root, "_NET_ACTIVE_WINDOW", "u32", self.xid)


    ################################
    # Killing clients:
    ################################

    def request_close(self):
        if "WM_DELETE_WINDOW" in self.get_property("protocols"):
            with xswallow:
                send_wm_delete_window(self.client_window)
        else:
            title = self.get_property("title")
            xid = self.get_property("xid")
            log.warn("window %#x ('%s') does not support WM_DELETE_WINDOW... using force_quit", xid, title)
            # You don't wanna play ball?  Then no more Mr. Nice Guy!
            self.force_quit()

    def force_quit(self):
        pid = self.get_property("pid")
        machine = self.get_property("client-machine")
        from socket import gethostname
        localhost = gethostname()
        log("force_quit() pid=%s, machine=%s, localhost=%s", pid, machine, localhost)
        def XKill():
            with xswallow:
                X11Window.XKillClient(self.xid)
        if pid > 0 and machine is not None and machine == localhost:
            if pid==os.getpid():
                log.warn("force_quit() refusing to kill ourselves!")
                return
            if self._kill_count==0:
                #first time around: just send a SIGINT and hope for the best
                try:
                    os.kill(pid, signal.SIGINT)
                except OSError:
                    log.warn("failed to kill(SIGINT) client with pid %s", pid)
            else:
                #the more brutal way: SIGKILL + XKill
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    log.warn("failed to kill(SIGKILL) client with pid %s", pid)
                XKill()
            self._kill_count += 1
            return
        XKill()
