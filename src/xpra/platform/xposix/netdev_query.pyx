# This file is part of Xpra.
# Copyright (C) 2017-2018 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from __future__ import absolute_import

import os

from libc.stdint cimport uintptr_t, uint32_t, uint16_t, uint8_t

from xpra.os_util import strtobytes
from xpra.log import Logger
log = Logger("util", "network")

ctypedef uint32_t __u32
ctypedef uint16_t __u16
ctypedef uint8_t __u8
cdef extern from "linux/ethtool.h":
    int ETHTOOL_GSET
    cdef struct ethtool_cmd:
        __u32   cmd
        __u32   supported
        __u32   advertising
        __u16   speed
        __u8    duplex
        __u8    port
        __u8    phy_address
        __u8    transceiver
        __u8    autoneg
        __u8    mdio_support
        __u32   maxtxpkt
        __u32   maxrxpkt
        __u16   speed_hi
        __u8    eth_tp_mdix
        __u8    eth_tp_mdix_ctrl
        __u32   lp_advertising
        __u32   reserved[2]


cdef extern from "linux/sockios.h":
    int SIOCETHTOOL

cdef extern from "net/if.h":
    DEF IFNAMSIZ=16
    cdef struct ifr_ifrn:
        char ifrn_name[IFNAMSIZ]
    cdef struct ifr_ifru:
        int ifru_flags
        int ifru_ivalue
        int ifru_mtu
        void *ifru_data
    cdef struct ifreq:
        ifr_ifrn ifr_ifrn
        ifr_ifru ifr_ifru

cdef extern from "sys/ioctl.h":
    int ioctl(int fd, unsigned long request, ...)


def get_interface_speed(int sockfd, ifname):
    """ returns the ethtool speed in bps, or 0 """
    if sockfd==0:
        return 0
    if len(ifname)>=IFNAMSIZ:
        log.warn("Warning: invalid interface name '%s'", ifname)
        log.warn(" maximum length is %i", IFNAMSIZ)
        return 0
    cdef ifreq ifr
    cdef ethtool_cmd edata
    bifname = strtobytes(ifname)
    cdef char *cifname = bifname
    ifr.ifr_ifrn.ifrn_name = cifname
    ifr.ifr_ifru.ifru_data = <void*> &edata
    edata.cmd = ETHTOOL_GSET
    cdef int r = ioctl(sockfd, SIOCETHTOOL, &ifr)
    if r < 0:
        log.info("no ethtool interface speed available for %s", ifname)
        return 0
    #log("get_interface_speed(%i, %s)=%i", sockfd, ifname, edata.speed*1000*1000)
    return edata.speed*1000*1000
