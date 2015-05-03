#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2014 Antoine Martin <antoine@devloop.org.uk>

#need to find a generic way to discover tests
#that works with python2.6 without introducing more dependencies
#until then... this hack will do
#runs all the files in "unit/" that end in "test.py"

def main():
    import sys
    import os.path
    import subprocess
    p = os.path.abspath(os.path.dirname(__file__))
    #ie: p=~/Xpra/trunk/src/tests/unit
    root = os.path.dirname(p)
    #ie: d=~/Xpra/trunk/src/tests
    sys.path.append(root)
    #now look for tests to run
    def run_file(p):
        #ie: "~/projects/Xpra/trunk/src/tests/unit/version_util_test.py"
        assert p.startswith(root) and p.endswith("test.py")
        #ie: "unit.version_util_test"
        name = p[len(root)+1:-3].replace(os.path.sep, ".")
        print("running %s" % name)
        cmd = ["python%s" % sys.version_info[0], p]
        try:
            proc = subprocess.Popen(cmd)
        except:
            print("failed to execute %s" % p)
            sys.exit(1)
        assert proc.wait()==0, "failure on %s" % name
    def add_recursive(d):
        paths = os.listdir(d)
        for path in paths:
            p = os.path.join(d, path)
            if os.path.isfile(p) and p.endswith("test.py"):
                run_file(p)
            elif os.path.isdir(p):
                fp = os.path.join(d, p)
                add_recursive(fp)
    print("running all the tests in %s" % p)
    add_recursive(p)

if __name__ == '__main__':
    main()
