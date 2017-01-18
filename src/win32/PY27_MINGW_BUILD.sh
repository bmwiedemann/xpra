#!/bin/bash
# coding=utf8
# This file is part of Xpra.
# Copyright (C) 2017 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

ARGS=$@
DO_CLEAN=${DO_CLEAN:-0}
DO_ZIP=${DO_ZIP:-0}
DO_INSTALLER=${DO_INSTALLER:-1}
RUN_INSTALLER=${RUN_INSTALLER:-1}
DO_MSI=${DO_MSI:-0}
BUNDLE_PUTTY=${BUNDLE_PUTTY:-1}
BUNDLE_OPENSSL=${BUNDLE_OPENSSL:-1}
DIST="./dist"
BUILD_OPTIONS="--without-enc_x265"


################################################################################
# Get version information, generate filenames

#figure out the full xpra version:
VERSION=`python2.7.exe -c "from xpra import __version__;import sys;sys.stdout.write(__version__)"`
SVN_VERSION=`svnversion`
REVISION=`python -c "x=\"$SVN_VERSION\";y=x.split(\":\");y.reverse();z=y[0];print \"\".join([c for c in z if c in \"0123456789\"])"`
FULL_VERSION=${VERSION}-r${REVISION}
EXTRA_VERSION=""
BUILD_TYPE=""
if [ "${MSYSTEM_CARCH}" != "i686" ]; then
	BUILD_TYPE="-${MSYSTEM_CARCH}"
fi
echo
echo "Xpra${EXTRA_VERSION} ${FULL_VERSION}"
echo

INSTALLER_FILENAME="Xpra${EXTRA_VERSION}${BUILD_TYPE}_Setup_${FULL_VERSION}.exe"
MSI_FILENAME="Xpra${EXTRA_VERSION}${BUILD_TYPE}_Setup_${FULL_VERSION}.msi"
ZIP_DIR="Xpra${EXTRA_VERSION}${BUILD_TYPE}_${FULL_VERSION}"
ZIP_FILENAME="${ZIP_DIR}.zip"


################################################################################
# Build: clean, build extensions, generate exe directory

