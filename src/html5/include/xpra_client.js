/*
 * Copyright (c) 2013 Antoine Martin <antoine@devloop.org.uk>
 * Copyright (c) 2014 Joshua Higgins <josh@kxes.net>
 * Copyright (c) 2015 Spikes, Inc.
 * Licensed under MPL 2.0
 *
 * xpra client
 *
 * requires:
 *	xpra_protocol.js
 *  xpra_window.js
 *  keycodes.js
 */

XPRA_CLIENT_FORCE_NO_WORKER = false;

function XpraClient(container) {
	// state
	var me = this;
	this.host = null;
	this.port = null;
	this.ssl = null;
	// some client stuff
	this.capabilities = {};
	this.OLD_ENCODING_NAMES_TO_NEW = {"x264" : "h264", "vpx" : "vp8"};
	this.RGB_FORMATS = ["RGBX", "RGBA"];
	this.supported_encodings = ["h264", "jpeg", "png", "rgb32"];
	this.enabled_encodings = [];
	this.normal_fullscreen_mode = false;
	// encryption
	this.encryption = false;
	this.encryption_caps = null;
	this.encryption_key = null;
	// authentication
	this.authentication = false;
	this.authentication_key = null;
	// hello
	this.HELLO_TIMEOUT = 2000;
	this.hello_timer = null;
	// modifier keys
	this.caps_lock = null;
	this.alt_modifier = null;
	this.meta_modifier = null;
	// audio stuff
	this.audio_enabled = false;
	this.audio_ctx = null;
	// the "clipboard"
	this.clipboard_buffer = "";
	this.clipboard_targets = ["UTF8_STRING", "TEXT", "STRING", "text/plain"];
	// the container div is the "screen" on the HTML page where we
	// are able to draw our windows in.
	this.container = document.getElementById(container);
	if(!this.container) {
		throw "invalid container element";
	}
	// a list of our windows
	this.id_to_window = {};
	// basic window management
	this.topwindow = null;
	this.topindex = 0;
	this.focus = -1;
	// the protocol
	this.protocol = null;
	// the client holds a list of packet handlers
	this.packet_handlers = {
		'open': this._process_open,
		'close': this._process_close,
		'disconnect': this._process_disconnect,
		'startup-complete': this._process_startup_complete,
		'hello': this._process_hello,
		'ping': this._process_ping,
		'new-window': this._process_new_window,
		'new-override-redirect': this._process_new_override_redirect,
		'window-metadata': this._process_window_metadata,
		'lost-window': this._process_lost_window,
		'raise-window': this._process_raise_window,
		'window-resized': this._process_window_resized,
		'draw': this._process_draw,
		'sound-data': this._process_sound_data,
		'clipboard-token': this._process_clipboard_token,
		'set-clipboard-enabled': this._process_set_clipboard_enabled,
		'clipboard-request': this._process_clipboard_request
	};
	// assign callback for window resize event
	if (window.jQuery) {
		jQuery(window).resize(jQuery.debounce(250, function (e) {
			me._screen_resized(e, me);
		}));
	}
	// assign the keypress callbacks
	// if we detect jQuery, use that to assign them instead
	// to allow multiple clients on the same page
	if (window.jQuery) {
		jQuery(document).keydown(function (e) {
			e.preventDefault();
			me._keyb_onkeydown(e, me);
		});
		jQuery(document).keyup(function (e) {
			e.preventDefault();
			me._keyb_onkeyup(e, me);
		});
		jQuery(document).keypress(function (e) {
			e.preventDefault();
			me._keyb_onkeypress(e, me);
		});
	} else {
		document.onkeydown = function (e) {
			me._keyb_onkeydown(e, me);
		};
		document.onkeyup = function (e) {
			me._keyb_onkeyup(e, me);
		};
		document.onkeypress = function (e) {
			me._keyb_onkeypress(e, me);
		};
	}
}

XpraClient.prototype.callback_close = function(reason) {
	if (reason === undefined) {
		reason = "unknown reason";
	}
	console.log("connection closed: "+reason);
}

