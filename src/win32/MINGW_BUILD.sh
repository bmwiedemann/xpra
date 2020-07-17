#!/bin/bash
# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2017-2018 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

ARGS=$@
DO_CLEAN=${DO_CLEAN:-1}
DO_CUDA=${DO_CUDA:-1}
DO_ZIP=${DO_ZIP:-0}
DO_INSTALLER=${DO_INSTALLER:-1}
DO_TESTS=${DO_TESTS:-1}
DO_VERPATCH=${DO_VERPATCH:-1}
DO_SERVICE=${DO_SERVICE:-1}
CLIENT_ONLY=${CLIENT_ONLY:-0}
RUN_INSTALLER=${RUN_INSTALLER:-1}
DO_MSI=${DO_MSI:-0}
DO_SIGN=${DO_SIGN:-1}
BUNDLE_PUTTY=${BUNDLE_PUTTY:-1}
BUNDLE_OPENSSH=${BUNDLE_OPENSSH:-0}
BUNDLE_OPENSSL=${BUNDLE_OPENSSL:-1}
BUNDLE_PAEXEC=${BUNDLE_PAEXEC:-1}
BUNDLE_DESKTOPLOGON=${BUNDLE_DESKTOPLOGON:-1}
ZIP_MODULES=${ZIP_MODULES:-1}

PYTHON=python3

KEY_FILE="E:\\xpra.pfx"
DIST="./dist"
BUILD_OPTIONS="--without-enc_x265 --without-cuda_rebuild"

if [ "${CLIENT_ONLY}" == "1" ]; then
	DO_TESTS="0"
	DO_SERVICE="0"
	BUNDLE_PAEXEC="0"
	BUNDLE_DESKTOPLOGON="0"
	BUILD_OPTIONS="${BUILD_OPTIONS} --without-enc_x264 --without-nvenc --without-nvfbc"
	shift
fi

################################################################################
# get paths and compilation options:
PROGRAMFILES_X86="C:\\Program Files (x86)"

if [ "${CLIENT_ONLY}" == "1" ]; then
	BUILD_OPTIONS="${BUILD_OPTIONS} --without-server --without-shadow --without-proxy --without-html5 --without-rfb"
else
	# Find a java interpreter we can use for the html5 minifier
	$JAVA -version >& /dev/null
	if [ "$?" != "0" ]; then
		#try my hard-coded default first to save time:
		export JAVA="C:\Program Files/Java/jdk1.8.0_121/bin/java.exe"
		if [ ! -e "${JAVA}" ]; then
			export JAVA=`find "${PROGRAMFILES}/Java" "${PROGRAMFILES}" "${PROGRAMFILES_X86}" -name "java.exe" 2> /dev/null | head -n 1`
		fi
	fi
fi

SIGNTOOL="${PROGRAMFILES}\\Microsoft SDKs\\Windows\\v7.1\\Bin\\signtool.exe"
if [ ! -e "${SIGNTOOL}" ]; then
	SIGNTOOL="${PROGRAMFILES}\\Microsoft SDKs\\Windows\\v7.1A\\Bin\\signtool.exe"
fi
if [ ! -e "${SIGNTOOL}" ]; then
	SIGNTOOL="${PROGRAMFILES_X86}\\Windows Kits\\8.1\\Bin\\x64\\signtool.exe"
fi
if [ ! -e "${SIGNTOOL}" ]; then
	SIGNTOOL="${PROGRAMFILES_X86}\\Windows Kits\\10\\App Certification Kit\\signtool.exe"
fi
if [ ! -e "${SIGNTOOL}" ]; then
	SIGNTOOL=`find /c/Program\ Files* -wholename "*/x64/signtool.exe"`
fi
if [ -e "${SIGNTOOL}" ]; then
	cp "$SIGNTOOL" ./
fi

################################################################################
# Get version information, generate filenames

#record in source tree:
rm xpra/src_info.py xpra/build_info.py >& /dev/null
${PYTHON} add_build_info.py >& /dev/null
if [ "$?" != "0" ]; then
	echo "ERROR: recording build info"
	exit 1
fi

