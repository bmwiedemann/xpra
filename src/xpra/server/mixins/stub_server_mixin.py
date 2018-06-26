# This file is part of Xpra.
# Copyright (C) 2018 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

"""
Base class for server mixins.
Defines the default interface methods that each mixin may override.
"""
class StubServerMixin(object):

    """
    Initialize this instance with the options given.
    Options are usually obtained by parsing the command line,
    or using a default configuration object.
    """
    def init(self, _opts):
        pass

    """
    Initialize state attributes.
    """
    def init_state(self):
        pass


    """
    Called when we reset the focus.
    """
    def reset_focus(self):
        pass

    """
    Called when the last client has exited,
    so we can reset things to their original state.
    """
    def reset_state(self):
        pass

    """
    Free up any resources.
    """
    def cleanup(self):
        pass

    """
    After initialization, prepare to run.
    """
    def setup(self):
        pass

    """
    Prepare to run, this method runs in parallel to save startup time.
    """
    def threaded_setup(self):
        pass

    """
    Prepare to handle connections from the given sockets.
    """
    def init_sockets(self, _sockets):
        pass

    """
    Capabilities provided by this mixin.
    """
    def get_caps(self, _source):
        return {}

    """
    Features provided by this mixin.
    (the difference with capabilities is that those will only
    be returned if the client requests 'features')
    """
    def get_server_features(self, _source):
        return {}

    """
    When the user in control of the session changes,
    this method will be called.
    """
    def set_session_driver(self, _source):
        pass

    """
    Runtime information on this mixin, includes state and settings.
    Somewhat overlaps with the capabilities and features,
    but the data is returned in a structured format. (ie: nested dictionaries)
    """
    def get_info(self, _proto):
        return {}

    """
    Runtime information on this mixin,
    unlike get_info() this method will be called
    from the UI thread.
    """
    def get_ui_info(self, proto, client_uuids=None, *args):
        return {}

    """
    Register the packet types that this mixin can handle.
    """
    def init_packet_handlers(self):
        pass

    """
    Parse capabilities from a new connection.
    """
    def parse_hello(self, ss, caps, send_ui):
        pass

    """
    Cleanup method for a specific connection.
    (to cleanup / free up resources associated with a specific client or connection)
    """
    def cleanup_protocol(self, protocol):
        pass