XpraClient.prototype.connect = function(host, port, ssl) {
	// open the web socket, started it in a worker if available
	console.log("connecting to xpra server " + host + ":" + port + " with ssl: " + ssl);
	this.host = host;
	this.port = port;
	this.ssl = ssl;
	// detect websocket in webworker support and degrade gracefully
	if(window.Worker) {
		console.log("we have webworker support");
		// spawn worker that checks for a websocket
		var me = this;
		var worker = new Worker('include/wsworker_check.js');
		worker.addEventListener('message', function(e) {
			var data = e.data;
			switch (data['result']) {
				case true:
				// yey, we can use websocket in worker!
				console.log("we can use websocket in webworker");
				me._do_connect(true);
				break;
				case false:
				console.log("we can't use websocket in webworker, won't use webworkers");
				me._do_connect(false);
				break;
				default:
				console.log("client got unknown message from worker");
			};
		}, false);
		// ask the worker to check for websocket support, when we receive a reply
		// through the eventlistener above, _do_connect() will finish the job
		worker.postMessage({'cmd': 'check'});
	} else {
		// no webworker support
		console.log("no webworker support at all.")
	}
}

XpraClient.prototype._do_connect = function(with_worker) {
	if(with_worker && !(XPRA_CLIENT_FORCE_NO_WORKER)) {
		this.protocol = new XpraProtocolWorkerHost();
	} else {
		this.protocol = new XpraProtocol();
	}
	// set protocol to deliver packets to our packet router
	this.protocol.set_packet_handler(this._route_packet, this);
	// make uri
	var uri = "ws://";
	if (this.ssl)
		uri = "wss://";
	uri += this.host;
	uri += ":" + this.port;
	// do open
	this.protocol.open(uri);
	// wait timeout seconds for a hello, then bomb
	var me = this;
	this.hello_timer = setTimeout(function () {
		me.callback_close("Did not receive hello before timeout reached, not an Xpra server?");
	}, this.HELLO_TIMEOUT);
}

XpraClient.prototype.close = function() {
	// close all windows
	// close protocol
	this.protocol.close();
}

XpraClient.prototype.enable_encoding = function(encoding) {
	// add an encoding to our hello.encodings list
	this.enabled_encodings.push(encoding);
}

XpraClient.prototype.disable_encoding = function(encoding) {
	// remove an encoding from our hello.encodings.core list
	// as if we don't support it
	var index = this.supported_encodings.indexOf(encoding);
	if(index > -1) {
		this.supported_encodings.splice(index, 1);
	}
}

XpraClient.prototype._route_packet = function(packet, ctx) {
	// ctx refers to `this` because we came through a callback
	var packet_type = "";
	var fn = "";
	try {
		packet_type = packet[0];
		console.log("received a " + packet_type + " packet");
		fn = ctx.packet_handlers[packet_type];
		if (fn==undefined) {
			console.error("no packet handler for "+packet_type+"!");
			console.log(packet);
		}
		else
			fn(packet, ctx);
	}
	catch (e) {
		console.error("error processing '"+packet_type+"' with '"+fn+"': "+e);
		throw e;
	}
}

XpraClient.prototype._screen_resized = function(event, ctx) {
	// send the desktop_size packet so server knows we changed size
	var newsize = this._get_desktop_size();
	var packet = ["desktop_size", newsize[0], newsize[1], this._get_screen_sizes()];
	ctx.protocol.send(packet);
	// call the screen_resized function on all open windows
	for (var i in ctx.id_to_window) {
		var iwin = ctx.id_to_window[i];
		iwin.screen_resized();
	}
}

XpraClient.prototype.handle_paste = function(text) {
	// set our clipboard buffer
	this.clipboard_buffer = text;
	// send token
	var packet = ["clipboard-token", "CLIPBOARD"];
	this.protocol.send(packet);
	// tell user to paste in remote application
	alert("Paste acknowledged. Please paste in remote application.");
}

XpraClient.prototype._keyb_get_modifiers = function(event) {
	/**
	 * Returns the modifiers set for the current event.
	 * We get the list of modifiers using "get_event_modifiers"
	 * then translate "alt" and "meta" into their keymap name.
	 * (usually "mod1")
	 */
	//convert generic modifiers "meta" and "alt" into their x11 name:
	var modifiers = get_event_modifiers(event);
	//FIXME: look them up!
	var alt = "mod1";
	var meta = "mod1";
	var index = modifiers.indexOf("alt");
	if (index>=0)
		modifiers[index] = alt;
	index = modifiers.indexOf("meta");
	if (index>=0)
		modifiers[index] = meta;
	//show("get_modifiers() modifiers="+modifiers.toSource());
	return modifiers;
}

