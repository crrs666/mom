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

import os
from subprocess import *
from mom.Collectors.Collector import *

class HostKSM(Collector):
    """
    This Collctor returns statistics about the Kernel Samepage Merging daemon
    by reading files in /sys/kernel/mm/ksm/.  The fields provided are:
        ksm_run - Status of the KSM daemon: 0 - Stopped, 1 - Running
        ksm_sleep_millisecs - The amount of idle time between scans (ms)
        ksm_pages_shared - The number of pages being shared
        ksm_pages_sharing - The number of sites where a shared page is in use
        ksm_pages_unshared - The number of pages that are scanned but not shared
        ksm_pages_to_scan - The number of pages to scan in each work interval
        ksm_pages_volatile - The number of pages that are changing too fast to be shared
        ksm_full_scans - The number of times all mergeable memory areas have been scanned
        ksm_shareable - Estimated amount of host memory that is eligible for sharing
        ksmd_cpu_usage - The cpu usage of kernel thread ksmd during the monitor interval
        ksm_share_across_nodes - Toggle, policy_string, Share memory pages across all
                                 NUMA nodes = 1, default = 1
    """

    sysfs_keys = [ 'full_scans', 'pages_sharing', 'pages_unshared', 'run',
                   'pages_shared', 'pages_to_scan', 'pages_volatile',
                   'sleep_millisecs', 'merge_across_nodes']

    def __init__(self, properties):
        self.open_files()
        self.interval = properties['interval']
        self.pid = self._get_ksmd_pid()
        self.last_jiff = self.get_ksmd_jiffies()

    def __del__(self):
        for datum in self.sysfs_keys:
            if datum in self.files and self.files[datum] is not None:
                self.files[datum].close()

    def _get_ksmd_pid(self):
        proc = Popen(['pidof', 'ksmd'], stdout=PIPE)
        out = proc.communicate()[0]
        if proc.returncode == 0:
            return int(out)
        else:
            return None

    def open_files(self):
        self.files = {}
        for datum in self.sysfs_keys:
            name = '/sys/kernel/mm/ksm/%s' % datum
            try:
                self.files[datum] = open(name, 'r')
            except IOError as e:
                raise FatalError("HostKSM: open %s failed: %s" % (name, e.strerror))

    def get_ksmd_jiffies(self):
        if self.pid is None:
            return 0
        else:
            return sum(map(int, open('/proc/%s/stat' % self.pid)
                       .read().split()[13:15]))

    def get_ksmd_cpu_usage(self):
        """
        Calculate the cpu utilization of the ksmd kernel thread as a percentage.
        """
        cur_jiff = self.get_ksmd_jiffies()
        # Get the number of jiffies used in this interval taking counter
        # wrap-around into account.
        interval_jiffs = (cur_jiff - self.last_jiff) % 2**32
        total_jiffs = os.sysconf('SC_CLK_TCK') * self.interval
        # Calculate percentage of total jiffies during this interval.
        self.last_jiff = cur_jiff
        return int(100 * interval_jiffs / total_jiffs)

    def get_shareable_mem(self):
        """
        Estimate how much memory has been reported to KSM for potential sharing.
        We assume that qemu is reporting guest physical memory areas to KSM.
        """
        try:
            p1 = Popen(["pgrep", "qemu"], stdout=PIPE).communicate()[0]
        except OSError:
            raise CollectionError("HostKSM: Unable to execute pgrep")
        pids = p1.split()
        if len(pids) == 0:
            return 0
        ps_argv = ["ps", "-ovsz", "h"] + pids
        p1 = Popen(ps_argv, stdout=PIPE).communicate()[0]
        mem_tot = 0
        for mem in p1.split():
            mem_tot = mem_tot + int(mem)
        return mem_tot

    def collect(self):
        data = {}
        for (datum, file) in self.files.items():
            file.seek(0)
            data['ksm_' + datum] = parse_int('(.*)', file.read())
        data['ksm_shareable'] = self.get_shareable_mem()
        data['ksmd_cpu_usage'] = self.get_ksmd_cpu_usage()
        return data

    def getFields(self):
        return {'ksm_' + x for x in HostKSM.sysfs_keys} | \
               {'ksm_shareable', 'ksmd_cpu_usage'}
