#!/usr/bin/env python3
"""
Script to test CPU load imposed by a simple disk read operation.

Copyright (c) 2016 Canonical Ltd.

Authors:
    Rod Smith <rod.smith@canonical.com>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License version 3,
as published by the Free Software Foundation.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.

The purpose of this script is to measure the CPU load imposed by a
simple disk read operation using dd.

Usage:
    disk_cpu_load.py [--max-load LOAD] [--xfer MEBIBYTES]
                     [--verbose] [<device-filename>]

Parameters:
    --max-load LOAD     The maximum acceptable CPU load, as a percentage.
                        Defaults to 30.
    --xfer MEBIBYTES    The amount of data to read from the disk, in
                        mebibytes. Defaults to 4096 (4 GiB).
    --verbose           If present, produce more verbose output.
    <device-filename>   This is the WHOLE-DISK device filename (with or
                        without "/dev/"), e.g. "sda" or "/dev/sda". The
                        script finds a filesystem on that device, mounts
                        it if necessary, and runs the tests on that mounted
                        filesystem. Defaults to /dev/sda.
"""

import argparse
import os
import stat
import subprocess
import sys


PROC_STAT_PATH = "/proc/stat"


class CPUStats:
    """Reads and parses CPU statistics from /proc/stat."""

    def __init__(self):
        self.values = self._read()

    def _read(self):
        """Read the current aggregate CPU stats from /proc/stat."""
        with open(PROC_STAT_PATH) as f:
            for line in f:
                if line.startswith("cpu "):
                    parts = line.strip().split()
                    return [int(x) for x in parts[1:]]
        raise RuntimeError(f"Could not read CPU stats from {PROC_STAT_PATH}")

    @property
    def idle(self):
        """Return the idle time field (4th column in /proc/stat)."""
        return self.values[3]

    @property
    def total(self):
        """Return the total CPU time (sum of all fields)."""
        return sum(self.values)


class CPULoadMonitor:
    """Computes CPU load between two measurement snapshots."""

    def __init__(self, verbose=False):
        self.verbose = verbose

    def compute_load(self, start, end):
        """
        Compute CPU load percentage between two CPUStats snapshots.

        Args:
            start (CPUStats): CPU stats captured before the workload.
            end (CPUStats): CPU stats captured after the workload.

        Returns:
            int: CPU load as an integer percentage in the range 0–100.
        """
        diff_idle = end.idle - start.idle
        diff_total = end.total - start.total
        diff_used = diff_total - diff_idle

        if self.verbose:
            print(f"Start CPU time = {start.total}")
            print(f"End CPU time = {end.total}")
            print(f"CPU time used = {diff_used}")
            print(f"Total elapsed time = {diff_total}")

        if diff_total == 0:
            return 0

        return (diff_used * 100) // diff_total


class DiskCPULoadTest:
    """Measures the CPU load imposed by a sequential disk read."""

    DEFAULT_DEVICE = "/dev/sda"
    DEFAULT_MAX_LOAD = 30
    DEFAULT_XFER_MIB = 4096
    BLOCK_SIZE = 1048576  # 1 MiB in bytes

    def __init__(self, device, max_load, xfer_mib, verbose=False):
        self.device = device
        self.max_load = max_load
        self.xfer_mib = xfer_mib
        self.verbose = verbose
        self.monitor = CPULoadMonitor(verbose=verbose)

    def _flush_buffers(self):
        """Flush disk read buffers so the read is not served from cache."""
        subprocess.run(
            ["blockdev", "--flushbufs", self.device],
            check=True,
            capture_output=True,
        )

    def _read_disk(self):
        """Read xfer_mib mebibytes from the device using dd."""
        if self.verbose:
            print("Beginning disk read....")
        subprocess.run(
            [
                "dd",
                f"if={self.device}",
                "of=/dev/null",
                f"bs={self.BLOCK_SIZE}",
                f"count={self.xfer_mib}",
            ],
            check=True,
            capture_output=True,
        )
        if self.verbose:
            print("Disk read complete!")

    def run(self):
        """
        Execute the disk CPU load test.

        Returns:
            int: 0 if the measured CPU load is within the acceptable
                 limit, 1 if the limit is exceeded.
        """
        print(
            f"Testing CPU load when reading {self.xfer_mib} MiB"
            f" from {self.device}"
        )
        print(f"Maximum acceptable CPU load is {self.max_load}")

        self._flush_buffers()

        start_stats = CPUStats()
        self._read_disk()
        end_stats = CPUStats()

        cpu_load = self.monitor.compute_load(start_stats, end_stats)
        print(f"Detected disk read CPU load is {cpu_load}")

        if cpu_load > self.max_load:
            print("*** DISK CPU LOAD TEST HAS FAILED! ***")
            return 1

        return 0


def resolve_device(device_arg):
    """
    Resolve a device argument to a canonical /dev/ block-device path.

    Accepts device names with or without the "/dev/" prefix and removes
    any accidental double "/dev/dev/" prefix (matching the original shell
    script behaviour).

    Args:
        device_arg (str): Device name supplied on the command line.

    Returns:
        str: Absolute device path (e.g. "/dev/sda").

    Raises:
        SystemExit: If the resolved path does not refer to a block device.
    """
    if not device_arg.startswith("/dev/"):
        device = f"/dev/{device_arg}"
    else:
        device = device_arg

    # Remove an accidental double prefix (/dev//dev/ -> /dev/)
    device = device.replace("/dev//dev/", "/dev/")

    if not os.path.exists(device) or not stat.S_ISBLK(
        os.stat(device).st_mode
    ):
        print(f'Unknown block device "{device}"')
        print(
            "Usage: disk_cpu_load.py [--max-load LOAD] [--xfer MEBIBYTES]"
            " [--verbose] [device]"
        )
        sys.exit(1)

    return device


def parse_args(argv=None):
    """Build and return the argument parser for this script."""
    parser = argparse.ArgumentParser(
        description=(
            "Test the CPU load imposed by a sequential disk read operation."
        )
    )
    parser.add_argument(
        "--max-load",
        type=int,
        default=DiskCPULoadTest.DEFAULT_MAX_LOAD,
        metavar="LOAD",
        help=(
            "Maximum acceptable CPU load as a percentage"
            f" (default: {DiskCPULoadTest.DEFAULT_MAX_LOAD})"
        ),
    )
    parser.add_argument(
        "--xfer",
        type=int,
        default=DiskCPULoadTest.DEFAULT_XFER_MIB,
        metavar="MEBIBYTES",
        help=(
            "Amount of data to read from the disk in mebibytes"
            f" (default: {DiskCPULoadTest.DEFAULT_XFER_MIB})"
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Produce more verbose output",
    )
    parser.add_argument(
        "device",
        nargs="?",
        default=DiskCPULoadTest.DEFAULT_DEVICE,
        help=(
            "Whole-disk block device filename, with or without /dev/ prefix"
            f" (default: {DiskCPULoadTest.DEFAULT_DEVICE})"
        ),
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    device = resolve_device(args.device)
    test = DiskCPULoadTest(
        device=device,
        max_load=args.max_load,
        xfer_mib=args.xfer,
        verbose=args.verbose,
    )
    return test.run()


if __name__ == "__main__":
    sys.exit(main())