XpraClient.prototype._keyb_process = function(pressed, event) {
	/**
	 * Process a key event: key pressed or key released.
	 * Figure out the keycode, keyname, modifiers, etc
	 * And send the event to the server.
	 */
	// MSIE hack
	if (window.event)
		event = window.event;
	//show("processKeyEvent("+pressed+", "+event+") keyCode="+event.keyCode+", charCode="+event.charCode+", which="+event.which);

	var keyname = "";
	var keycode = 0;
	if (event.which)
		keycode = event.which;
	else
		keycode = event.keyCode;
	if (keycode in CHARCODE_TO_NAME)
		keyname = CHARCODE_TO_NAME[keycode];
	var DOM_KEY_LOCATION_RIGHT = 2;
	if (keyname.match("_L$") && event.location==DOM_KEY_LOCATION_RIGHT)
		keyname = keyname.replace("_L", "_R")

	var modifiers = this._keyb_get_modifiers(event);
	if (this.caps_lock)
		modifiers.push("lock");
	var keyval = keycode;
	var str = String.fromCharCode(event.which);
	var group = 0;

	var shift = modifiers.indexOf("shift")>=0;
	if ((this.caps_lock && shift) || (!this.caps_lock && !shift))
		str = str.toLowerCase();

	if (this.topwindow != null) {
		//show("win="+win.toSource()+", keycode="+keycode+", modifiers=["+modifiers+"], str="+str);
		var packet = ["key-action", this.topwindow, keyname, pressed, modifiers, keyval, str, keycode, group];
		this.protocol.send(packet);
	}
}

XpraClient.prototype._keyb_onkeydown = function(event, ctx) {
	ctx._keyb_process(true, event);
	return false;
};
XpraClient.prototype._keyb_onkeyup = function(event, ctx) {
	ctx._keyb_process(false, event);
	return false;
};

XpraClient.prototype._keyb_onkeypress = function(event, ctx) {
	/**
	 * This function is only used for figuring out the caps_lock state!
	 * onkeyup and onkeydown give us the raw keycode,
	 * whereas here we get the keycode in lowercase/uppercase depending
	 * on the caps_lock and shift state, which allows us to figure
	 * out caps_lock state since we have shift state.
	 */
	var keycode = 0;
	if (event.which)
		keycode = event.which;
	else
		keycode = event.keyCode;
	var modifiers = ctx._keyb_get_modifiers(event);

	/* PITA: this only works for keypress event... */
	caps_lock = false;
	var shift = modifiers.indexOf("shift")>=0;
	if (keycode>=97 && keycode<=122 && shift)
		caps_lock = true;
	else if (keycode>=65 && keycode<=90 && !shift)
		caps_lock = true;
	//show("caps_lock="+caps_lock);
	return false;
};

XpraClient.prototype._guess_platform_processor = function() {
	//mozilla property:
	if (navigator.oscpu)
		return navigator.oscpu;
	//ie:
	if (navigator.cpuClass)
		return navigator.cpuClass;
	return "unknown";
}

XpraClient.prototype._guess_platform_name = function() {
	//use python style strings for platforms:
	if (navigator.appVersion.indexOf("Win")!=-1)
		return "Microsoft Windows";
	if (navigator.appVersion.indexOf("Mac")!=-1)
		return "Mac OSX";
	if (navigator.appVersion.indexOf("Linux")!=-1)
		return "Linux";
	if (navigator.appVersion.indexOf("X11")!=-1)
		return "Posix";
	return "unknown";
}

XpraClient.prototype._guess_platform = function() {
	//use python style strings for platforms:
	if (navigator.appVersion.indexOf("Win")!=-1)
		return "win32";
	if (navigator.appVersion.indexOf("Mac")!=-1)
		return "darwin";
	if (navigator.appVersion.indexOf("Linux")!=-1)
		return "linux2";
	if (navigator.appVersion.indexOf("X11")!=-1)
		return "posix";
	return "unknown";
}

XpraClient.prototype._get_hex_uuid = function() {
	var s = [];
    var hexDigits = "0123456789abcdef";
    for (var i = 0; i < 36; i++) {
        s[i] = hexDigits.substr(Math.floor(Math.random() * 0x10), 1);
    }
    s[14] = "4";  // bits 12-15 of the time_hi_and_version field to 0010
    s[19] = hexDigits.substr((s[19] & 0x3) | 0x8, 1);  // bits 6-7 of the clock_seq_hi_and_reserved to 01
    s[8] = s[13] = s[18] = s[23] = "-";

    var uuid = s.join("");
    return uuid;
}

