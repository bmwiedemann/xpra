Name:		libyuv
Summary:	YUV conversion and scaling functionality library
Version:	0
Release:	0.31.20180904git9a07219.1%{?dist}
License:	BSD
Url:		https://chromium.googlesource.com/libyuv/libyuv
VCS:		scm:git:https://chromium.googlesource.com/libyuv/libyuv
## git clone https://chromium.googlesource.com/libyuv/libyuv
## cd libyuv
## git archive --format=tar --prefix=libyuv-0/ 9a07219 | xz  > ../libyuv-0.tar.xz
Source0:	%{name}-%{version}.tar.xz
# Fedora-specific. Upstream isn't interested in these patches.
Patch1:		libyuv-0001-Use-a-proper-so-version.patch
Patch2:		libyuv-0002-Link-against-shared-library.patch
Patch3:		libyuv-0003-Disable-static-library.patch
Patch4:		libyuv-0004-Don-t-install-conversion-tool.patch
Patch5:		libyuv-0005-Use-library-suffix-during-installation.patch
BuildRequires:	cmake
BuildRequires:	gcc-c++
%if !0%{?el7}
BuildRequires:	gtest-devel
%endif
BuildRequires:	libjpeg-devel


%description
This is an open source project that includes YUV conversion and scaling
functionality. Converts all webcam formats to YUV (I420). Convert YUV to
formats for rendering/effects. Rotate by 90 degrees to adjust for mobile
devices in portrait mode. Scale YUV to prepare content for compression,
with point, bilinear or box filter.


%package devel
Summary: The development files for %{name}
Requires: %{name}%{?_isa} = %{version}-%{release}


%description devel
Additional header files for development with %{name}.


%prep
%autosetup -p1


%build
%if 0%{?el7}
%{cmake} -DTEST=false
%else
%{cmake} -DTEST=true
%endif

%install
%{make_install}
mkdir -p %{buildroot}%{_libdir}/pkgconfig
cat > %{buildroot}%{_libdir}/pkgconfig/%{name}.pc <<EOF
prefix=%{_prefix}
libdir=%{_libdir}
includedir=%{_includedir}

Name: %{name}
Description: YUV conversion and scaling functionality library
Version: 0
Libs: -lyuv -ljpeg
EOF


%check
# FIXME fails again on s390
./libyuv_unittest || true


%files
%license LICENSE
%doc AUTHORS PATENTS README.md
%{_libdir}/%{name}.so.*


%files devel
%{_includedir}/%{name}
%{_includedir}/%{name}.h
%{_libdir}/%{name}.so
%{_libdir}/pkgconfig/%{name}.pc


%changelog
* Tue Nov 13 2018 Peter Lemenkov <lemenkov@gmail.com> - 0-0.31.20180904git9a07219.1
- add pkgconfig file

* Mon Sep 24 2018 Peter Lemenkov <lemenkov@gmail.com> - 0-0.31.20180904git9a07219
- Update to the latest git snapshot

* Fri Jul 13 2018 Fedora Release Engineering <releng@fedoraproject.org> - 0-0.30.20121221svn522
- Rebuilt for https://fedoraproject.org/wiki/Fedora_29_Mass_Rebuild

* Wed Feb 07 2018 Fedora Release Engineering <releng@fedoraproject.org> - 0-0.29.20121221svn522
- Rebuilt for https://fedoraproject.org/wiki/Fedora_28_Mass_Rebuild

* Thu Aug 03 2017 Fedora Release Engineering <releng@fedoraproject.org> - 0-0.28.20121221svn522
- Rebuilt for https://fedoraproject.org/wiki/Fedora_27_Binutils_Mass_Rebuild

* Wed Jul 26 2017 Fedora Release Engineering <releng@fedoraproject.org> - 0-0.27.20121221svn522
- Rebuilt for https://fedoraproject.org/wiki/Fedora_27_Mass_Rebuild

* Fri Feb 10 2017 Fedora Release Engineering <releng@fedoraproject.org> - 0-0.26.20121221svn522
- Rebuilt for https://fedoraproject.org/wiki/Fedora_26_Mass_Rebuild

* Thu Feb 04 2016 Fedora Release Engineering <releng@fedoraproject.org> - 0-0.25.20121221svn522
- Rebuilt for https://fedoraproject.org/wiki/Fedora_24_Mass_Rebuild

* Wed Jun 17 2015 Fedora Release Engineering <rel-eng@lists.fedoraproject.org> - 0-0.24.20121221svn522
- Rebuilt for https://fedoraproject.org/wiki/Fedora_23_Mass_Rebuild

