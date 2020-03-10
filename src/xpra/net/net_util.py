#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2013-2019 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

# taken from the code I wrote for winswitch

import socket
import sys

from xpra.os_util import WIN32
from xpra.log import Logger

log = Logger("network", "util")


netifaces_version = 0
_netifaces = None
def import_netifaces():
    global _netifaces, netifaces_version
    if _netifaces is None:
        try:
            import netifaces                #@UnresolvedImport
            log("netifaces loaded sucessfully")
            _netifaces = netifaces
            netifaces_version = netifaces.version        #@UndefinedVariable
        except ImportError:
            _netifaces = False
            log.warn("Warning: the python netifaces package is missing")
    return _netifaces

iface_ipmasks = {}
bind_IPs = None


def get_free_tcp_port() -> int:
    s = socket.socket()
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def get_interfaces():
    netifaces = import_netifaces()
    if not netifaces:
        return []
    return netifaces.interfaces()           #@UndefinedVariable pylint: disable=no-member

def get_interfaces_addresses() -> dict:
    d = {}
    netifaces = import_netifaces()
    if netifaces:
        for iface in get_interfaces():
            d[iface] = netifaces.ifaddresses(iface)     #@UndefinedVariable pylint: disable=no-member
    return d

def get_interface(address):
    for iface, idefs in get_interfaces_addresses().items():
        #ie: {
        #    17: [{'broadcast': u'ff:ff:ff:ff:ff:ff', 'addr': u'00:e0:4c:68:46:a6'}],
        #    2: [{'broadcast': u'192.168.1.255', 'netmask': u'255.255.255.0', 'addr': u'192.168.1.7'}],
        #    10: [{'netmask': u'ffff:ffff:ffff:ffff::/64', 'addr': u'fe80::6c45:655:c59e:92a1%eth0'}]
        #}
        for _itype, defs in idefs.items():
            #ie: itype=2, defs=[{'broadcast': u'192.168.1.255', 'netmask': u'255.255.255.0', 'addr': u'192.168.1.7'}]
            for props in defs:
                if props.get("addr")==address:
                    return iface
    return None

def get_gateways() -> dict:
    netifaces = import_netifaces()
    if not netifaces:
        return {}
    #versions older than 0.10.5 can crash when calling gateways()
    #https://bitbucket.org/al45tair/netifaces/issues/15/gateways-function-crash-segmentation-fault
    if netifaces.version<'0.10.5':            #@UndefinedVariable pylint: disable=no-member
        return {}
    try:
        d = netifaces.gateways()            #@UndefinedVariable pylint: disable=no-member
        AF_NAMES = {}
        for k in dir(netifaces):
            if k.startswith("AF_"):
                v = getattr(netifaces, k)
                AF_NAMES[v] = k[3:]
        gateways = {}
        for family, gws in d.items():
            if family=="default":
                continue
            gateways[AF_NAMES.get(family, family)] = gws
        return gateways
    except Exception:
        log("get_gateways() failed", exc_info=True)
        return {}

def get_bind_IPs():
    global bind_IPs
    if not bind_IPs:
        netifaces = import_netifaces()
        if netifaces:
            bind_IPs = do_get_bind_IPs()
        else:
            bind_IPs = ["127.0.0.1"]
    return bind_IPs

def do_get_bind_IPs():
    global iface_ipmasks
    ips = []
    netifaces = import_netifaces()
    assert netifaces
    ifaces = netifaces.interfaces()            #@UndefinedVariable pylint: disable=no-member
    log("ifaces=%s", ifaces)
    for iface in ifaces:
        if_ipmasks = []
        try:
            ipmasks = do_get_bind_ifacemask(iface)
            for ipmask in ipmasks:
                (ip,_) = ipmask
                if ip not in ips:
                    ips.append(ip)
                if ipmask not in if_ipmasks:
                    if_ipmasks.append(ipmask)
        except Exception as e:
            log("do_get_bind_IPs()", exc_info=True)
            log.error("Error parsing network interface '%s':", iface)
            log.error(" %s", iface, e)
        iface_ipmasks[iface] = if_ipmasks
    log("do_get_bind_IPs()=%s", ips)
    log("iface_ipmasks=%s", iface_ipmasks)
    return ips