XpraClient.prototype._get_keyboard_layout = function() {
	//IE:
	//navigator.systemLanguage
	//navigator.browserLanguage
	var v = window.navigator.userLanguage || window.navigator.language;
	//ie: v="en_GB";
	v = v.split(",")[0];
	var l = v.split("-", 2);
	if (l.length==1)
		l = v.split("_", 2);
	if (l.length==1)
		return "";
	//ie: "gb"
	return l[1].toLowerCase();
}

XpraClient.prototype._get_keycodes = function() {
	//keycodes.append((nn(keyval), nn(name), nn(keycode), nn(group), nn(level)))
	var keycodes = [];
	var kc;
	for(var keycode in CHARCODE_TO_NAME) {
		kc = parseInt(keycode);
		keycodes.push([kc, CHARCODE_TO_NAME[keycode], kc, 0, 0]);
	}
	//show("keycodes="+keycodes.toSource());
	return keycodes;
}

XpraClient.prototype._get_desktop_size = function() {
	return [this.container.clientWidth, this.container.clientHeight];
}

XpraClient.prototype._get_DPI = function() {
	"use strict";
	var dpi_div = document.getElementById("dpi");
	if (dpi_div != undefined) {
		//show("dpiX="+dpi_div.offsetWidth+", dpiY="+dpi_div.offsetHeight);
		if (dpi_div.offsetWidth>0 && dpi_div.offsetHeight>0)
			return Math.round((dpi_div.offsetWidth + dpi_div.offsetHeight) / 2.0);
	}
	//alternative:
	if ('deviceXDPI' in screen)
		return (screen.systemXDPI + screen.systemYDPI) / 2;
	//default:
	return 96;
}

XpraClient.prototype._get_screen_sizes = function() {
	var dpi = this._get_DPI();
	var screen_size = this._get_desktop_size();
	var wmm = Math.round(screen_size[0]*25.4/dpi);
	var hmm = Math.round(screen_size[1]*25.4/dpi);
	var monitor = ["Canvas", 0, 0, screen_size[0], screen_size[1], wmm, hmm];
	var screen = ["HTML", screen_size[0], screen_size[1],
				wmm, hmm,
				[monitor],
				0, 0, screen_size[0], screen_size[1]
			];
	//just a single screen:
	return [screen];
}

XpraClient.prototype._get_encodings = function() {
	if(this.enabled_encodings.length == 0) {
		// return all supported encodings
		return this.supported_encodings;
	} else {
		return this.enabled_encodings;
	}
}

XpraClient.prototype._update_capabilities = function(appendobj) {
	for (var attr in appendobj) {
		this.capabilities[attr] = appendobj[attr];
	}
}

XpraClient.prototype._send_hello = function(challenge_response, client_salt) {
	// make the base hello
	this._make_hello_base()
	// handle a challenge if we need to
	// finish the hello
	this._make_hello()
	// send the packet
	console.log("sending hello");
	this.protocol.send(["hello", this.capabilities]);
}

XpraClient.prototype._make_hello_base = function() {
	this._update_capabilities({
		// version and platform
		"version"					: "0.16.0",
		"platform"					: this._guess_platform(),
		"platform.name"				: this._guess_platform_name(),
		"platform.processor"		: this._guess_platform_processor(),
		"platform.platform"			: navigator.appVersion,
		"namespace"			 		: true,
		"client_type"		   		: "HTML5",
        "encoding.generic" 			: true,
        "username" 					: "html5user",
        "argv" 						: [window.location.href],
        "digest" 					: ["hmac"],
        //compression bits:
		"zlib"						: true,
		"lz4"						: true,
		"lzo"						: false,
		"compression_level"	 		: 1,
		// packet encoders
		"rencode" 					: false,
		"bencode"					: true,
		"yaml"						: false,
    });

    if(this.encryption) {
    	this._update_capabilities({
			// encryption stuff
			"cipher"					: this.encryption,
			"cipher.iv"					: this._get_hex_uuid().slice(0, 16),
			"cipher.key_salt"			: this._get_hex_uuid()+this._get_hex_uuid(),
	        "cipher.key_stretch_iterations"	: 1000,
		});
	}
}

