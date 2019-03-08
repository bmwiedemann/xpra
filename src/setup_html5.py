#!/usr/bin/env python

# This file is part of Xpra.
# Copyright (C) 2017-2019 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file LICENSE for details.

import sys
import os.path
import shutil


def glob_recurse(srcdir):
    m = {}
    for root, _, files in os.walk(srcdir):
        for f in files:
            dirname = root[len(srcdir)+1:]
            filename = os.path.join(root, f)
            m.setdefault(dirname, []).append(filename)
    return m

def get_status_output(*args, **kwargs):
    import subprocess
    kwargs["stdout"] = subprocess.PIPE
    kwargs["stderr"] = subprocess.PIPE
    try:
        p = subprocess.Popen(*args, **kwargs)
    except Exception as e:
        print("error running %s,%s: %s" % (args, kwargs, e))
        return -1, "", ""
    stdout, stderr = p.communicate()
    return p.returncode, stdout.decode("utf-8"), stderr.decode("utf-8")


def install_symlink(symlink_options, dst):
    for symlink_option in symlink_options:
        if symlink_option.find("*"):
            import glob
            #this is a glob, find at least one match:
            matches = glob.glob(symlink_option)
            if matches:
                symlink_option = matches[0]
            else:
                continue
        if os.path.exists(symlink_option):
            print("symlinked %s from %s" % (dst, symlink_option))
            if os.path.exists(dst):
                os.unlink(dst)
            os.symlink(symlink_option, dst)
            return True
    #print("no symlinks found for %s from %s" % (dst, symlink_options))
    return False

def install_html5(install_dir="www", minifier="uglifyjs", gzip=True, brotli=True, verbose=False, extra_symlinks={}):
    if minifier:
        print("minifying html5 client to '%s' using %s" % (install_dir, minifier))
    else:
        print("copying html5 client to '%s'" % (install_dir, ))
    #those are used to replace the file we ship in source form
    #with one that is maintained by the distribution:
    symlinks = {
        "jquery.js"     : [
            "/usr/share/javascript/jquery/jquery.js",
            "/usr/share/javascript/jquery/3/jquery.js",
            ],
        "jquery-ui.js"     : [
            "/usr/share/javascript/jquery-ui/jquery-ui.js",
            "/usr/share/javascript/jquery-ui/3/jquery-ui.js",
            ],
        }
    for k,files in glob_recurse("html5").items():
        if (k!=""):
            k = os.sep+k
        for f in files:
            src = os.path.join(os.getcwd(), f)
            parts = f.split(os.path.sep)
            if parts[0]=="html5":
                f = os.path.join(*parts[1:])
            if install_dir==".":
                install_dir = os.getcwd()
            dst = os.path.join(install_dir, f)
            if os.path.exists(dst):
                os.unlink(dst)
            #try to find an existing installed library and symlink it:
            symlink_options = symlinks.get(os.path.basename(f), [])
            if install_symlink(symlink_options, dst):
                #we've created a symlink, skip minification and compression
                continue
            ddir = os.path.split(dst)[0]
            if ddir and not os.path.exists(ddir):
                os.makedirs(ddir, 0o755)
            ftype = os.path.splitext(f)[1].lstrip(".")
            if minifier and ftype=="js":
                if minifier=="uglifyjs":
                    minify_cmd = ["uglifyjs",
                                  "--screw-ie8",
                                  src,
                                  "-o", dst,
                                  "--compress",
                                  ]
                else:
                    assert minifier=="yuicompressor"
                    import yuicompressor        #@UnresolvedImport
                    jar = yuicompressor.get_jar_filename()
                    java_cmd = os.environ.get("JAVA", "java")
                    minify_cmd = [java_cmd, "-jar", jar,
                                  src,
                                  "--nomunge",
                                  "--line-break", "400",
                                  "--type", ftype,
                                  "-o", dst,
                                  ]
                r = get_status_output(minify_cmd)[0]
                if r!=0:
                    print("Error: failed to minify '%s', command returned error %i" % (f, r))
                    if verbose:
                        print(" command: %s" % (minify_cmd,))
                else:
                    print("minified %s" % (f, ))
            else:
                r = -1
            if r!=0:
                shutil.copyfile(src, dst)
                os.chmod(dst, 0o644)
            if ftype not in ("png", ):
                if gzip:
                    gzip_dst = "%s.gz" % dst
                    if os.path.exists(gzip_dst):
                        os.unlink(gzip_dst)
                    cmd = ["gzip", "-f", "-n", "-9", "-k", dst]
                    get_status_output(cmd)
                    if os.path.exists(gzip_dst):
                        os.chmod(gzip_dst, 0o644)
                if brotli:
                    br_dst = "%s.br" % dst
                    if os.path.exists(br_dst):
                        os.unlink(br_dst)
                    #find brotli on $PATH
                    paths = os.environ.get("PATH", "").split(os.pathsep)
                    if os.name=="posix":
                        #not always present,
                        #but brotli is often installed there (install from source):
                        paths.append("/usr/local/bin")
                    for x in paths:
                        br = os.path.join(x, "brotli")
                        if sys.platform.startswith("win"):
                            br += ".exe"
                        if not os.path.exists(br):
                            continue
                        cmd = [br, "-k", dst]
                        code, out, err = get_status_output(cmd)
                        if code!=0:
                            print("brotli error code=%i on %s" % (code, cmd))
                            if out:
                                print("stdout=%s" % out)
                            if err:
                                print("stderr=%s" % err)
                        elif os.path.exists(br_dst):
                            os.chmod(br_dst, 0o644)
                            break
                        else:
                            print("Warning: brotli did not create '%s'" % br_dst)

    if os.name=="posix":
        try:
            from xpra.platform.paths import get_desktop_background_paths
        except ImportError as e:
            print("cannot locate desktop background: %s" % (e,))
        else:
            paths = get_desktop_background_paths()
            print("desktop background paths: %s" % (paths,))
            if paths:
                extra_symlinks = {"background.png" : paths}
                for f, symlink_options in extra_symlinks.items():
                    dst = os.path.join(install_dir, f)
                    install_symlink(symlink_options, dst)


def main():
    if len(sys.argv)==1:
        install_dir = os.path.join(sys.prefix, "share/xpra/www")
    elif len(sys.argv)==2:
        install_dir = sys.argv[1]
    else:
        print("invalid number of arguments: %i" % len(sys.argv))
        print("usage:")
        print("%s [installation-directory]" % sys.argv[0])
        sys.exit(1)

    install_html5(install_dir)

if __name__ == "__main__":
    main()
