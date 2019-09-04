# Memory Overcommitment Manager
# Copyright (C) 2010 Adam Litke, IBM Corporation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public
# License along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

from mom.Collectors.Collector import *

class HostMemory(Collector):
    """
    This Collctor returns memory statistics about the host by examining
    /proc/meminfo and /proc/vmstat.  The fields provided are:
        mem_available - The total amount of available memory (kB)
        mem_unused    - The amount of memory that is not being used for any purpose (kB)
        mem_free      - The amount of free memory including some caches (kB)
        swap_in       - The amount of memory swapped in since the last collection (pages)
        swap_out      - The amount of memory swapped out since the last collection (pages)
        anon_pages    - The amount of memory used for anonymous memory areas (kB)
    """
    def __init__(self, properties):
        self.meminfo = open_datafile("/proc/meminfo")
        self.vmstat = open_datafile("/proc/vmstat")
        self.swap_in_prev = None
        self.swap_in_cur = None
        self.swap_out_prev = None
        self.swap_out_cur = None

    def __del__(self):
        if self.meminfo is not None:
            self.meminfo.close()
        if self.vmstat is not None:
            self.vmstat.close()

    def collect(self):
        self.meminfo.seek(0)
        self.vmstat.seek(0)

        contents = self.meminfo.read()
        avail = parse_int("^MemTotal: (.*) kB", contents)
        anon = parse_int("^AnonPages: (.*) kB", contents)
        unused = parse_int("^MemFree: (.*) kB", contents)
        buffers = parse_int("^Buffers: (.*) kB", contents)
        cached = parse_int("^Cached: (.*) kB", contents)
        free = unused + buffers + cached
        swap_total = parse_int("^SwapTotal: (.*) kB", contents)
        swap_free = parse_int("^SwapFree: (.*) kB", contents)

        # /proc/vmstat reports cumulative statistics so we must subtract the
        # previous values to get the difference since the last collection.
        contents = self.vmstat.read()
        self.swap_in_prev = self.swap_in_cur
        self.swap_out_prev = self.swap_out_cur
        self.swap_in_cur = parse_int("^pswpin (.*)", contents)
        self.swap_out_cur = parse_int("^pswpout (.*)", contents)
        if self.swap_in_prev is None:
            self.swap_in_prev = self.swap_in_cur
        if self.swap_out_prev is None:
            self.swap_out_prev = self.swap_out_cur
        swap_in = self.swap_in_cur - self.swap_in_prev
        swap_out = self.swap_out_cur - self.swap_out_prev


        data = { 'mem_available': avail, 'mem_unused': unused, \
                 'mem_free': free, 'swap_in': swap_in, 'swap_out': swap_out, \
                 'anon_pages': anon, 'swap_total': swap_total, \
                 'swap_usage': swap_total - swap_free }
        return data

    def getFields(self):
        return {'mem_available', 'mem_unused', 'mem_free', 'swap_in', 'swap_out',
                'anon_pages', 'swap_total', 'swap_usage'}
