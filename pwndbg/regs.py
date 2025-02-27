"""
Reading register value from the inferior, and provides a
standardized interface to registers like "sp" and "pc".
"""
import collections
import ctypes
import re
import sys
from types import ModuleType

import gdb

import pwndbg.gdblib.arch
import pwndbg.gdblib.events
import pwndbg.lib.memoize
import pwndbg.proc
import pwndbg.remote


class RegisterSet:
    #: Program counter register
    pc = None

    #: Stack pointer register
    stack = None

    #: Frame pointer register
    frame = None

    #: Return address register
    retaddr = None

    #: Flags register (eflags, cpsr)
    flags = None

    #: List of native-size generalp-purpose registers
    gpr = None

    #: List of miscellaneous, valid registers
    misc = None

    #: Register-based arguments for most common ABI
    regs = None

    #: Return value register
    retval = None

    #: Common registers which should be displayed in the register context
    common = None

    #: All valid registers
    all = None

    def __init__(
        self,
        pc="pc",
        stack="sp",
        frame=None,
        retaddr=tuple(),
        flags=dict(),
        gpr=tuple(),
        misc=tuple(),
        args=tuple(),
        retval=None,
    ):
        self.pc = pc
        self.stack = stack
        self.frame = frame
        self.retaddr = retaddr
        self.flags = flags
        self.gpr = gpr
        self.misc = misc
        self.args = args
        self.retval = retval

        # In 'common', we don't want to lose the ordering of:
        self.common = []
        for reg in gpr + (frame, stack, pc) + tuple(flags):
            if reg and reg not in self.common:
                self.common.append(reg)

        self.all = set(i for i in misc) | set(flags) | set(self.retaddr) | set(self.common)
        self.all -= {None}

    def __iter__(self):
        for r in self.all:
            yield r


arm_cpsr_flags = collections.OrderedDict(
    [
        ("N", 31),
        ("Z", 30),
        ("C", 29),
        ("V", 28),
        ("Q", 27),
        ("J", 24),
        ("T", 5),
        ("E", 9),
        ("A", 8),
        ("I", 7),
        ("F", 6),
    ]
)
arm_xpsr_flags = collections.OrderedDict(
    [("N", 31), ("Z", 30), ("C", 29), ("V", 28), ("Q", 27), ("T", 24)]
)

