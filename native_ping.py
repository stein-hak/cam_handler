#!/usr/bin/env python

from execute import execute

def ping(host):

    out,err,rc = execute(['ping','-c','2',host])

    if rc == 0:
        return True

    else:
        return False