def do_get_bind_ifacemask(iface):
    ipmasks = []
    netifaces = import_netifaces()
    assert netifaces
    address_types = netifaces.ifaddresses(iface)    #@UndefinedVariable pylint: disable=no-member
    for addresses in address_types.values():
        for address in addresses:
            if 'netmask' in address and 'addr' in address:
                addr = address['addr']
                mask = address['netmask']
                if addr!= '::1' and addr != '0.0.0.0' and addr.find("%")<0:
                    try:
                        socket.inet_aton(addr)
                        ipmasks.append((addr,mask))
                    except Exception as e:
                        log.error("do_get_bind_ifacemask(%s) error on %s", iface, addr, e)
    log("do_get_bind_ifacemask(%s)=%s", iface, ipmasks)
    return ipmasks

def get_iface(ip) -> str:
    log("get_iface(%s)", ip)
    if not ip:
        return None
    if ip.find("%")>=0:
        return ip.split("%", 1)[1]
    if ip.find(":")>=0:
        #ipv6?
        return None
    if any(x for x in ip if (".:0123456789").find(x)<0):
        #extra characters, assume this is a hostname:
        try:
            v = socket.getaddrinfo(ip, None)
            assert len(v)>0
        except Exception as e:
            log.error("Error: cannot revolve '%s'", ip)
            return None
        for i, x in enumerate(v):
            family, socktype, proto, canonname, sockaddr = x
            log("get_iface(%s) [%i]=%s", ip, i, (family, socktype, proto, canonname, sockaddr))
            if family==socket.AF_INET:
                break
        log("get_iface(%s) sockaddr=%s", ip, sockaddr)
        ip = sockaddr[0]

    ip_parts = ip.split(".")
    if len(ip_parts)!=4:
        return None

    best_match = None
    get_bind_IPs()
    for (iface, ipmasks) in iface_ipmasks.items():
        for (test_ip,mask) in ipmasks:
            if test_ip == ip:
                #exact match
                log("get_iface(%s)=%s", iface, ip)
                return iface
            test_ip_parts = test_ip.split(".")
            mask_parts = mask.split(".")
            if len(test_ip_parts)!=4 or len(mask_parts)!=4:
                log.error("incorrect ip or mask: %s/%s", test_ip, mask)
            match = True
            try:
                for i in (0,1,2,3):
                    mask_part = int(mask_parts[i])
                    ip_part = int(ip_parts[i]) & mask_part
                    test_ip_part = int(test_ip_parts[i]) & mask_part
                    if ip_part!=test_ip_part:
                        match = False
                        break
                if match:
                    best_match = iface
            except Exception as e:
                log.error("error parsing ip (%s) or its mask (%s): %s", test_ip, mask, e)
    log("get_iface(%s)=%s", ip, best_match)
    return best_match


# Found this recipe here:
# http://code.activestate.com/recipes/442490/
if_nametoindex = None
if_indextoname = None

if WIN32:   # pragma: no cover
    def int_if_nametoindex(iface):
        #IPv6 addresses give us the interface as a string:
        #fe80:....%11, so try to convert "11" into 11
        try:
            return int(iface)
        except (TypeError, ValueError):
            return None
    if_nametoindex = int_if_nametoindex
else:
    if_nametoindex = socket.if_nametoindex
    def socket_if_indextoname(index):
        if index<0:
            return None
        return socket.if_indextoname(index)
    if_indextoname = socket_if_indextoname


