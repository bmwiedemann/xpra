%global commit d86dc916771c126afb797637dda9f6421c0cb998

Name:           lz4
Version:        r131
Release:        1%{?dist}
Summary:        Extremely fast compression algorithm

Group:          Applications/System
License:        GPLv2+ and BSD
URL:            https://code.google.com/p/lz4/
Source0:        https://github.com/Cyan4973/%{name}/archive/%{commit}/%{name}-%{commit}.tar.gz

%if 0%{?rhel}
BuildRoot:      %{_tmppath}/%{name}-%{version}-%{release}-buildroot
%endif

%description
LZ4 is an extremely fast loss-less compression algorithm, providing compression
speed at 400 MB/s per core, scalable with multi-core CPU. It also features
an extremely fast decoder, with speed in multiple GB/s per core, typically
reaching RAM speed limits on multi-core systems.

%package        devel
Summary:        Development library for lz4
Group:          Development/Libraries
License:        BSD
Requires:       %{name}%{?_isa} = %{version}-%{release}

%description    devel
This package contains the header(.h) and library(.so) files required to build
applications using liblz4 library.


%package        static
Summary:        Static library for lz4
Group:          Development/Libraries
License:        BSD

%description    static
LZ4 is an extremely fast loss-less compression algorithm. This package
contains static libraries for static linking of applications.

%prep
%setup -q -n %{name}-%{commit}
echo '#!/bin/sh' > ./configure
chmod +x ./configure

%build
%configure
make %{?_smp_mflags}


%install
%configure
%make_install LIBDIR=%{_libdir} PREFIX=/usr INSTALL="install -p"
chmod -x %{buildroot}%{_includedir}/*.h


%post -p /sbin/ldconfig
%postun -p /sbin/ldconfig


%files
%doc programs/COPYING NEWS
%{_bindir}/lz4
%{_bindir}/lz4c
%{_bindir}/lz4cat
%{_bindir}/unlz4
%{_mandir}/man1/lz4*
%{_mandir}/man1/unlz4*
%{_libdir}/liblz4.so.1*


%files devel
%doc lib/LICENSE
%{_includedir}/*.h
%{_libdir}/liblz4.so
%{_libdir}/pkgconfig/liblz4.pc


%files static
%doc lib/LICENSE
%{_libdir}/liblz4.a


%changelog
* Thu Dec 17 2015 Antoine Martin <antoine@devloop.org.uk> - r131-1
- xpra packaging for CentOS
