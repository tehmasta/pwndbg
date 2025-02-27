import gdb

import pwndbg.chain
import pwndbg.commands
import pwndbg.gdblib.arch
import pwndbg.regs
import pwndbg.vmmap


@pwndbg.commands.ArgparsedCommand("Print out the stack addresses that contain return addresses.")
@pwndbg.commands.OnlyWhenRunning
def retaddr():
    sp = pwndbg.regs.sp
    stack = pwndbg.vmmap.find(sp)

    # Enumerate all return addresses
    frame = gdb.newest_frame()
    addresses = []
    while frame:
        addresses.append(frame.pc())
        frame = frame.older()

    # Find all of them on the stack
    start = stack.vaddr
    stop = start + stack.memsz
    while addresses and start < sp < stop:
        value = pwndbg.memory.u(sp)

        if value in addresses:
            index = addresses.index(value)
            del addresses[:index]
            print(pwndbg.chain.format(sp))

        sp += pwndbg.gdblib.arch.ptrsize
