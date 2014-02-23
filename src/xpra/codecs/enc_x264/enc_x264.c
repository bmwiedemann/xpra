/* This file is part of Xpra.
 * Copyright (C) 2012 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
 * Copyright (C) 2012, 2013 Antoine Martin <antoine@devloop.org.uk>
 * Xpra is released under the terms of the GNU GPL v2, or, at your option, any
 * later version. See the file COPYING for details.
 */

//NOTE: this file is only here because accessing those structures
//from Cython would be too tedious!

#include <stdint.h>
#include <inttypes.h>

#include <x264.h>


const char * const *get_preset_names(void) {
	return x264_preset_names;
}