#figure out the full xpra version:
PYTHON_VERSION=`${PYTHON} --version | awk '{print $2}'`
echo "Python version ${PYTHON_VERSION}"
VERSION=`${PYTHON} -c "from xpra import __version__;import sys;sys.stdout.write(__version__)"`
REVISION=`${PYTHON} -c "from xpra.src_info import REVISION;import sys;sys.stdout.write(str(REVISION))"`
NUMREVISION=`${PYTHON} -c "from xpra.src_info import REVISION;import sys;sys.stdout.write(str(REVISION).rstrip('M'))"`
ZERO_PADDED_VERSION=`${PYTHON} -c 'from xpra import __version__;print(".".join((__version__.split(".")+["0","0","0"])[:3]))'`".${NUMREVISION}"
LOCAL_MODIFICATIONS=`${PYTHON} -c "from xpra.src_info import LOCAL_MODIFICATIONS;import sys;sys.stdout.write(str(LOCAL_MODIFICATIONS))"`
FULL_VERSION=${VERSION}-r${REVISION}
if [ "${LOCAL_MODIFICATIONS}" != "0" ]; then
	FULL_VERSION="${FULL_VERSION}M"
fi
EXTRA_VERSION=""
if [ "${CLIENT_ONLY}" == "1" ]; then
	EXTRA_VERSION="-Client"
	DO_CUDA="0"
fi
echo
echo -n "Xpra${EXTRA_VERSION} ${FULL_VERSION}"
if [ "${MSYSTEM_CARCH}" == "i686" ]; then
	BUILD_TYPE=""
	DO_CUDA="0"
	APPID="Xpra32bit"
	BITS="32"
else
	BUILD_TYPE="-${MSYSTEM_CARCH}"
	echo " (64-bit)"
	APPID="Xpra_is1"
	BITS="64"
fi
BUILD_TYPE="-Python3${BUILD_TYPE}"
echo
echo

INSTALLER_FILENAME="Xpra${EXTRA_VERSION}${BUILD_TYPE}_Setup_${FULL_VERSION}.exe"
MSI_FILENAME="Xpra${EXTRA_VERSION}${BUILD_TYPE}_${FULL_VERSION}.msi"
ZIP_DIR="Xpra${EXTRA_VERSION}${BUILD_TYPE}_${FULL_VERSION}"
ZIP_FILENAME="${ZIP_DIR}.zip"


################################################################################
# Build: clean, build extensions, generate exe directory