net_sys_config = None
def get_net_sys_config():
    global net_sys_config
    if net_sys_config is None:
        net_sys_config = {}
        if sys.platform.startswith("linux"):
            def stripnl(v):
                return str(v).rstrip("\r").rstrip("\n")
            def addproc(procpath, subsystem, name, conv=stripnl):
                assert name
                try:
                    with open(procpath) as f:
                        data = f.read()
                        subdict = net_sys_config.setdefault(subsystem, {})
                        if name.find("/")>0:
                            sub, name = name.split("/", 1)
                            subdict = subdict.setdefault(sub, {})
                        for sub in ("ip", "tcp", "ipfrag", "icmp", "igmp"):
                            if name.startswith("%s_" % sub):
                                name = name[len(sub)+1:]
                                subdict = subdict.setdefault(sub, {})
                                break
                        subdict[name] = conv(data)
                except Exception as e:
                    log("cannot read '%s': %s", procpath, e)
            for k in ("netdev_max_backlog", "optmem_max", "rmem_default", "rmem_max", "wmem_default", "wmem_max", "max_skb_frags",
                    "busy_poll", "busy_read", "somaxconn"):
                addproc("/proc/sys/net/core/%s" % k,     "core", k, int)
            for k in ("default_qdisc", ):
                addproc("/proc/sys/net/core/%s" % k,     "core", k)
            for k in ("max_dgram_qlen", ):
                addproc("/proc/sys/net/unix/%s" % k,     "unix", k, int)
            for k in ("ip_forward", "ip_forward_use_pmtu", "tcp_abort_on_overflow", "fwmark_reflect", "tcp_autocorking", "tcp_dsack",
                    "tcp_ecn_fallback", "tcp_fack",
                    #"tcp_l3mdev_accept",
                    "tcp_low_latency", "tcp_no_metrics_save", "tcp_recovery", "tcp_retrans_collapse", "tcp_timestamps",
                    "tcp_workaround_signed_windows", "tcp_thin_linear_timeouts", "tcp_thin_dupack", "ip_nonlocal_bind",
                    "ip_dynaddr", "ip_early_demux", "icmp_echo_ignore_all", "icmp_echo_ignore_broadcasts",
                    ):
                addproc("/proc/sys/net/ipv4/%s" % k,     "ipv4", k, bool)
            for k in ("tcp_allowed_congestion_control", "tcp_available_congestion_control", "tcp_congestion_control", "tcp_early_retrans",
                    "tcp_moderate_rcvbuf", "tcp_rfc1337", "tcp_sack", "tcp_slow_start_after_idle", "tcp_stdurg",
                    "tcp_syncookies", "tcp_tw_recycle", "tcp_tw_reuse", "tcp_window_scaling",
                    "icmp_ignore_bogus_error_responses", "icmp_errors_use_inbound_ifaddr"):
                addproc("/proc/sys/net/ipv4/%s" % k,     "ipv4", k)
            def parsenums(v):
                return tuple(int(x.strip()) for x in v.split("\t") if len(x.strip())>0)
            for k in ("tcp_mem", "tcp_rmem", "tcp_wmem", "ip_local_port_range", "ip_local_reserved_ports", ):
                addproc("/proc/sys/net/ipv4/%s" % k,     "ipv4", k, parsenums)
            for k in ("ip_default_ttl", "ip_no_pmtu_disc", "route/min_pmtu",
                    "route/mtu_expires", "route/min_adv_mss",
                    "ipfrag_high_thresh", "ipfrag_low_thresh", "ipfrag_time", "ipfrag_max_dist",
                    "tcp_adv_win_scale", "tcp_app_win", "tcp_base_mss", "tcp_ecn", "tcp_fin_timeout", "tcp_frto",
                    "tcp_invalid_ratelimit", "tcp_keepalive_time", "tcp_keepalive_probes", "tcp_keepalive_intvl",
                    "tcp_max_orphans", "tcp_max_syn_backlog", "tcp_max_tw_buckets",
                    "tcp_min_rtt_wlen", "tcp_mtu_probing", "tcp_probe_interval", "tcp_probe_threshold", "tcp_orphan_retries",
                    "tcp_reordering", "tcp_max_reordering", "tcp_retries1", "tcp_retries2", "tcp_synack_retries",
                    "tcp_fastopen", "tcp_syn_retries", "tcp_min_tso_segs", "tcp_pacing_ss_ratio",
                    "tcp_pacing_ca_ratio", "tcp_tso_win_divisor", "tcp_notsent_lowat",
                    "tcp_limit_output_bytes", "tcp_challenge_ack_limit",
                    "icmp_ratelimit", "icmp_msgs_per_sec", "icmp_msgs_burst", "icmp_ratemask",
                    "igmp_max_memberships", "igmp_max_msf", "igmp_qrv",
                    ):
                addproc("/proc/sys/net/ipv4/%s" % k,     "ipv4", k, int)
    return net_sys_config

