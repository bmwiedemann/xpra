#!/bin/bash

VERSION=$1
if [ -z "${VERSION}" ]; then
	VERSION=`PYTHONPATH="./src" python -c "from xpra import __version__; print(__version__)"`
fi
DIR=xpra-${VERSION}
rm -fr "${DIR}"
rm -f "src/xpra/build_info.py"
rm -f "src/xpra/src_info.py"
#record current svn info into xpra/src_info.py:
pushd src
PYTHONPATH="." python -c "from add_build_info import record_src_info;record_src_info()"
svn info > ./svn-info
svnversion > ./svn-version
popd
cp -apr src ${DIR}
pushd "${DIR}"
rm -fr "dist" "build" "install" "MANIFEST"
./setup.py clean
popd
find ${DIR} -name ".svn" -exec rm -fr {} \; 2>&1 | grep -v "No such file or directory"
find ${DIR} -name ".pyc" -exec rm -fr {} \;
find ${DIR} -name "__pycache__" -exec rm -fr {} \; 2>&1 | grep -v "No such file or directory"
find ${DIR} -name "*.pyc" -exec rm -fr {} \;

RAW_SVN_VERSION=`svnversion`
SVN_REVISION=`python -c "x=\"$RAW_SVN_VERSION\";y=x.split(\":\");y.reverse();z=y[0];print \"\".join([c for c in z if c in \"0123456789\"])"`
MODULE_DIRS="xpra"
echo "adding svn revision ${SVN_REVISION} to __init__.py in ${MODULE_DIRS}"
for module in ${MODULE_DIRS}; do
	file="${DIR}/${module}/__init__.py"
	sed -i -e "s+unknown+${SVN_REVISION}+" "${file}"
done

tar -jcf ${DIR}.tar.bz2 ${DIR}
tar -Jcf ${DIR}.tar.xz ${DIR}
for a in ${DIR}.tar.bz2 ${DIR}.tar.xz; do
	md5sum ${a} > ${a}.md5
	sha1sum ${a} > ${a}.sha
done
ls -al ${DIR}.tar.*
rm -fr "${DIR}"
