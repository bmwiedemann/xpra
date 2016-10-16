#!/bin/bash

echo
echo "*******************************************************************************"
if [ ! -d "./image/Xpra.app" ]; then
	echo "./image/Xpra.app is missing - cannot continue"
	exit 1
fi

#get the version and build info from the python build records:
export PYTHONPATH="image/Xpra.app/Contents/Resources/lib/python/"
VERSION=`python -c "from xpra import __version__;import sys;sys.stdout.write(__version__)"`
REVISION=`python -c "from xpra import src_info;import sys;sys.stdout.write(str(src_info.REVISION))"`
REV_MOD=`python -c "from xpra import src_info;import sys;sys.stdout.write(['','M'][src_info.LOCAL_MODIFICATIONS>0])"`
BUILD_CPU=`python -c "from xpra import build_info;import sys;sys.stdout.write(str(build_info.BUILD_CPU))"`
BUILD_INFO=""
if [ "$BUILD_CPU" != "i386" ]; then
	BUILD_INFO="-x86_64"
fi

PKG_FILENAME="Xpra$BUILD_INFO-$VERSION-r$REVISION$REV_MOD.pkg"
rm -f ./image/$PKG_FILENAME >& /dev/null
echo "Making $PKG_FILENAME"

#create directory structure:
rm -fr ./image/flat ./image/root
mkdir -p ./image/flat/base.pkg ./image/flat/Resources/en.lproj
mkdir -p ./image/root/Applications
rsync -rplogt ./image/Xpra.app ./image/root/Applications/

#man page:
mkdir -p ./image/root/usr/share/man/man1
for x in xpra xpra_launcher; do
	gzip -c ../src/man/$x.1 > ./image/root/usr/share/man/man1/$x.1.gz
done
#add cups backend:
mkdir -p ./image/root/usr/libexec/cups/backend/
cp ../src/cups/xpraforwarder ./image/root/usr/libexec/cups/backend/
chmod 700 ./image/root/usr/libexec/cups/backend
#add launchd agent:
#mkdir -p ./image/root/System/Library/LaunchAgents/
#cp ./org.xpra.Agent.plist ./image/root/System/Library/LaunchAgents/

pushd ./image/root >& /dev/null
find . | cpio -o --format odc --owner 0:80 | gzip -c > ../flat/base.pkg/Payload
popd >& /dev/null

FILECOUNT=`find ./image/root | wc -l`
DISKUSAGE=`du -sk ./image/root`

#add the postinstall fix script (cups backend and shortcuts)
mkdir ./image/scripts
cp postinstall ./image/scripts/
chmod +x ./image/scripts/postinstall
pushd ./image/scripts >& /dev/null
find . | cpio -o --format odc --owner 0:80 | gzip -c > ../flat/base.pkg/Scripts
popd >& /dev/null

mkbom -u 0 -g 80 ./image/root ./image/flat/base.pkg/Bom

cat > ./image/flat/base.pkg/PackageInfo << EOF
<pkg-info format-version="2" identifier="org.xpra.pkg" version="$VERSION" install-location="/" auth="root">
  <payload installKBytes="$DISKUSAGE" numberOfFiles="$FILECOUNT"/>
  <scripts>
	<postinstall file="./postinstall"/>
  </scripts>
  <bundle-version>
	<bundle id="org.xpra.Xpra" CFBundleIdentifier="org.xpra.Xpra" path="./Applications/Xpra.app" CFBundleVersion="$VERSION">
		<bundle id="org.xpra.XpraNoDock" CFBundleIdentifier="org.xpra.XpraNoDock" path="./Contents/Xpra_NoDock.app" CFBundleVersion="$VERSION"/>
	</bundle>
  </bundle-version>
</pkg-info>
EOF

cat > ./image/flat/Distribution << EOF
<?xml version="1.0" encoding="utf-8"?>
<installer-script minSpecVersion="2">
	<title>Xpra $VERSION</title>
	<allowed-os-versions>
		<os-version min="10.5.8" />
	</allowed-os-versions>
	<options customize="never" require-scripts="false" allow-external-scripts="no"/>
	<domains enable_anywhere="true"/>
	<background file="background.png" alignment="bottomleft" scaling="none"/>
	<license file="GPL.rtf"/>
	<choices-outline>
		<line choice="choice1"/>
	</choices-outline>
	<choice id="choice1" title="base">
		<pkg-ref id="org.xpra.pkg"/>
	</choice>
	<pkg-ref id="org.xpra.pkg" installKBytes="$DISKUSAGE" version="$VERSION" auth="Root">#base.pkg</pkg-ref>
</installer-script>
EOF

#add license and background files to image:
cp background.png GPL.rtf ./image/flat/Resources/en.lproj/

pushd ./image/flat >& /dev/null
xar --compression none -cf "../$PKG_FILENAME" *
popd >& /dev/null

#clean temporary build directories
rm -fr ./image/flat ./image/root ./image/scripts

if [ ! -z "${CODESIGN_KEYNAME}" ]; then
		echo "Signing with key '${CODESIGN_KEYNAME}'"
		productsign --sign "3rd Party Mac Developer Installer: ${CODESIGN_KEYNAME}" ./image/$PKG_FILENAME ./image/$PKG_FILENAME.signed
		if [ "$?" == "0" ]; then
			ls -la ./image/*pkg*
			mv ./image/$PKG_FILENAME.signed ./image/$PKG_FILENAME
		fi
else
		echo "PKG Signing skipped (no keyname)"
fi

#show resulting file and copy it to the desktop
du -sm ./image/$PKG_FILENAME
cp ./image/$PKG_FILENAME ~/Desktop/

echo "Done PKG"
echo "*******************************************************************************"
echo