XpraClient.prototype._make_hello = function() {
	this._update_capabilities({
		"share"						: false,
		"auto_refresh_delay"		: 500,
		"randr_notify"				: true,
		"sound.server_driven"		: true,
		"generic_window_types"		: true,
		"server-window-resize"		: true,
		"notify-startup-complete"	: true,
		"generic-rgb-encodings"		: true,
		"window.raise"				: true,
		"encodings"					: this._get_encodings(),
		"raw_window_icons"			: true,
		//rgb24 is not efficient in HTML so don't use it:
		//png and jpeg will need extra code
		//"encodings.core"			: ["rgb24", "rgb32", "png", "jpeg"],
		"encodings.core"			: this.supported_encodings,
		"encodings.rgb_formats"	 	: this.RGB_FORMATS,
		"encoding.generic"	  		: true,
		"encoding.transparency"		: true,
		"encoding.client_options"	: true,
		"encoding.csc_atoms"		: true,
		"encoding.uses_swscale"		: false,
		//video stuff we may handle later:
		"encoding.video_reinit"		: false,
		"encoding.video_scaling"	: false,
		"encoding.full_csc_modes"	: {"h264" : ["YUV420P"]},
		"encoding.x264.YUV420P.profile"	: "baseline",
		//sound (not yet):
		"sound.receive"				: true,
		"sound.send"				: false,
		"sound.decoders"			: ["wav"],
		// encoding stuff
		"compressible_cursors"		: true,
		"encoding.rgb24zlib"		: true,
		"encoding.rgb_zlib"			: true,
		"encoding.rgb_lz4"			: true,
		"windows"					: true,
		//partial support:
		"keyboard"					: true,
		"xkbmap_layout"				: this._get_keyboard_layout(),
		"xkbmap_keycodes"			: this._get_keycodes(),
		"desktop_size"				: this._get_desktop_size(),
		"screen_sizes"				: this._get_screen_sizes(),
		"dpi"						: this._get_DPI(),
		//not handled yet, but we will:
		"clipboard_enabled"			: true,
		"clipboard.want_targets"	: true,
		"clipboard.selections"		: ["CLIPBOARD"],
		"notifications"				: true,
		"cursors"					: true,
		"bell"						: true,
		"system_tray"				: true,
		//we cannot handle this (GTK only):
		"named_cursors"				: false,
		"argv"						: [window.location.href],
		// printing
		"file-transfer" 			: false,
        "printing" 					: false,
	});
}

/*
 * Window callbacks
 */

XpraClient.prototype._new_window = function(wid, x, y, w, h, metadata, override_redirect, client_properties) {
	// each window needs their own DIV that contains a canvas
	var mydiv = document.createElement("div");
	mydiv.id = String(wid);
	var mycanvas = document.createElement("canvas");
	mydiv.appendChild(mycanvas);
	document.body.appendChild(mydiv);
	// set initial sizes
	mycanvas.width = w;
	mycanvas.height = h;
	// create the XpraWindow object to own the new div
	var win = new XpraWindow(this, mycanvas, wid, x, y, w, h,
		metadata,
		override_redirect,
		client_properties,
		this._window_geometry_changed,
		this._window_mouse_move,
		this._window_mouse_click,
		this._window_set_focus,
		this._window_closed
		);
	this.id_to_window[wid] = win;
	if (!override_redirect) {
		if(this.normal_fullscreen_mode) {
			if(win.windowtype == "NORMAL") {
				win.undecorate();
				win.set_maximized(true);
			}
		}
		var geom = win.get_internal_geometry();
		this.protocol.send(["map-window", wid, geom.x, geom.y, geom.w, geom.h, this._get_client_properties(win)]);
		this._window_set_focus(win);
	}
}

XpraClient.prototype._new_window_common = function(packet, override_redirect) {
	var wid, x, y, w, h, metadata;
	wid = packet[1];
	x = packet[2];
	y = packet[3];
	w = packet[4];
	h = packet[5];
	metadata = packet[6];
	if (wid in this.id_to_window)
		throw "we already have a window " + wid;
	if (w<=0 || h<=0) {
		console.error("window dimensions are wrong: "+w+"x"+h);
		w, h = 1, 1;
	}
	var client_properties = {}
	if (packet.length>=8)
		client_properties = packet[7];
	this._new_window(wid, x, y, w, h, metadata, override_redirect, client_properties)
}

XpraClient.prototype._window_closed = function(win) {
	win.client.protocol.send(["close-window", win.wid]);
}