def get_net_config() -> dict:
    config = {}
    try:
        from xpra.net.bytestreams import VSOCK_TIMEOUT, SOCKET_TIMEOUT, SOCKET_NODELAY
        config = {
                "vsocket.timeout"    : VSOCK_TIMEOUT,
                "socket.timeout"     : SOCKET_TIMEOUT,
                }
        if SOCKET_NODELAY is not None:
            config["socket.nodelay"] = SOCKET_NODELAY
    except Exception:   # pragma: no cover
        log("get_net_config()", exc_info=True)
    return config


def get_ssl_info(show_constants=False) -> dict:
    try:
        import ssl
    except ImportError as e:    # pragma: no cover
        log("no ssl: %s", e)
        return {}
    info = {}
    if show_constants:
        protocols = dict((k,int(getattr(ssl, k))) for k in dir(ssl) if k.startswith("PROTOCOL_"))
        ops = dict((k,int(getattr(ssl, k))) for k in dir(ssl) if k.startswith("OP_"))
        vers = dict((k,int(getattr(ssl, k))) for k in dir(ssl) if k.startswith("VERIFY_"))
        info.update({
                "protocols"    : protocols,
                "options"    : ops,
                "verify"    : vers,
                })
    for k,name in {
                    "HAS_ALPN"                : "alpn",
                    "HAS_ECDH"                : "ecdh",
                    "HAS_SNI"                : "sni",
                    "HAS_NPN"                : "npn",
                    "CHANNEL_BINDING_TYPES"    : "channel-binding-types",
                    }.items():
        v = getattr(ssl, k, None)
        if v is not None:
            info[name] = v
    for k, idef in {
                    ""           : ("version", str),
                    "_INFO"      : ("version-info", str),
                    "_NUMBER"    : ("version-number", int),
                    }.items():
        v = getattr(ssl, "OPENSSL_VERSION%s" % k, None)
        if v is not None:
            name, conv = idef
            info.setdefault("openssl", {})[name] = conv(v)
    return info


def get_network_caps() -> dict:
    from xpra.net.digest import get_digests
    from xpra.net.crypto import get_crypto_caps
    from xpra.net.compression import get_enabled_compressors, get_compression_caps
    from xpra.net.packet_encoding import get_enabled_encoders, get_packet_encoding_caps
    digests = get_digests()
    #"hmac" is the legacy name, "xor" and "des" should not be used for salt:
    salt_digests = tuple(x for x in digests if x not in ("hmac", "xor", "des"))
    caps = {
                "digest"                : digests,
                "salt-digest"           : salt_digests,
                "compressors"           : get_enabled_compressors(),
                "encoders"              : get_enabled_encoders(),
               }
    caps.update(get_crypto_caps())
    caps.update(get_compression_caps())
    caps.update(get_packet_encoding_caps())
    return caps