arm = RegisterSet(
    retaddr=("lr",),
    flags={"cpsr": arm_cpsr_flags},
    gpr=("r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "r12"),
    args=("r0", "r1", "r2", "r3"),
    retval="r0",
)

# ARM Cortex-M
armcm = RegisterSet(
    retaddr=("lr",),
    flags={"xpsr": arm_xpsr_flags},
    gpr=("r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "r12"),
    args=("r0", "r1", "r2", "r3"),
    retval="r0",
)

# FIXME AArch64 does not have a CPSR register
aarch64 = RegisterSet(
    retaddr=("lr",),
    flags={"cpsr": {}},
    # X29 is the frame pointer register (FP) but setting it
    # as frame here messes up the register order to the point
    # it's confusing. Think about improving this if frame
    # pointer semantics are required for other functionalities.
    # frame   = 'x29',
    gpr=(
        "x0",
        "x1",
        "x2",
        "x3",
        "x4",
        "x5",
        "x6",
        "x7",
        "x8",
        "x9",
        "x10",
        "x11",
        "x12",
        "x13",
        "x14",
        "x15",
        "x16",
        "x17",
        "x18",
        "x19",
        "x20",
        "x21",
        "x22",
        "x23",
        "x24",
        "x25",
        "x26",
        "x27",
        "x28",
        "x29",
        "x30",
    ),
    misc=(
        "w0",
        "w1",
        "w2",
        "w3",
        "w4",
        "w5",
        "w6",
        "w7",
        "w8",
        "w9",
        "w10",
        "w11",
        "w12",
        "w13",
        "w14",
        "w15",
        "w16",
        "w17",
        "w18",
        "w19",
        "w20",
        "w21",
        "w22",
        "w23",
        "w24",
        "w25",
        "w26",
        "w27",
        "w28",
    ),
    args=("x0", "x1", "x2", "x3"),
    retval="x0",
)

x86flags = {
    "eflags": collections.OrderedDict(
        [
            ("CF", 0),
            ("PF", 2),
            ("AF", 4),
            ("ZF", 6),
            ("SF", 7),
            ("IF", 9),
            ("DF", 10),
            ("OF", 11),
        ]
    )
}

amd64 = RegisterSet(
    pc="rip",
    stack="rsp",
    frame="rbp",
    flags=x86flags,
    gpr=(
        "rax",
        "rbx",
        "rcx",
        "rdx",
        "rdi",
        "rsi",
        "r8",
        "r9",
        "r10",
        "r11",
        "r12",
        "r13",
        "r14",
        "r15",
    ),
    misc=(
        "cs",
        "ss",
        "ds",
        "es",
        "fs",
        "gs",
        "fsbase",
        "gsbase",
        "ax",
        "ah",
        "al",
        "bx",
        "bh",
        "bl",
        "cx",
        "ch",
        "cl",
        "dx",
        "dh",
        "dl",
        "dil",
        "sil",
        "spl",
        "bpl",
        "di",
        "si",
        "bp",
        "sp",
        "ip",
    ),
    args=("rdi", "rsi", "rdx", "rcx", "r8", "r9"),
    retval="rax",
)

i386 = RegisterSet(
    pc="eip",
    stack="esp",
    frame="ebp",
    flags=x86flags,
    gpr=("eax", "ebx", "ecx", "edx", "edi", "esi"),
    misc=(
        "cs",
        "ss",
        "ds",
        "es",
        "fs",
        "gs",
        "fsbase",
        "gsbase",
        "ax",
        "ah",
        "al",
        "bx",
        "bh",
        "bl",
        "cx",
        "ch",
        "cl",
        "dx",
        "dh",
        "dl",
        "di",
        "si",
        "bp",
        "sp",
        "ip",
    ),
    retval="eax",
)

# http://math-atlas.sourceforge.net/devel/assembly/elfspec_ppc.pdf
# r0      Volatile register which may be modified during function linkage
# r1      Stack frame pointer, always valid
# r2      System-reserved register (points at GOT)
# r3-r4   Volatile registers used for parameter passing and return values
# r5-r10  Volatile registers used for parameter passing
# r11-r12 Volatile registers which may be modified during function linkage
# r13     Small data area pointer register (points to TLS)
# r14-r30 Registers used for local variables
# r31     Used for local variables or "environment pointers"
powerpc = RegisterSet(
    retaddr=("lr",),
    flags={"msr": {}, "xer": {}},
    gpr=(
        "r0",
        "r1",
        "r2",
        "r3",
        "r4",
        "r5",
        "r6",
        "r7",
        "r8",
        "r9",
        "r10",
        "r11",
        "r12",
        "r13",
        "r14",
        "r15",
        "r16",
        "r17",
        "r18",
        "r19",
        "r20",
        "r21",
        "r22",
        "r23",
        "r24",
        "r25",
        "r26",
        "r27",
        "r28",
        "r29",
        "r30",
        "r31",
        "cr",
        "ctr",
    ),
    args=("r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10"),
    retval="r3",
)

# http://people.cs.clemson.edu/~mark/sparc/sparc_arch_desc.txt
# http://people.cs.clemson.edu/~mark/subroutines/sparc.html
# https://www.utdallas.edu/~edsha/security/sparcoverflow.htm
#
# http://people.cs.clemson.edu/~mark/sparc/assembly.txt
# ____________________________________
# %g0 == %r0  (always zero)           \
# %g1 == %r1                          | g stands for global
# ...                                 |
# %g7 == %r7                          |
# ____________________________________/
# %o0 == %r8                          \
# ...                                 | o stands for output (note: not 0)
# %o6 == %r14 == %sp (stack ptr)      |
# %o7 == %r15 == for return address   |
# ____________________________________/
# %l0 == %r16                         \
# ...                                 | l stands for local (note: not 1)
# %l7 == %r23                         |
# ____________________________________/
# %i0 == %r24                         \
# ...                                 | i stands for input
# %i6 == %r30 == %fp (frame ptr)      |
# %i7 == %r31 == for return address   |
# ____________________________________/

sparc = RegisterSet(
    stack="sp",
    frame="fp",
    retaddr=("i7",),
    flags={"psr": {}},
    gpr=(
        "g1",
        "g2",
        "g3",
        "g4",
        "g5",
        "g6",
        "g7",
        "o0",
        "o1",
        "o2",
        "o3",
        "o4",
        "o5",
        "o7",
        "l0",
        "l1",
        "l2",
        "l3",
        "l4",
        "l5",
        "l6",
        "l7",
        "i0",
        "i1",
        "i2",
        "i3",
        "i4",
        "i5",
    ),
    args=("i0", "i1", "i2", "i3", "i4", "i5"),
    retval="o0",
)

# http://logos.cs.uic.edu/366/notes/mips%20quick%20tutorial.htm
# r0        => zero
# r1        => temporary
# r2-r3     => values
# r4-r7     => arguments
# r8-r15    => temporary
# r16-r23   => saved values
# r24-r25   => temporary
# r26-r27   => interrupt/trap handler
# r28       => global pointer
# r29       => stack pointer
# r30       => frame pointer
# r31       => return address
mips = RegisterSet(
    frame="fp",
    retaddr=("ra",),
    gpr=(
        "v0",
        "v1",
        "a0",
        "a1",
        "a2",
        "a3",
        "t0",
        "t1",
        "t2",
        "t3",
        "t4",
        "t5",
        "t6",
        "t7",
        "t8",
        "t9",
        "s0",
        "s1",
        "s2",
        "s3",
        "s4",
        "s5",
        "s6",
        "s7",
        "s8",
        "gp",
    ),
    args=("a0", "a1", "a2", "a3"),
    retval="v0",
)

# https://riscv.org/technical/specifications/
# Volume 1, Unprivileged Spec v. 20191213
# Chapter 25 - RISC-V Assembly Programmer’s Handbook
# x0        => zero   (Hard-wired zero)
# x1        => ra     (Return address)
# x2        => sp     (Stack pointer)
# x3        => gp     (Global pointer)
# x4        => tp     (Thread pointer)
# x5        => t0     (Temporary/alternate link register)
# x6–7      => t1–2   (Temporaries)
# x8        => s0/fp  (Saved register/frame pointer)
# x9        => s1     (Saved register)
# x10-11    => a0–1   (Function arguments/return values)
# x12–17    => a2–7   (Function arguments)
# x18–27    => s2–11  (Saved registers)
# x28–31    => t3–6   (Temporaries)
# f0–7      => ft0–7  (FP temporaries)
# f8–9      => fs0–1  (FP saved registers)
# f10–11    => fa0–1  (FP arguments/return values)
# f12–17    => fa2–7  (FP arguments)
# f18–27    => fs2–11 (FP saved registers)
# f28–31    => ft8–11 (FP temporaries)
riscv = RegisterSet(
    pc="pc",
    frame="fp",
    stack="sp",
    retaddr="ra",
    gpr=(
        "ra",
        "gp",
        "tp",
        "t0",
        "t1",
        "t2",
        "fp",
        "s1",
        "a0",
        "a1",
        "a2",
        "a3",
        "a4",
        "a5",
        "a6",
        "a7",
        "s2",
        "s3",
        "s4",
        "s5",
        "s6",
        "s7",
        "s8",
        "s9",
        "s10",
        "s11",
        "t3",
        "t4",
        "t5",
        "t6",
        "ft0",
        "ft1",
        "ft2",
        "ft3",
        "ft4",
        "ft5",
        "ft6",
        "ft7",
        "fs0",
        "fs1",
        "fa0",
        "fa1",
        "fa2",
        "fa3",
        "fa4",
        "fa5",
        "fa6",
        "fa7",
        "fs2",
        "fs3",
        "fs4",
        "fs5",
        "fs6",
        "fs7",
        "fs8",
        "fs9",
        "fs10",
        "fs11",
        "ft8",
        "ft9",
        "ft10",
        "ft11",
    ),
    retval=(
        "a0",
        "a1",
        "fa0",
        "fa1",
    ),
    args=(
        "a0",
        "a1",
        "a2",
        "a3",
        "a4",
        "a5",
        "a6",
        "a7",
        "fa0",
        "fa1",
        "fa2",
        "fa3",
        "fa4",
        "fa5",
        "fa6",
        "fa7",
    ),
)


arch_to_regs = {
    "i386": i386,
    "i8086": i386,
    "x86-64": amd64,
    "mips": mips,
    "sparc": sparc,
    "arm": arm,
    "armcm": armcm,
    "aarch64": aarch64,
    "powerpc": powerpc,
    "riscv:rv64": riscv,
}


@pwndbg.proc.OnlyWhenRunning
def gdb77_get_register(name):
    return gdb.parse_and_eval("$" + name)


@pwndbg.proc.OnlyWhenRunning
def gdb79_get_register(name):
    return gdb.selected_frame().read_register(name)


try:
    gdb.Frame.read_register
    get_register = gdb79_get_register
except AttributeError:
    get_register = gdb77_get_register


# We need to manually make some ptrace calls to get fs/gs bases on Intel
PTRACE_ARCH_PRCTL = 30
ARCH_GET_FS = 0x1003
ARCH_GET_GS = 0x1004


class module(ModuleType):
    last = {}

    @pwndbg.lib.memoize.reset_on_stop
    @pwndbg.lib.memoize.reset_on_prompt
    def __getattr__(self, attr):
        attr = attr.lstrip("$")
        try:
            # Seriously, gdb? Only accepts uint32.
            if "eflags" in attr or "cpsr" in attr:
                value = gdb77_get_register(attr)
                value = value.cast(pwndbg.gdblib.typeinfo.uint32)
            else:
                value = get_register(attr)
                if value is None and attr.lower() == "xpsr":
                    value = get_register("xPSR")
                size = pwndbg.gdblib.typeinfo.unsigned.get(
                    value.type.sizeof, pwndbg.gdblib.typeinfo.ulong
                )
                value = value.cast(size)
                if attr.lower() == "pc" and pwndbg.gdblib.arch.current == "i8086":
                    value += self.cs * 16

            value = int(value)
            return value & pwndbg.gdblib.arch.ptrmask
        except (ValueError, gdb.error):
            return None

    @pwndbg.lib.memoize.reset_on_stop
    @pwndbg.lib.memoize.reset_on_prompt
    def __getitem__(self, item):
        if not isinstance(item, str):
            print("Unknown register type: %r" % (item))
            import pdb
            import traceback

            traceback.print_stack()
            pdb.set_trace()
            return None

        # e.g. if we're looking for register "$rax", turn it into "rax"
        item = item.lstrip("$")
        item = getattr(self, item.lower())

        if isinstance(item, int):
            return int(item) & pwndbg.gdblib.arch.ptrmask

        return item

    def __iter__(self):
        regs = set(arch_to_regs[pwndbg.gdblib.arch.current]) | {"pc", "sp"}
        for item in regs:
            yield item

    @property
    def current(self):
        return arch_to_regs[pwndbg.gdblib.arch.current]

    @property
    def gpr(self):
        return arch_to_regs[pwndbg.gdblib.arch.current].gpr

    @property
    def common(self):
        return arch_to_regs[pwndbg.gdblib.arch.current].common

    @property
    def frame(self):
        return arch_to_regs[pwndbg.gdblib.arch.current].frame

    @property
    def retaddr(self):
        return arch_to_regs[pwndbg.gdblib.arch.current].retaddr

    @property
    def flags(self):
        return arch_to_regs[pwndbg.gdblib.arch.current].flags

    @property
    def stack(self):
        return arch_to_regs[pwndbg.gdblib.arch.current].stack

    @property
    def retval(self):
        return arch_to_regs[pwndbg.gdblib.arch.current].retval

    @property
    def all(self):
        regs = arch_to_regs[pwndbg.gdblib.arch.current]
        retval = []
        for regset in (
            regs.pc,
            regs.stack,
            regs.frame,
            regs.retaddr,
            regs.flags,
            regs.gpr,
            regs.misc,
        ):
            if regset is None:
                continue
            elif isinstance(regset, (list, tuple)):
                retval.extend(regset)
            elif isinstance(regset, dict):
                retval.extend(regset.keys())
            else:
                retval.append(regset)
        return retval

    def fix(self, expression):
        for regname in set(self.all + ["sp", "pc"]):
            expression = re.sub(r"\$?\b%s\b" % regname, r"$" + regname, expression)
        return expression

    def items(self):
        for regname in self.all:
            yield regname, self[regname]

    arch_to_regs = arch_to_regs

    @property
    def changed(self):
        delta = []
        for reg, value in self.previous.items():
            if self[reg] != value:
                delta.append(reg)
        return delta

    @property
    @pwndbg.lib.memoize.reset_on_stop
    def fsbase(self):
        return self._fs_gs_helper("fs_base", ARCH_GET_FS)

    @property
    @pwndbg.lib.memoize.reset_on_stop
    def gsbase(self):
        return self._fs_gs_helper("gs_base", ARCH_GET_GS)

    @pwndbg.lib.memoize.reset_on_stop
    def _fs_gs_helper(self, regname, which):
        """Supports fetching based on segmented addressing, a la fs:[0x30].
        Requires ptrace'ing the child directly for GDB < 8."""

        # For GDB >= 8.x we can use get_register directly if the current arch is x86-64
        # Elsewhere we have to get the register via ptrace
        if pwndbg.gdblib.arch.current == "x86-64":
            if get_register == gdb79_get_register:
                return get_register(regname)

        # We can't really do anything if the process is remote.
        if pwndbg.remote.is_remote():
            return 0

        # Use the lightweight process ID
        pid, lwpid, tid = gdb.selected_thread().ptid

        # Get the register
        ppvoid = ctypes.POINTER(ctypes.c_void_p)
        value = ppvoid(ctypes.c_void_p())
        value.contents.value = 0

        libc = ctypes.CDLL("libc.so.6")
        result = libc.ptrace(PTRACE_ARCH_PRCTL, lwpid, value, which)

        if result == 0:
            return (value.contents.value or 0) & pwndbg.gdblib.arch.ptrmask

        return 0

    def __repr__(self):
        return "<module pwndbg.regs>"


# To prevent garbage collection
tether = sys.modules[__name__]
sys.modules[__name__] = module(__name__, "")


@pwndbg.gdblib.events.cont
@pwndbg.gdblib.events.stop
def update_last():
    M = sys.modules[__name__]
    M.previous = M.last
    M.last = {k: M[k] for k in M.common}
    if pwndbg.config.show_retaddr_reg:
        M.last.update({k: M[k] for k in M.retaddr})