XpraClient.prototype._get_client_properties = function(win) {
	var cp = win.client_properties;
	cp["encodings.rgb_formats"] = this.RGB_FORMATS;
	return cp;
}

XpraClient.prototype._window_geometry_changed = function(win) {
	// window callbacks are called from the XpraWindow function context
	// so use win.client instead of `this` to refer to the client
	var geom = win.get_internal_geometry();
	var wid = win.wid;
	
	if (!win.override_redirect) {
		win.client._window_set_focus(win);
	}
	win.client.protocol.send(["configure-window", wid, geom.x, geom.y, geom.w, geom.h, win.client._get_client_properties(win)]);
}

XpraClient.prototype._window_mouse_move = function(win, x, y, modifiers, buttons) {
	var wid = win.wid;
	win.client.protocol.send(["pointer-position", wid, [x, y], modifiers, buttons]);
}

XpraClient.prototype._window_mouse_click = function(win, button, pressed, x, y, modifiers, buttons) {
	var wid = win.wid;
	// dont call set focus unless the focus has actually changed
	if(win.client.focus != wid) {
		win.client._window_set_focus(win);
	}
	win.client.protocol.send(["button-action", wid, button, pressed, [x, y], modifiers, buttons]);
}

XpraClient.prototype._window_set_focus = function(win) {
	// don't send focus packet for override_redirect windows!
	if(!win.override_redirect) {
		var wid = win.wid;
		win.client.focus = wid;
		win.client.topwindow = wid;
		win.client.protocol.send(["focus", wid, []]);
		//set the focused flag on all windows:
		for (var i in win.client.id_to_window) {
			var iwin = win.client.id_to_window[i];
			iwin.focused = (i==wid);
			iwin.updateFocus();
		}
	}
}

XpraClient.prototype._window_send_damage_sequence = function(wid, packet_sequence, width, height, decode_time) {
	// this function requires wid as arugment because it may be called
	// without a valid client side window
	this.protocol.send(["damage-sequence", packet_sequence, wid, width, height, decode_time]);
}

XpraClient.prototype._sound_start_receiving = function() {
	try {
		this.audio_ctx = AV.Player.fromXpraSource();
	} catch(e) {
		console.error('Could not start audio player:', e);
		return;
	}
	this.audio_ctx.play();
	this.protocol.send(["sound-control", "start", "wav"]);
}

/*
 * packet processing functions start here
 */

XpraClient.prototype._process_open = function(packet, ctx) {
	// call the send_hello function
	ctx._send_hello();
}

XpraClient.prototype._process_close = function(packet, ctx) {
	// call the client's close callback
	ctx.callback_close();
}

XpraClient.prototype._process_disconnect = function(packet, ctx) {
	if(ctx.hello_timer) {
		clearTimeout(ctx.hello_timer);
		ctx.hello_timer = null;
	}
	ctx.callback_close("Disconnect: "+packet[1]+", "+packet[2]);
}

XpraClient.prototype._process_startup_complete = function(packet, ctx) {
	console.log("startup complete");
}

XpraClient.prototype._process_hello = function(packet, ctx) {
	//show("process_hello("+packet+")");
	// clear hello timer
	if(ctx.hello_timer) {
		clearTimeout(ctx.hello_timer);
		ctx.hello_timer = null;
	}
	var hello = packet[1];
	var version = hello["version"];
	try {
		var vparts = version.split(".");
		var vno = [];
		for (var i=0; i<vparts.length;i++) {
			vno[i] = parseInt(vparts[i]);
		}
		if (vno[0]<=0 && vno[1]<10) {
			ctx.callback_close("unsupported version: " + version);
			ctx.close();
			return;
		}
	}
	catch (e) {
		ctx.callback_close("error parsing version number '" + version + "'");
		ctx.close();
		return;
	}
	console.log("got hello: server version "+version+" accepted our connection");
	//figure out "alt" and "meta" keys:
	if ("modifier_keycodes" in hello) {
		var modifier_keycodes = hello["modifier_keycodes"];
		for (var mod in modifier_keycodes) {
			//show("modifier_keycode["+mod+"]="+modifier_keycodes[mod].toSource());
			var keys = modifier_keycodes[mod];
			for (var i=0; i<keys.length; i++) {
				var key = keys[i];
				//the first value is usually the integer keycode,
				//the second one is the actual key name,
				//doesn't hurt to test both:
				for (var j=0; j<key.length; j++) {
					if ("Alt_L"==key[j])
						ctx.alt_modifier = mod;
					if ("Meta_L"==key[j])
						ctx.meta_modifier = mod;
				}
			}
		}
	}
	//show("alt="+alt_modifier+", meta="+meta_modifier);
	// stuff that must be done after hello
	if(ctx.audio_enabled) {
		ctx._sound_start_receiving();
	}
	
}

