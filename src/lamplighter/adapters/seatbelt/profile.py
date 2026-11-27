"""The two profile texts of the wall, and the hash that names them.

The language is Apple's sandbox profile language, which Apple does not
document. In it the last rule that matches decides, so the order below is
the meaning: reads are allowed, closed under the home folders and the temp
folders, opened again for the two folders, and the store is closed last.
There is no rule about the network, so under `(deny default)` every
connection is refused.
"""

import hashlib

from lamplighter.ports import Profile, canonical_json

# What a command line program needs to start under `(deny default)`: the
# base policy that Codex ships, with eight rules left out. Four are for a
# terminal, which a command here does not have. The others are a system
# value Java writes, one device class, OpenMP's shared memory and the power
# service, which nothing here runs yet.
_BASE_RULES = """(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow signal (target same-sandbox))
(allow process-info* (target same-sandbox))
(allow file-write-data
  (require-all (path "/dev/null") (vnode-type CHARACTER-DEVICE)))
(allow sysctl-read
  (sysctl-name "hw.activecpu")
  (sysctl-name "hw.busfrequency_compat")
  (sysctl-name "hw.byteorder")
  (sysctl-name "hw.cacheconfig")
  (sysctl-name "hw.cachelinesize_compat")
  (sysctl-name "hw.cpufamily")
  (sysctl-name "hw.cpufrequency_compat")
  (sysctl-name "hw.cputype")
  (sysctl-name "hw.l1dcachesize_compat")
  (sysctl-name "hw.l1icachesize_compat")
  (sysctl-name "hw.l2cachesize_compat")
  (sysctl-name "hw.l3cachesize_compat")
  (sysctl-name "hw.logicalcpu_max")
  (sysctl-name "hw.machine")
  (sysctl-name "hw.model")
  (sysctl-name "hw.memsize")
  (sysctl-name "hw.ncpu")
  (sysctl-name "hw.nperflevels")
  (sysctl-name-prefix "hw.optional.arm.")
  (sysctl-name-prefix "hw.optional.armv8_")
  (sysctl-name "hw.packages")
  (sysctl-name "hw.pagesize_compat")
  (sysctl-name "hw.pagesize")
  (sysctl-name "hw.physicalcpu")
  (sysctl-name "hw.physicalcpu_max")
  (sysctl-name "hw.logicalcpu")
  (sysctl-name "hw.cpufrequency")
  (sysctl-name "hw.tbfrequency_compat")
  (sysctl-name "hw.vectorunit")
  (sysctl-name "machdep.cpu.brand_string")
  (sysctl-name "kern.argmax")
  (sysctl-name "kern.hostname")
  (sysctl-name "kern.maxfilesperproc")
  (sysctl-name "kern.maxproc")
  (sysctl-name "kern.osproductversion")
  (sysctl-name "kern.osrelease")
  (sysctl-name "kern.ostype")
  (sysctl-name "kern.osvariant_status")
  (sysctl-name "kern.osversion")
  (sysctl-name "kern.secure_kernel")
  (sysctl-name "kern.sysv.semmns")
  (sysctl-name "kern.usrstack64")
  (sysctl-name "kern.version")
  (sysctl-name "sysctl.proc_cputype")
  (sysctl-name "vm.loadavg")
  (sysctl-name-prefix "hw.perflevel")
  (sysctl-name-prefix "kern.proc.pgrp.")
  (sysctl-name-prefix "kern.proc.pid.")
  (sysctl-name-prefix "net.routetable."))
(allow mach-lookup
  (global-name "com.apple.system.opendirectoryd.libinfo"))
(allow ipc-posix-sem)
"""

# `/private/var/folders` holds every user's temp folder. Closing it broke
# none of git, Python, mktemp, sort, a process pool or a here-document on a
# Mac, because a command's temp folder is in the scratch folder. A folder's
# own details stay readable everywhere: git looks at every folder above the
# work folder, and stops with "Invalid path '/Users'" when it cannot.
_READ = """
(allow file-read*)
(deny file-read*
  (subpath "/Users")
  (subpath "/Volumes")
  (subpath "/Library/Keychains")
  (subpath "/private/var/root")
  (subpath "/private/var/folders"))
(allow file-read-metadata (vnode-type DIRECTORY))
(allow file-read* (subpath (param "WORK")) (subpath (param "SCRATCH")))
"""

# The two folders may be written, but not removed or renamed themselves, so
# a command cannot swap one for a link to somewhere else.
_WRITE = """
(allow file-write* (subpath (param "WORK")) (subpath (param "SCRATCH")))
(deny file-write-unlink
  (require-all (literal (param "WORK")) (vnode-type DIRECTORY)))
(deny file-write-unlink
  (require-all (literal (param "SCRATCH")) (vnode-type DIRECTORY)))
"""

_STATE = """
(deny file-read* file-write* (subpath (param "STATE")))
"""

PROFILES: dict[Profile, str] = {
    "read_only": "\n".join(rules.strip() for rules in (_BASE_RULES, _READ, _STATE)),
    "workspace_write": "\n".join(
        rules.strip() for rules in (_BASE_RULES, _READ, _WRITE, _STATE)
    ),
}


def profiles_sha256() -> str:
    """Give the SHA-256 of the two texts, as the wall's record names them."""
    named = ["profiles", PROFILES["read_only"], PROFILES["workspace_write"]]
    return hashlib.sha256(canonical_json(named).encode("utf-8")).hexdigest()
