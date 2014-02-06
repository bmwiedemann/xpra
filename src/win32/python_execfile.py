#!/usr/bin/env python

# simple wrapper script so we can launch a script file with the same python interpreter
# and environment which is used by the xpra.exe / xpra_cmd.exe process.
#
# normally, the py2exe win32 platform code redirects the output to a log file
# this script disables that.

import os.path
import sys
from xpra.platform.win32 import set_redirect_output
set_redirect_output(False)

try:
    import win32api                     #@UnresolvedImport
    win32api.SetConsoleTitle("Python")
except:
    pass


if len(sys.argv)<2:
    print("you must specify a python script file to run!")
    sys.exit(1)
filename = sys.argv[1]
if not os.path.exists(filename):
    print("script file '%s' not found" % filename)
    sys.exit(1)
execfile(filename)
sys.exit(0)