echo "* Cleaning ${DIST} output directory"
rm -fr ${DIST}/*
mkdir ${DIST} >& /dev/null

if [ "${DO_CLEAN}" == "1" ]; then
	rm -fr "build"
	#clean sometimes errors on removing pyd files,
	#so do it with rm instead:
	find xpra/ -name "*-cpython-*dll" -exec rm -f {} \;
	CLEAN_LOG="clean.log"
	${PYTHON} ./setup.py clean >& "${CLEAN_LOG}"
	if [ "$?" != "0" ]; then
		echo "ERROR: clean failed, see ${CLEAN_LOG}:"
		tail -n 20 "${CLEAN_LOG}"
		exit 1
	fi
fi

if [ "${DO_SERVICE}" == "1" ]; then
	echo "* Compiling system service shim"
	if [ "${BITS}" == "64" ]; then
		ARCH_DIRS="x64 x86"
	else
		ARCH_DIRS="x86"
	fi
	pushd "win32/service" > /dev/null
	#the proper way would be to run vsvars64.bat
	#but we only want to locate 3 commands,
	#so we find them "by hand":
	rm -f event_log.rc event_log.res MSG00409.bin Xpra-Service.exe
	for KIT_DIR in "C:\Program Files\\Windows Kits" "C:\\Program Files (x86)\\Windows Kits"; do
		for V in 8.1 10; do
			VKIT_BIN_DIR="${KIT_DIR}\\$V\\bin"
			if [ ! -d "${VKIT_BIN_DIR}" ]; then
				continue
			fi
			for B in $ARCH_DIRS; do
				MC="${VKIT_BIN_DIR}\\$B\\mc.exe"
				RC="${VKIT_BIN_DIR}\\$B\\rc.exe"
				if [ ! -e "$MC" ]; then
					#try to find it in a versionned subdir:
					MC=`find "${VKIT_BIN_DIR}" -name "mc.exe" | grep "$B/"`
					RC=`find "${VKIT_BIN_DIR}" -name "rc.exe" | grep "$B/"`
				fi
				if [ -e "$MC" ]; then
					echo "  using SDK $V $B found in:"
					echo "  '$KIT_DIR'"
					#echo "  mc=$MC"
					#echo "  rc=$RC"
					break 3
				fi
			done
		done
	done
	for PF in "C:\\Program Files" "C:\\Program Files (x86)"; do
		for VSV in 14.0 17.0 19.0 2019; do
			LINK="$PF\\Microsoft Visual Studio $VSV\\VC\\bin\\link.exe"
			if  [ -e "${LINK}" ]; then
				break 2
			fi
			VSDIR="$PF\\Microsoft Visual Studio\\$VSV\\BuildTools\\VC\\Tools\\MSVC"
			if [ -d "${VSDIR}" ]; then
				for B in $ARCH_DIRS; do
					LINK=`find "$VSDIR" -name "link.exe" | grep "$B/$B"`
					if  [ -e "${LINK}" ]; then
						break 3
					fi
				done
			fi
		done
	done
	#MC="C:\\Program Files\\Windows Kits\\8.1\\bin\\x64\\mc.exe"
	#RC="C:\\Program Files\\Windows Kits\\8.1\\bin\\x64\\mc.exe"
	EVENT_LOG_BUILD_LOG="event_log_build.log"

	#first build the event log definitions:
	"$MC" -U event_log.mc >& "${EVENT_LOG_BUILD_LOG}"
	if [ "$?" != "0" ]; then
		echo "ERROR: service event_log build failed, see ${EVENT_LOG_BUILD_LOG}:"
		tail -n 20 "${EVENT_LOG_BUILD_LOG}"
		exit 1
	fi
	"$RC" event_log.rc > "${EVENT_LOG_BUILD_LOG}"
	if [ "$?" != "0" ]; then
		echo "ERROR: service event_log build failed, see ${EVENT_LOG_BUILD_LOG}:"
		tail -n 20 "${EVENT_LOG_BUILD_LOG}"
		exit 1
	fi
	"$LINK" -dll -noentry -out:event_log.dll event_log.res > "${EVENT_LOG_BUILD_LOG}"
	if [ "$?" != "0" ]; then
		echo "ERROR: service event_log build failed, see ${EVENT_LOG_BUILD_LOG}:"
		tail -n 20 "${EVENT_LOG_BUILD_LOG}"
		exit 1
	fi

	#now build the system service executable:
	g++ -o Xpra-Service.exe Xpra-Service.cpp -Wno-write-strings
	if [ "$?" != "0" ]; then
		echo "ERROR: service build failed"
		exit 1
	fi
	cp -fn Xpra-Service.exe ../../dist/
	popd > /dev/null
fi

if [ "${DO_CUDA}" == "1" ]; then
	echo "* Building CUDA kernels"
	cmd.exe //c "win32\\BUILD_CUDA_KERNEL" BGRA_to_NV12 || exit 1
	cmd.exe //c "win32\\BUILD_CUDA_KERNEL" BGRA_to_YUV444 || exit 1
	cmd.exe //c "win32\\BUILD_CUDA_KERNEL" ARGB_to_NV12 || exit 1
	cmd.exe //c "win32\\BUILD_CUDA_KERNEL" ARGB_to_YUV444 || exit 1
else
	BUILD_OPTIONS="${BUILD_OPTIONS} --without-nvenc"
fi

echo "* Building Python3 Cython modules"
BUILD_LOG="win32/Python3-build.log"
${PYTHON} ./setup.py build_ext ${BUILD_OPTIONS} --inplace >& ${BUILD_LOG}
if [ "$?" != "0" ]; then
	echo "ERROR: build failed, see ${BUILD_LOG}:"
	tail -n 20 "${BUILD_LOG}"
	exit 1
fi

if [ "${DO_TESTS}" == "1" ]; then
	echo "* Running unit tests"
	UNITTEST_LOG="win32/unittest.log"
	PYTHONPATH=.:./unittests XPRA_COMMAND="./scripts/xpra" ${PYTHON} ./unittests/unit/run.py >& ${UNITTEST_LOG}
	if [ "$?" != "0" ]; then
		echo "ERROR: unittests have failed, see ${UNITTEST_LOG}:"
		tail -n 20 "${UNITTEST_LOG}"
		exit 1
	fi
fi

echo "* Generating installation directory"
CX_FREEZE_LOG="win32/cx_freeze-install.log"
${PYTHON} ./setup.py install_exe ${BUILD_OPTIONS} --install=${DIST} >& ${CX_FREEZE_LOG}
if [ "$?" != "0" ]; then
	echo "ERROR: build failed, see ${CX_FREEZE_LOG}:"
	tail -n 20 "${CX_FREEZE_LOG}"
	exit 1
fi
#fix case sensitive mess:
mv ${DIST}/lib/girepository-1.0/Glib-2.0.typelib ${DIST}/lib/girepository-1.0/GLib-2.0.typelib.tmp
mv ${DIST}/lib/girepository-1.0/GLib-2.0.typelib.tmp ${DIST}/lib/girepository-1.0/GLib-2.0.typelib

#fixup cx_Logging, required by the service class before we can patch sys.path to find it:
if [ -e "${DIST}/lib/cx_Logging.pyd" ]; then
	mv "${DIST}/lib/cx_Logging.pyd" "${DIST}/"
fi
#fixup cx freeze wrongly including an empty dir:
rm -fr "${DIST}/lib/comtypes/gen"
#fixup tons of duplicated DLLs, thanks cx_Freeze!
pushd ${DIST} > /dev/null
#why is it shipping those files??
find lib/ -name "*dll.a" -exec rm {} \;
#only keep the actual loaders, not all the other crap cx_Freeze put there:
mkdir lib/gdk-pixbuf-2.0/2.10.0/loaders.tmp
mv lib/gdk-pixbuf-2.0/2.10.0/loaders/libpixbufloader-*.dll lib/gdk-pixbuf-2.0/2.10.0/loaders.tmp/
rm -fr lib/gdk-pixbuf-2.0/2.10.0/loaders
mv lib/gdk-pixbuf-2.0/2.10.0/loaders.tmp lib/gdk-pixbuf-2.0/2.10.0/loaders
#move libs that are likely to be common to the lib dir:
for prefix in lib avcodec avformat avutil swscale swresample zlib1 xvidcore; do
	find lib/Xpra -name "${prefix}*dll" -exec mv {} ./lib/ \;
done
#liblz4 ends up in the wrong place and duplicated,
#keep just one copy in ./lib
find lib/lz4 -name "liblz4.dll" -exec mv {} ./lib/ \;
if [ "${CLIENT_ONLY}" == "1" ]; then
	rm -fr ./lib/numpy
else
	for x in openblas gfortran quadmath; do
		mv -f ./lib/numpy/core/lib$x*.dll ./lib/
		mv -f ./lib/numpy/linalg/lib$x*.dll ./lib/
	done
	#trim tests from numpy
	pushd ./lib/numpy > /dev/null
	rm -fr ./f2py/docs ./tests ./doc
	for x in core distutils f2py lib linalg ma matrixlib oldnumeric polynomial random testing compat fft; do
		rm -fr ./$x/tests
	done
	popd > /dev/null
fi
rm lib/libx265*.dll
mv lib/nacl/libsodium*dll ./lib/
#gstreamer uses its own lib dir, so this does not belong in the root:
mv ./libgst*.dll ./lib/gstreamer-1.0/
#but the main gstreamer lib does:
mv ./lib/gstreamer-1.0/libgstreamer*.dll ./lib/
#and the gstreamer support libraries look like plugins but those are actual DLLs:
mv ./lib/gstreamer-1.0/libgst*-1.0-*.dll ./lib/
#move most DLLs to /lib
mv *dll lib/
#but keep the core DLLs (python, gcc, etc):
cp lib/msvcrt*dll lib/libpython*dll lib/libgcc*dll lib/libwinpthread*dll ./
pushd lib > /dev/null
#remove all the pointless duplication:
for x in `ls *dll`; do
	find ./ -mindepth 2 -name "${x}" -exec rm {} \;
done

#remove test bits we don't need:
rm -fr ./future/backports/test ./comtypes/test/ ./ctypes/macholib/fetch_macholib* ./distutils/tests ./distutils/command ./enum/doc ./websocket/tests ./email/test/
#remove source:
find xpra -name "*.pyx" -exec rm {} \;
find xpra -name "*.c" -exec rm {} \;
find xpra -name "*.cpp" -exec rm {} \;
find xpra -name "*.m" -exec rm {} \;
find xpra -name "constants.txt" -exec rm {} \;
find xpra -name "*.h" -exec rm {} \;
find xpra -name "*.html" -exec rm {} \;
find xpra -name "*.pxd" -exec rm {} \;
find xpra -name "*.cu" -exec rm {} \;
#remove empty directories:
rmdir xpra/*/*/* 2> /dev/null
rmdir xpra/*/* 2> /dev/null
rmdir xpra/* 2> /dev/null
#zip up some modules:
if [ "${ZIP_MODULES}" == "1" ]; then
	#these modules contain native code or data files,
	#so they will require special treatment:
	#xpra numpy cryptography PIL nacl cffi gtk rencode gobject glib > /dev/null
	zip --move -ur library.zip OpenGL test encodings unittest ldap3 future paramiko html \
			pyasn1 distutils comtypes asn1crypto ldap email multiprocessing \
			pkg_resources pyu2f pycparser idna ctypes json \
			http enum sqlite3 winreg copyreg _thread _dummythread builtins importlib \
			logging queue urllib xml xmlrpc pyasn1_modules concurrent pynvml collections > /dev/null
fi
popd > /dev/null
popd > /dev/null


echo "* Generating gdk pixbuf loaders.cache"
gdk-pixbuf-query-loaders.exe "dist/lib/gdk-pixbuf-2.0/2.10.0/loaders/*" | sed 's+".*dist/+"+g' > dist/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache
echo "* Generating icons and theme cache"
for itheme in `ls dist/share/icons/`; do
	gtk-update-icon-cache.exe -t -i "dist/share/icons/${itheme}"
done

echo "* Generating HTML Manual Page"
groff.exe -mandoc -Thtml < man/xpra.1 > ${DIST}/manual.html

if [ "${BUNDLE_PUTTY}" == "1" ]; then
	echo "* Adding TortoisePlink"
	TORTOISESVN="/c/Program Files/TortoiseSVN"
	if [ ! -e "${TORTOISESVN}" ]; then
		TORTOISESVN="/c/Program Files (x86)/TortoiseSVN"
		if [ ! -e "${TORTOISESVN}" ]; then
			echo "Missing TortoiseSVN!"
			exit 1
		fi
	fi
	cp -fn "${TORTOISESVN}/bin/TortoisePlink.exe" "${DIST}/Plink.exe"
	for dll in vcruntime140.dll msvcp140.dll vcruntime140_1.dll; do
		cp "/c/Windows/System32/$dll" "${DIST}/"
	done
	#are we meant to include those DLLs?
	#rsync -rplogt "${TORTOISESVN}/bin/"*dll "${DIST}/"
fi

if [ "${BUNDLE_OPENSSH}" == "1" ]; then
	echo "* Adding OpenSSH"
	cp -fn "/usr/bin/ssh.exe" "${DIST}/"
	cp -fn "/usr/bin/sshpass.exe" "${DIST}/"
	cp -fn "/usr/bin/ssh-keygen.exe" "${DIST}/"
	for x in 2.0 gcc_s crypto z gssapi asn1 com_err roken crypt heimntlm krb5 heimbase wind hx509 hcrypto sqlite3; do
		cp -fn /usr/bin/msys-$x*.dll "${DIST}/"
	done
fi

if [ "${BUNDLE_OPENSSL}" == "1" ]; then
	cp -fn "${MINGW_PREFIX}/bin/openssl.exe" "${DIST}/"
	#use the old filename so we don't have to change the xpra.iss and the setup.py build system:
	cp -fn "${MINGW_PREFIX}/ssl/openssl.cnf" "${DIST}/openssl.cfg"
	#we need those libraries at the top level:
	mv "${DIST}"/lib/libssl-*dll "${DIST}/"
	mv "${DIST}"/lib/libcrypto-*dll "${DIST}/"
fi

if [ "${BUNDLE_PAEXEC}" == "1" ]; then
	cp -fn "${MINGW_PREFIX}/bin/paexec.exe" "${DIST}/"
fi

if [ "${BUNDLE_DESKTOPLOGON}" == "1" ]; then
	for lib in AxMSTSCLib MSTSCLib DesktopLogon; do
		cp -fn "${MINGW_PREFIX}/bin/$lib.dll" "${DIST}/"
	done
fi

if [ "${DO_VERPATCH}" == "1" ]; then
	for exe in `ls dist/*exe | grep -v Plink.exe`; do
		tool_name=`echo $exe | sed 's+dist/++g;s+Xpra_++g;s+Xpra-++g;s+_+ +g;s+-+ +g;s+\.exe++g'`
		verpatch $exe				//s desc "Xpra $tool_name"		//va "${ZERO_PADDED_VERSION}" //s company "xpra.org" //s copyright "(c) xpra.org 2020" //s product "xpra" //pv "${ZERO_PADDED_VERSION}"
	done
	verpatch dist/Xpra_cmd.exe 		//s desc "Xpra command line"	//va "${ZERO_PADDED_VERSION}" //s company "xpra.org" //s copyright "(c) xpra.org 2020" //s product "xpra" //pv "${ZERO_PADDED_VERSION}"
	verpatch dist/Xpra-Proxy.exe	//s desc "Xpra Proxy Server"	//va "${ZERO_PADDED_VERSION}" //s company "xpra.org" //s copyright "(c) xpra.org 2020" //s product "xpra" //pv "${ZERO_PADDED_VERSION}"
	verpatch dist/Xpra.exe 			//s desc "Xpra" 				//va "${ZERO_PADDED_VERSION}" //s company "xpra.org" //s copyright "(c) xpra.org 2020" //s product "xpra" //pv "${ZERO_PADDED_VERSION}"
fi

################################################################################
# packaging: ZIP / EXE / MSI

if [ "${DO_ZIP}" == "1" ]; then
	echo "* Creating ZIP file:"
	rm -fr "${ZIP_DIR}" "${ZIP_FILENAME}"
	mkdir "${ZIP_DIR}"
	rsync -rplogt "${DIST}"/* "${ZIP_DIR}"
	zip -9qmr "${ZIP_FILENAME}" "${ZIP_DIR}"
	ls -la "${ZIP_FILENAME}"
fi

if [ "${DO_INSTALLER}" == "1" ]; then
	INNOSETUP="/c/Program Files/Inno Setup 5/ISCC.exe"
	if [ ! -e "${INNOSETUP}" ]; then
		INNOSETUP="/c/Program Files (x86)/Inno Setup 5/ISCC.exe"
		if [ ! -e "${INNOSETUP}" ]; then
			echo "cannot find InnoSetup"
			exit 1
		fi
	fi
	INNOSETUP_LOG="win32/innosetup.log"
	echo "* Creating the installer using InnoSetup"
	rm -f "Xpra_Setup.exe" "${INSTALLER_FILENAME}" "${INNOSETUP_LOG}"
	cp -fn "win32/xpra.iss" "xpra.iss"
	if [ "${MSYSTEM_CARCH}" == "x86_64" ]; then
		cat "win32/xpra.iss" | sed '/\(ArchitecturesInstallIn64BitMode\|ArchitecturesInstallIn64BitMode\)/ s/=.*/=x64/g' | sed '/\(AppName=\|AppVerName=\|DefaultGroupName=\)/ s/\r$/ (64-bit)\r/g' | sed 's/ArchitecturesAllowed=.*/ArchitecturesAllowed=x64/g' > "xpra.iss"
	fi
	if [ "${CLIENT_ONLY}" == "1" ]; then
		#remove shadow start menu entry
		sed -i"" "s/.*Xpra Shadow Server.*//g" xpra.iss
	fi
	sed -i"" "s/AppId=.*/AppId=${APPID}/g" xpra.iss
	sed -i"" "s/AppName=.*/AppName=Xpra ${VERSION} (${BITS}-bit)/g" xpra.iss
	sed -i"" "s/UninstallDisplayName=.*/UninstallDisplayName=Xpra ${VERSION} (${BITS}-bit)/g" xpra.iss
	sed -i"" "s/AppVersion=.*/AppVersion=${FULL_VERSION}/g" xpra.iss
	"${INNOSETUP}" "xpra.iss" >& "${INNOSETUP_LOG}"
	if [ "$?" != "0" ]; then
		echo "InnoSetup error - see ${INNOSETUP_LOG}:"
		tail -n 20 "${INNOSETUP_LOG}"
		#rm "xpra.iss"
		exit 1
	fi
	rm "xpra.iss"
	mv "dist\Xpra_Setup.exe" "${INSTALLER_FILENAME}"

	if [ "${DO_SIGN}" == "1" ]; then
		SIGNTOOL_LOG="win32/signtool.log"
		echo "* Signing EXE"
		cmd.exe //c signtool.exe sign //v //f "${KEY_FILE}" //t "http://timestamp.comodoca.com/authenticode" "${INSTALLER_FILENAME}" > ${SIGNTOOL_LOG}
		if [ "$?" != 0 ]; then
			echo "signtool command failed, see ${SIGNTOOL_LOG}:"
			cat ${SIGNTOOL_LOG}
		fi
	fi
	ls -la "${INSTALLER_FILENAME}"

	if [ "${RUN_INSTALLER}" == "1" ]; then
		echo "* Finished - running the new installer"
		#we need to escape slashes!
		#(this doesn't preserve spaces.. we should use shift instead)
		CMD_ARGS=`echo ${ARGS} | sed 's+/+//+g'`
		"./${INSTALLER_FILENAME}" "${CMD_ARGS}"
	fi