def get_info() -> dict:
    i = get_network_caps()
    netifaces = import_netifaces()
    if netifaces:
        i["interfaces"] = get_interfaces()
        i["gateways"] = get_gateways()
    if "ssl" in sys.modules:
        ssli = get_ssl_info()
        ssli[""] = True
        i["ssl"] = ssli
    s = get_net_sys_config()
    if s:
        i["system"] = s
    i["config"] = get_net_config()
    paramiko = sys.modules.get("paramiko")
    if paramiko:
        i["paramiko"] = {
            "version"   : paramiko.__version_info__,
            }
    return i


def main(): # pragma: no cover
    from xpra.os_util import POSIX
    from xpra.util import print_nested_dict, csv
    from xpra.platform import program_context
    from xpra.platform.netdev_query import get_interface_info
    from xpra.log import enable_color, add_debug_category, enable_debug_for
    with program_context("Network-Info", "Network Info"):
        enable_color()
        verbose = "-v" in sys.argv or "--verbose" in sys.argv
        if verbose:
            enable_debug_for("network")
            add_debug_category("network")
            log.enable_debug()

        print("Network interfaces found:")
        netifaces = import_netifaces()
        for iface in get_interfaces():
            if if_nametoindex:
                print("* %s (index=%s)" % (iface.ljust(20), if_nametoindex(iface)))
            else:
                print("* %s" % iface)
            addresses = netifaces.ifaddresses(iface)     #@UndefinedVariable pylint: disable=no-member
            for addr, defs in addresses.items():
                if addr in (socket.AF_INET, socket.AF_INET6):
                    for d in defs:
                        ip = d.get("addr")
                        if ip:
                            stype = {
                                socket.AF_INET  : "IPv4",
                                socket.AF_INET6 : "IPv6",
                                }[addr]
                            print(" * %s:     %s" % (stype, ip))
                            if POSIX:
                                from xpra.net.socket_util import create_tcp_socket
                                try:
                                    sock = create_tcp_socket(ip, 0)
                                    sockfd = sock.fileno()
                                    info = get_interface_info(sockfd, iface)
                                    if info:
                                        print("  %s" % info)
                                finally:
                                    sock.close()
            if not POSIX:
                info = get_interface_info(0, iface)
                if info:
                    print("  %s" % info)

        from xpra.os_util import bytestostr
        def pver(v):
            if isinstance(v, (tuple, list)):
                s = ""
                lastx = None
                for x in v:
                    if lastx is not None:
                        #dot seperated numbers
                        if isinstance(lastx, int):
                            s += "."
                        else:
                            s += ", "
                    s += bytestostr(x)
                    lastx = x
                return s
            if isinstance(v, bytes):
                v = bytestostr(v)
            if isinstance(v, str) and v.startswith("v"):
                return v[1:]
            return str(v)

        print("Gateways found:")
        for gt,idefs in get_gateways().items():
            print("* %s" % gt)      #ie: "INET"
            for i, idef in enumerate(idefs):
                if isinstance(idef, (list, tuple)):
                    print(" [%i]           %s" % (i, csv(idef)))
                    continue

        print("")
        print("Protocol Capabilities:")
        netcaps = get_network_caps()
        netif = {""    : bool(netifaces)}
        if netifaces_version:
            netif["version"] = netifaces_version
        netcaps["netifaces"] = netif
        print_nested_dict(netcaps, vformat=pver)

        print("")
        print("Network Config:")
        print_nested_dict(get_net_config())

        net_sys = get_net_sys_config()
        if net_sys:
            print("")
            print("Network System Config:")
            print_nested_dict(net_sys)

        print("")
        print("SSL:")
        print_nested_dict(get_ssl_info(True))

        try:
            from xpra.net.crypto import crypto_backend_init, get_crypto_caps
            crypto_backend_init()
            ccaps = get_crypto_caps()
            if ccaps:
                print("")
                print("Crypto Capabilities:")
                print_nested_dict(ccaps)
        except Exception as e:
            print("No Crypto:")
            print(" %s" % e)
    return 0


if __name__ == "__main__":  # pragma: no cover
    main()