* Sat May 02 2015 Kalev Lember <kalevlember@gmail.com> - 0-0.23.20121221svn522
- Rebuilt for GCC 5 C++11 ABI change

* Sun Aug 17 2014 Fedora Release Engineering <rel-eng@lists.fedoraproject.org> - 0-0.22.20121221svn522
- Rebuilt for https://fedoraproject.org/wiki/Fedora_21_22_Mass_Rebuild

* Sat Jun 07 2014 Fedora Release Engineering <rel-eng@lists.fedoraproject.org> - 0-0.21.20121221svn522
- Rebuilt for https://fedoraproject.org/wiki/Fedora_21_Mass_Rebuild

* Sat Aug 03 2013 Fedora Release Engineering <rel-eng@lists.fedoraproject.org> - 0-0.20.20121221svn522
- Rebuilt for https://fedoraproject.org/wiki/Fedora_20_Mass_Rebuild

* Thu Feb 14 2013 Fedora Release Engineering <rel-eng@lists.fedoraproject.org> - 0-0.19.20121221svn522
- Rebuilt for https://fedoraproject.org/wiki/Fedora_19_Mass_Rebuild

* Fri Jan 18 2013 Adam Tkac <atkac redhat com> - 0-0.18.20121221svn522
- rebuild due to "jpeg8-ABI" feature drop

* Sun Dec 30 2012 Dan Horák <dan[at]danny.cz> - 0-0.17.20121221svn522
- add big endian fix

* Fri Dec 21 2012 Adam Tkac <atkac redhat com> - 0-0.16.20121221svn522
- rebuild against new libjpeg

* Fri Dec 21 2012 Peter Lemenkov <lemenkov@gmail.com> - 0-0.15.20121221svn522
- Next svn snapshot - ver. 522

* Thu Oct 04 2012 Peter Lemenkov <lemenkov@gmail.com> - 0-0.14.20121001svn389
- Next svn snapshot - ver. 389
- Enable NEON on ARM (if detected)

* Sat Sep 15 2012 Peter Lemenkov <lemenkov@gmail.com> - 0-0.13.20120915svn353
- Next svn snapshot - ver. 353
- Dropped upstreamed patch no.3

* Mon Jul 30 2012 Peter Lemenkov <lemenkov@gmail.com> - 0-0.12.20120727svn312
- Next svn snapshot - ver. 312

* Thu Jul 19 2012 Fedora Release Engineering <rel-eng@lists.fedoraproject.org> - 0-0.11.20120627svn296
- Rebuilt for https://fedoraproject.org/wiki/Fedora_18_Mass_Rebuild

* Thu Jul 05 2012 Peter Lemenkov <lemenkov@gmail.com> - 0-0.10.20120627svn296
- Next svn snapshot - ver. 296
- Dropped patch3 (header conflict) - fixed upstream

* Thu Jun 14 2012 Tom Callaway <spot@fedoraproject.org> - 0-0.9.20120518svn268
- resolve header conflict with duplicate definition in scale*.h

* Fri May 18 2012 Peter Lemenkov <lemenkov@gmail.com> - 0-0.8.20120518svn268
- Next svn snapshot - ver. 268
- Fixed failure on s390x and PPC64 (see rhbz #822494)
- Fixed FTBFS on EL5 (see rhbz #819179)

* Sat May 05 2012 Peter Lemenkov <lemenkov@gmail.com> - 0-0.7.20120505svn256
- Next svn snapshot - ver. 256

* Sun Apr 08 2012 Peter Lemenkov <lemenkov@gmail.com> - 0-0.6.20120406svn239
- Next svn snapshot - ver. 239

* Thu Mar 08 2012 Peter Lemenkov <lemenkov@gmail.com> - 0-0.5.20120308svn209
- Next svn ver. - 209
- Drop upstreamed patches
- Add libjpeg as a dependency

* Thu Feb 02 2012 Peter Lemenkov <lemenkov@gmail.com> - 0-0.4.20120202svn164
- Next svn ver. - 164
- Added two patches - no.2 and no.3

* Thu Jan 12 2012 Peter Lemenkov <lemenkov@gmail.com> - 0-0.3.20120109svn128
- Use bzip2 instead of xz (for EL-5)

* Wed Jan 11 2012 Peter Lemenkov <lemenkov@gmail.com> - 0-0.2.20120109svn128
- Update to svn rev. 128
- Enable unit-tests
- Dropped obsolete defattr directive
- Consistently use macros
- Explicitly add _isa to the Requires for *-devel sub-package

* Fri Jan  6 2012 Peter Lemenkov <lemenkov@gmail.com> - 0-0.1.20120105svn127
- Initial package
