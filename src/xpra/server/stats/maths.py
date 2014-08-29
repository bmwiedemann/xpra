# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2012 - 2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

# Accelerated math functions used for inspecting/averaging lists of statistics
# see ServerSource, WindowSource and batch_delay_calculator

from xpra.server.stats.cymaths import (logp,                #@UnresolvedImport @UnusedImport
                      calculate_time_weighted_average,      #@UnresolvedImport @UnusedImport
                      time_weighted_average,                #@UnresolvedImport @UnusedImport
                      calculate_timesize_weighted_average,  #@UnresolvedImport @UnusedImport
                      calculate_for_target,                 #@UnresolvedImport @UnusedImport
                      calculate_for_average, queue_inspect) #@UnresolvedImport @UnusedImport