echo "* cleaning ${DIST} output directory"
rm -fr ${DIST}/*
mkdir ${DIST} >& /dev/null

if [ "${DO_CLEAN}" == "1" ]; then
	rm -fr "build"
fi

echo "* Building Python 2.7 Cython modules"
BUILD_LOG="win32/Python2.7-build.log"
python2.7.exe ./setup.py build_ext ${BUILD_OPTIONS} --inplace >& ${BUILD_LOG}
if [ "$?" != "0" ]; then
	echo "ERROR: build failed, see ${BUILD_LOG}:"
	tail -n 20 "${BUILD_LOG}"
	exit 1
fi

# For building Python 3.x Sound sub-app (broken because of cx_Freeze bugs)
#echo "* Building Python 3.4 Cython modules (see win32/Python2.7-build.log)"
#python2.7.exe ./setup.py build_ext ${BUILD_OPTIONS} --inplace >& win32/Python2.7-build.log

echo "* generating installation directory"
CX_FREEZE_LOG="win32/cx_freeze-install.log"
python2.7.exe ./setup.py install_exe ${BUILD_OPTIONS} --install=${DIST} >& ${CX_FREEZE_LOG}
if [ "$?" != "0" ]; then
	echo "ERROR: build failed, see ${CX_FREEZE_LOG}:"
	tail -n 20 "${CX_FREEZE_LOG}"
	exit 1
fi

if [ -e "${DIST}/OpenGL" ]; then
	echo "* adding PyOpenGL to library.zip"
	pushd "${DIST}" >& /dev/null
	zip -qmor "library.zip" OpenGL
	popd >& /dev/null
	#python2.7.exe win32\move_to_zip.py ${DIST}\library.zip ${DIST} OpenGL
fi

echo "* Generating gdk pixbuf loaders.cache"
gdk-pixbuf-query-loaders.exe "dist/lib/gdk-pixbuf-2.0/2.10.0/loaders/*" | sed 's+".*dist/+"+g' > dist/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache

echo "* Generating HTML Manual Page"
groff.exe -mandoc -Thtml < man/xpra.1 > ${DIST}/manual.html

if [ "${BUNDLE_PUTTY}" == "1" ]; then
	echo "* Adding TortoisePlink"
	TORTOISESVN="/c/Program Files/TortoiseSVN"
	if [ ! -e "${TORTOISESVN}" ]; then
		TORTOISESVN="/c/Program Files\(x86\)/TortoiseSVN"
		if [ ! -e "${TORTOISESVN}" ]; then
			echo "Missing TortoiseSVN!"
			exit 1
		fi
	fi
	cp "${TORTOISESVN}/bin/TortoisePlink.exe" "${DIST}/Plink.exe"
	#are we meant to include those DLLs?
	#rsync -rplogt "${TORTOISESVN}/bin/"*dll "${DIST}/"
fi

if [ "${BUNDLE_OPENSSL}" == "1" ]; then
	cp "${MINGW_PREFIX}/bin/openssl.exe" "${DIST}/"
	#use the old filename so we don't have to change the xpra.iss and the py2exe+MSVC build system:
	cp "${MINGW_PREFIX}/ssl/openssl.cnf" "${DIST}/openssl.cfg"
fi


################################################################################
# packaging: ZIP / EXE / MSI

if [ "${DO_ZIP}" == "1" ]; then
	echo "* Creating ZIP file:"
	rm -fr "${ZIP_DIR}" "${ZIP_FILENAME}"
	mkdir "${ZIP_DIR}"
	rsync -rplogt "${DIST}"/* "${ZIP_DIR}"
	zip -qmr "${ZIP_FILENAME}" "${ZIP_DIR}"
	ls -la "${ZIP_FILENAME}"
fi

if [ "${DO_INSTALLER}" == "1" ]; then
	INNOSETUP="/c/Program Files/Inno Setup 5/ISCC.exe"
	if [ ! -e "${INNOSETUP}" ]; then
		INNOSETUP="c/Program Files\(x86\)/Inno Setup 5/ISCC.exe"
		if [ ! -e "${INNOSETUP}" ]; then
			echo "cannot find InnoSetup"
			exit 1
		fi
	fi
	INNOSETUP_LOG="win32/innosetup.log"
	echo "* Creating the installer using InnoSetup"
	rm -f "Xpra_Setup.exe" "${INSTALLER_FILENAME}" "${INNOSETUP_LOG}"
	cp "win32/xpra.iss" "xpra.iss"
	"${INNOSETUP}" "xpra.iss" >& "${INNOSETUP_LOG}"
	rm "xpra.iss"
	if [ "$?" != "0" ]; then
		echo "InnoSetup error - see ${INNOSETUP_LOG}:"
		tail -n 20 "${INNOSETUP_LOG}"
		exit 1
	fi
	mv "Output\Xpra_Setup.exe" "${INSTALLER_FILENAME}"

	if [ "${RUN_INSTALLER}" == "1" ]; then
		echo "* Finished - running the new installer"
		#we need to escape slashes!
		#(this doesn't preserve spaces.. we should use shift instead)
		CMD_ARGS=`echo ${ARGS} | sed 's+/+//+g'`
		"./${INSTALLER_FILENAME}" "${CMD_ARGS}"
	fi
	ls -la "${INSTALLER_FILENAME}"
fi

if [ "${DO_MSI}" == "1" ]; then
	MSIWRAPPER="/c/Program Files/MSI Wrapper/MsiWrapper.exe"
	if [ ! -e "${MSIWRAPPER}" ]; then
		MSIWRAPPER="/c/Program Files\(x86\)/MSI Wrapper/MsiWrapper.exe"
		if [ ! -e "${MSIWRAPPER}" ]; then
			echo "cannot find MSI Wrapper"
			exit 1
		fi
	fi
	ZERO_PADDED_VERSION=`python2.7.exe -c 'from xpra import __version__;print(".".join((__version__.split(".")+["0","0","0"])[:4]))'`
	cat "win32\msi.xml" | sed "s/INPUT/${INSTALLER_FILENAME}/g" | sed "s/OUTPUT/${MSI_FILENAME}/g" | sed "s/ZERO_PADDED_VERSION/${ZERO_PADDED_VERSION}/g" | sed "s/FULL_VERSION/${FULL_VERSION}/g" > msi.xml
	"${MSIWRAPPER}" "msi.xml"
fi
