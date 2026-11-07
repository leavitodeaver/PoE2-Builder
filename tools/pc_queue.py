"""The queue for the PC: one line shared with the user's other projects (Beastking, CompanionVR). All of it is in C:\PROJECTS\PCQueue\pc_queue.py,
whose head says how to use it. PoE2's line: tests only, one at a time, always booked --no-gpu (the queue sets it), at most 6 LuaJIT processes
(builder/pob/slots.py) and 8 GB for the whole run (the queue's job object); paused on a hold, and while a measure runs.

    python tools/pc_queue.py run <JOB> test <minutes> "PoE2: <what>" -- <command ...>
    python tools/pc_queue.py status | free | history [hours] | cancel <JOB>
"""
import importlib.util, sys

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("pc_queue_shared", r"C:\PROJECTS\PCQueue\pc_queue.py")
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)
shared.main("poe2", sys.argv[1:])
