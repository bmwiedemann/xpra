# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2013, 2014 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.


from threading import Thread, Lock
from queue import Queue

from xpra.log import Logger
log = Logger("util")


class Worker_Thread(Thread):
    """
        A background thread which calls the functions we post to it.
        The functions are placed in a queue and only called once,
        when this thread gets around to it.
    """

    def __init__(self):
        super().__init__(name="Worker_Thread")
        self.items = Queue()
        self.exit = False
        self.setDaemon(True)

    def __repr__(self):
        return "Worker_Thread(items=%s, exit=%s)" % (self.items.qsize(), self.exit)

    def stop(self, force=False):
        if self.exit:
            return
        items = tuple(x for x in self.items.queue if x is not None)
        log("Worker_Thread.stop(%s) %i items still in work queue: %s", force, len(items), items)
        if force:
            if items:
                log.warn("Worker stop: %s items in the queue will not be run!", len(items))
                self.items.put(None)
                self.items = Queue()
            self.exit = True
        else:
            if items:
                log.info("waiting for %s items in work queue to complete", len(items))
        self.items.put(None)

    def add(self, item, allow_duplicates=True):
        if self.items.qsize()>10:
            log.warn("Worker_Thread.items queue size is %s", self.items.qsize())
        if not allow_duplicates:
            if item in self.items.queue:
                return
        self.items.put(item)

    def run(self):
        log("Worker_Thread.run() starting")
        while not self.exit:
            item = self.items.get()
            if item is None:
                log("Worker_Thread.run() found end of queue marker")
                self.exit = True
                break
            try:
                log("Worker_Thread.run() calling %s (queue size=%s)", item, self.items.qsize())
                item()
            except Exception:
                log.error("Error in worker thread processing item %s", item, exc_info=True)
        log("Worker_Thread.run() ended (queue size=%s)", self.items.qsize())

#only one worker thread for now:
singleton = None
#locking to ensure multi-threaded code doesn't create more than one
lock = Lock()

def get_worker(create=True):
    global singleton
    #fast path (no lock):
    if singleton is not None or not create:
        return singleton
    with lock:
        if not singleton:
            singleton = Worker_Thread()
            singleton.start()
    return singleton

def add_work_item(item, allow_duplicates=True):
    w = get_worker(True)
    log("add_work_item(%s, %s) worker=%s", item, allow_duplicates, w)
    w.add(item, allow_duplicates)

def stop_worker(force=False):
    w = get_worker(False)
    log("stop_worker(%s) worker=%s", force, w)
    if w:
        w.stop(force)