XpraClient.prototype._process_ping = function(packet, ctx) {
	var echotime = packet[1];
	var l1=0, l2=0, l3=0;
	ctx.protocol.send(["ping_echo", echotime, l1, l2, l3, 0]);
}

XpraClient.prototype._process_new_window = function(packet, ctx) {
	ctx._new_window_common(packet, false);
}

XpraClient.prototype._process_new_override_redirect = function(packet, ctx) {
	ctx._new_window_common(packet, true);
}

XpraClient.prototype._process_window_metadata = function(packet, ctx) {
	var wid = packet[1],
		metadata = packet[2],
		win = ctx.id_to_window[wid];
	win.update_metadata(metadata);
}

XpraClient.prototype._process_lost_window = function(packet, ctx) {
	var wid = packet[1];
	var win = ctx.id_to_window[wid];
	if (win!=null) {
		win.destroy();
	}
}

XpraClient.prototype._process_raise_window = function(packet, ctx) {
	var wid = packet[1];
	var win = ctx.id_to_window[wid];
	if (win!=null) {
		ctx._window_set_focus(win);
	}
}

XpraClient.prototype._process_window_resized = function(packet, ctx) {
	var wid = packet[1];
	var width = packet[2];
	var height = packet[3];
	var win = ctx.id_to_window[wid];
	if (win!=null) {
		win.resize(width, height);
	}
}

XpraClient.prototype._process_draw = function(packet, ctx) {
	var start = new Date().getTime(),
		wid = packet[1],
		x = packet[2],
		y = packet[3],
		width = packet[4],
		height = packet[5],
		coding = packet[6],
		data = packet[7],
		packet_sequence = packet[8],
		rowstride = packet[9],
		options = {};
	if (packet.length>10)
		options = packet[10];
	if (coding in ctx.OLD_ENCODING_NAMES_TO_NEW)
		coding = ctx.OLD_ENCODING_NAMES_TO_NEW[coding];
	var win = ctx.id_to_window[wid];
	var decode_time = -1;
	if (win) {
		// win.paint draws the update to the window's off-screen buffer and returns true if it
		// was changed.
		win.paint(x, y,
			width, height,
			coding, data, packet_sequence, rowstride, options,
			function (ctx) {
				decode_time = new Date().getTime() - start;
				ctx._window_send_damage_sequence(wid, packet_sequence, width, height, decode_time);
			}
		);
		// request that drawing to screen takes place at next available opportunity if possible
		if(requestAnimationFrame) {
			requestAnimationFrame(function() {
				win.draw();
			});
		} else {
			// requestAnimationFrame is not available, draw immediately
			win.draw();
		}
	}
}

XpraClient.prototype._process_sound_data = function(packet, ctx) {
	if(packet[3]["start-of-stream"] == 1) {
		console.log("start of stream");
	} else {
		ctx.audio_ctx.asset.source._on_data(packet[2]);
		console.log(ctx.audio_ctx.format);
	}
}

XpraClient.prototype._process_clipboard_token = function(packet, ctx) {
	// only accept some clipboard types
	if(ctx.clipboard_targets.indexOf(packet[3])>=0) {
		// we should probably update our clipboard buffer
		ctx.clipboard_buffer = packet[7];
		// prompt user
		prompt("Text was placed on the remote clipboard:", packet[7]);
	}
}

XpraClient.prototype._process_set_clipboard_enabled = function(packet, ctx) {
	console.warn("server set clipboard state to "+packet[1]+" reason was: "+packet[2]);
}

XpraClient.prototype._process_clipboard_request = function(packet, ctx) {
	var request_id = packet[1],
		selection = packet[2],
		target = packet[3];

	if(this.clipboard_buffer == "") {
		packet = ["clipboard-contents-none", request_id, selection];
	} else {
		var packet = ["clipboard-contents", request_id, selection, "UTF8_STRING", 8, "bytes", ctx.clipboard_buffer];
	}

	ctx.protocol.send(packet);
}