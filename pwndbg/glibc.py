"""
Get information about the GLibc
"""

import functools
import re

import gdb

import pwndbg.config
import pwndbg.heap
import pwndbg.lib.memoize
import pwndbg.memory
import pwndbg.proc
import pwndbg.search
import pwndbg.symbol

safe_lnk = pwndbg.config.Parameter(
    "safe-linking", "auto", "whether glibc use safe-linking (on/off/auto)"
)

glibc_version = pwndbg.config.Parameter("glibc", "", "GLIBC version for heuristics", scope="heap")


@pwndbg.proc.OnlyWhenRunning
def get_version():
    if glibc_version.value:
        ret = re.search(r"(\d+)\.(\d+)", glibc_version.value)
        if ret:
            return tuple(int(_) for _ in ret.groups())
        else:
            raise ValueError(
                "Invalid GLIBC version: `%s`, you should provide something like: 2.31 or 2.34"
                % glibc_version.value
            )
    return _get_version()


@pwndbg.proc.OnlyWhenRunning
@pwndbg.lib.memoize.reset_on_start
@pwndbg.lib.memoize.reset_on_objfile
def _get_version():
    if pwndbg.heap.current.libc_has_debug_syms():
        addr = pwndbg.symbol.address(b"__libc_version")
        if addr is not None:
            ver = pwndbg.memory.string(addr)
            return tuple([int(_) for _ in ver.split(b".")])
    for addr in pwndbg.search.search(b"GNU C Library"):
        banner = pwndbg.memory.string(addr)
        ret = re.search(rb"release version (\d+)\.(\d+)", banner)
        if ret:
            return tuple(int(_) for _ in ret.groups())
    return None


def OnlyWhenGlibcLoaded(function):
    @functools.wraps(function)
    def _OnlyWhenGlibcLoaded(*a, **kw):
        if get_version() is not None:
            return function(*a, **kw)
        else:
            print("%s: GLibc not loaded yet." % function.__name__)

    return _OnlyWhenGlibcLoaded


@OnlyWhenGlibcLoaded
def check_safe_linking():
    """
    Safe-linking is a glibc 2.32 mitigation; see:
    - https://lanph3re.blogspot.com/2020/08/blog-post.html
    - https://research.checkpoint.com/2020/safe-linking-eliminating-a-20-year-old-malloc-exploit-primitive/
    """
    return (get_version() >= (2, 32) or safe_lnk == "on") and safe_lnk != "off"
