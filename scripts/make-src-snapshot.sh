#!/bin/bash

VERSION=$1
if [ -z "${VERSION}" ]; then
	VERSION=`PYTHONPATH="./src" python -c "from xpra import __version__; print(__version__)"`
fi
DIR=xpra-${VERSION}
rm -fr "${DIR}"
rm -f "src/xpra/build_info.py"
cp -apr src ${DIR}
rm -fr "${DIR}/dist"
rm -fr "${DIR}/build"
rm -fr "${DIR}/install"
rm -fr "${DIR}/Output"
rm -fr "${DIR}/clean.sh"
rm -f "${DIR}/MANIFEST"
rm -f "${DIR}/xpra/wait_for_x_server.c"
rm -f "${DIR}/xpra/vpx/codec.c"
rm -f "${DIR}/xpra/x264/codec.c"
rm -f "${DIR}/xpra/x264/win32/codec.pyd" "${DIR}/xpra/x264/win32/x264lib.dll" "${DIR}/xpra/x264/win32/x264lib.lib"
rm -f "${DIR}/xpra/vpx/codec.c"
rm -f "${DIR}/xpra/vpx/win32/codec.pyd"
rm -f "${DIR}/wimpiggy/lowlevel/bindings.c"
rm -f "${DIR}/wimpiggy/lowlevel/wimpiggy.lowlevel.bindings.dep"
rm -f "${DIR}/wimpiggy/lowlevel/constants.pxi"
pushd "${DIR}"
svn info > ./svn-info
popd
find ${DIR} -name ".svn" -exec rm -fr {} \; 2>&1 | grep -v "No such file or directory"
find ${DIR} -name ".pyc" -exec rm -fr {} \;
find ${DIR} -name "__pycache__" -exec rm -fr {} \; 2>&1 | grep -v "No such file or directory"
find ${DIR} -name "*.pyc" -exec rm -fr {} \;

RAW_SVN_VERSION=`svnversion`
SVN_REVISION=`python -c "x=\"$RAW_SVN_VERSION\";y=x.split(\":\");y.reverse();z=y[0];print \"\".join([c for c in z if c in \"0123456789\"])"`
MODULE_DIRS="xpra wimpiggy parti"
echo "adding svn revision ${SVN_REVISION} to __init__.py in ${MODULE_DIRS}"
for module in ${MODULE_DIRS}; do
	file="${DIR}/${module}/__init__.py"
	sed -i -e "s+unknown+${SVN_REVISION}+" "${file}"
done

tar -jcf ${DIR}.tar.bz2 ${DIR}
md5sum ${DIR}.tar.bz2 > ${DIR}.tar.bz2.md5
sha1sum ${DIR}.tar.bz2 > ${DIR}.tar.bz2.sha
ls -al ${DIR}.tar.bz2*
rm -fr "${DIR}"
