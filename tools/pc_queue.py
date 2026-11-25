r"""The queue for the PC: one line shared with the user's other projects (Beastking, CompanionVR). All of it is in C:\PROJECTS\PCQueue\pc_queue.py,
whose head says how to use it. PoE2's line: tests only, one at a time, always booked --no-gpu (the queue sets it), at most 6 LuaJIT processes
(builder/pob/slots.py) and 8 GB for the whole run (the queue's job object); paused on a hold, and while a measure runs.

    python tools/pc_queue.py run <JOB> test <minutes> "PoE2: <what>" -- <command ...>
    python tools/pc_queue.py status | free | history [hours] | cancel <JOB>

PoE2's own guard (the manager, 24 Nov 2026, after a check found six job ids booked twice in six hours, the same tests run twice):
a `run` whose JOB is already in the line (waiting or running) is refused here, before the shared queue sees it. A second booking
usually comes from a chain script restarted after the 2-hour shell limit while its first client still waited. Check `status`;
`cancel <JOB>` an orphan, or wait for it; never book one id twice.
"""
import importlib.util, json, sys

sys.dont_write_bytecode = True
LINE = r"C:\PROJECTS\PCQueue\pc_queue.json"
if len(sys.argv) > 2 and sys.argv[1] == "run":
    job = sys.argv[2]
    try:
        with open(LINE, encoding="utf-8") as f:
            line = json.load(f)
    except (OSError, ValueError):
        line = []
    held = [e for e in line if isinstance(e, dict) and e.get("project") == "poe2" and e.get("job") == job]
    if held:
        e = held[0]
        state = "running since " + e["started"] if e.get("started") else "waiting since " + str(e.get("asked", "?"))
        sys.exit("pc_queue (PoE2 guard): %s is already in the line (%s: %s). Booking it again would run the same work twice. "
                 "Wait for it, or `python tools/pc_queue.py cancel %s` if it is an orphan; a new booking takes a NEW id."
                 % (job, state, str(e.get("what", ""))[:90], job))
spec = importlib.util.spec_from_file_location("pc_queue_shared", r"C:\PROJECTS\PCQueue\pc_queue.py")
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)
shared.main("poe2", sys.argv[1:])
