# This file is part of Xpra.
# Copyright (C) 2018 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

def logger():
    from xpra.log import Logger
    return Logger("network", "util", "win32")

def get_interface_info(_fd, iface):
    log = logger()
    from xpra.platform.win32.comtypes_util import QuietenLogging
    with QuietenLogging():
        try:
            from comtypes import CoInitialize               #@UnresolvedImport
            CoInitialize()
            from comtypes.client import CreateObject        #@UnresolvedImport
            o = CreateObject("WbemScripting.SWbemLocator")
            s = o.ConnectServer(".", "root\\cimv2")
            query = "SELECT * FROM Win32_NetworkAdapter WHERE GUID='%s'" % iface
            res = s.ExecQuery(query)
            log("ExecQuery(%s) returned %i rows", query, res.Count)
            if res.Count==1:
                for r in res:
                    props = {}
                    for k,ik,conv in (
                        ("AdapterType", "adapter-type", str),
                        ("Caption",     "caption",      str),
                        ("Description", "description",  str),
                        ("DeviceID",    "id",           int),
                        ("GUID",        "GUID",         str),
                        ("Index",       "index",        int),
                        ("Name",        "name",         str),
                        ("ProductName", "product-name", str),
                        ("Speed",       "speed",        int),
                        ):
                        try:
                            v = conv(r.Properties_[k].Value)
                        except Exception as e:
                            log.error("Error retrieving '%s' from network adapter record:", k)
                            log.error(" %s", e)
                        else:
                            props[ik] = v
                    log("get_interface_info(%s)=%s" % (iface, props))
                    return props
        except Exception as e:
            log("get_interface_info(%s)", iface, exc_info=True)
            from xpra.util import first_time
            if first_time("win32-network-query"):
                log.info("cannot query network interface:")
                log.info(" %s", e)
        return {}


def main():
    from xpra.platform import program_context
    with program_context("Network-Speed", "Network Speed Query Tool"):
        from xpra.net.net_util import get_interfaces
        from xpra.simple_stats import std_unit
        interfaces = get_interfaces()
        for iface in interfaces:
            speed = get_interface_info(0, iface).get("speed", 0)
            try:
                v = int(speed)
                s = "%sbps" % std_unit(v)
                print("%s : %s" % (iface, s))
            except ValueError:
                logger().error("Error: parsing speed value '%s'", speed, exc_info=True)

if __name__ == "__main__":
    main()