fi

if [ "${DO_MSI}" == "1" ]; then
	MSIWRAPPER="/c/Program Files/MSI Wrapper/MsiWrapper.exe"
	if [ ! -e "${MSIWRAPPER}" ]; then
		MSIWRAPPER="/c/Program Files (x86)/MSI Wrapper/MsiWrapper.exe"
		if [ ! -e "${MSIWRAPPER}" ]; then
			echo "cannot find MSI Wrapper"
			exit 1
		fi
	fi
	#we need to quadruple escape backslashes
	#as they get interpreted by the shell and sed, multiple times:
	CWD=`pwd | sed 's+/\([a-zA-Z]\)/+\1:\\\\\\\\+g; s+/+\\\\\\\\+g'`
	echo "CWD=${CWD}"
	cat "win32\msi.xml" | sed "s+\$CWD+${CWD}+g" | sed "s+\$INPUT+${INSTALLER_FILENAME}+g" | sed "s+\$OUTPUT+${MSI_FILENAME}+g" | sed "s+\$ZERO_PADDED_VERSION+${ZERO_PADDED_VERSION}+g" | sed "s+\$FULL_VERSION+${FULL_VERSION}+g" > msi.xml
	"${MSIWRAPPER}"
	if [ "${DO_SIGN}" == "1" ]; then
		SIGNTOOL_LOG="win32/signtool.log"
		echo "* Signing MSI"
		cmd.exe //c signtool.exe sign //v //f "${KEY_FILE}" //t "http://timestamp.comodoca.com/authenticode" "${MSI_FILENAME}" > ${SIGNTOOL_LOG}
		if [ "$?" != 0 ]; then
			echo "signtool command failed, see ${SIGNTOOL_LOG}:"
			cat ${SIGNTOOL_LOG}
		fi
	fi
fi
