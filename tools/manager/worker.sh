#!/bin/bash
# Runs a PoE2 Builder worker on another of the user's Claude accounts (6 Nov 2026; copied from Beastking's
# tools/lead/worker.sh, as its manager advised). Each account has its own folder and login: account 1 is wizzleteeks
# (C:\Users\conve\.claude-account1, a copy of the managers' login), account 2 is designkoalas, account 3 is leavitodeaver.
# Which accounts PoE2 may use is the user's call (12 Nov 2026: leavitodeaver and wizzleteeks).
# The desktop app stays on its own account; a worker run here does not show in its sidebar and cannot message the
# manager: it ends its turn to report, and the manager continues it with "more".
#
#   bash tools/manager/worker.sh <1|2|3> <JOB> new  "<what to do>"     starts it
#   bash tools/manager/worker.sh <1|2|3> <JOB> more "<message>"        continues the same worker
#
# Run it with the Bash tool's run_in_background: the manager is told when the turn ends. Its last answer is in
# scratch/manager/workers/<JOB>.last.json (its session id in <JOB>.id, each turn's answer in <JOB>.turn_<n>.json).
# Set POE2_WORKER_MODEL to choose a model.
#
# The inbox: no two turns may ever run at once on one worker. Every "more" goes into <JOB>.inbox/ first; whoever holds
# the worker's lock (<JOB>.lock) runs the turns, one after another, until the inbox is empty. A "more" sent while the
# worker is busy waits: it returns once the turn that carried it has ended.
#
# The user can write to a worker from Beastking's Workers page (http://127.0.0.1:8377; the user, 7 Nov: "can you set it
# up to show the poe2 builder's worker on the workers page"). The page runs `worker.sh <2|3> <JOB> more -` with the
# words in $WORKER_TEXT_FILE and WORKER_FROM_USER=1: the worker is told they are the user's own, and they are logged in
# scratch/manager/workers/user_messages.log for the manager to file. Keep a new worker's brief opening with
# "You are a worker on ... (job <JOB>)": that is how the page finds it.
#
# The whole script is one function, read in full before it runs, so changing this file never upsets a copy already
# running.

main() {
ACCOUNT="$1"; JOB="$2"; MODE="$3"; TEXT="$4"
if [ "$TEXT" = "-" ] && [ -f "$WORKER_TEXT_FILE" ]; then   # (the Workers page hands the user's words over in a file: no quoting trouble)
	TEXT=$(cat "$WORKER_TEXT_FILE"); rm -f "$WORKER_TEXT_FILE"
fi
if [ -z "$TEXT" ] || { [ "$ACCOUNT" != "1" ] && [ "$ACCOUNT" != "2" ] && [ "$ACCOUNT" != "3" ]; } || { [ "$MODE" != "new" ] && [ "$MODE" != "more" ]; } \
	|| ! [[ "$JOB" =~ ^[A-Za-z0-9_]+$ ]]; then
	echo "use: worker.sh <1|2|3> <JOB> new|more \"text\""; return 2
fi
ROOT1="$LOCALAPPDATA/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/claude-code"
ROOT2="$APPDATA/Claude/claude-code"
ROOT3="$LOCALAPPDATA/Claude-Data/claude_pzs8sxrjxfjjc/claude-code"   # (where the app keeps it since ~10 Nov 2026)
EXE=$(ls -t "$ROOT1"/*/*/claude.exe "$ROOT2"/*/*/claude.exe "$ROOT3"/*/*/claude.exe 2>/dev/null | head -1)
pin_self   # the user's two threads: this script, the worker and everything it starts stay on 2 and up, at Below Normal
[ -n "$EXE" ] || { echo "Claude Code not found"; return 3; }
W="C:/PROJECTS/PoE2 Builder/scratch/manager/workers"
LOCK="$W/$JOB.lock"; INBOX="$W/$JOB.inbox"
mkdir -p "$W" "$INBOX"
CLEAR=""
for v in $(env | cut -d= -f1 | grep -E '^(ANTHROPIC|CLAUDE)'); do CLEAR="$CLEAR -u $v"; done
trap 'if [ "$(cat "$LOCK/pid" 2>/dev/null)" = "$$" ]; then rm -rf "$LOCK"; fi' EXIT

if [ "$MODE" = "new" ]; then
	if ! take_lock; then
		lock_alive && { echo "$JOB is already running; send it more instead"; return 6; }
		rm -rf "$LOCK"; take_lock || { echo "could not take $JOB's lock"; return 6; }
	fi
	TEXT="$TEXT

Anything heavy on the PC joins the shared queue: read C:\\PROJECTS\\PCQueue\\README.md first, its part for workers on another account above all.

NEVER buy usage credits, turn on extra usage or touch any billing or plan setting on any account (the user, 2 Nov 2026: \"I don't ever want to see a charge pop up on any of my accounts that I didn't personally authorize\"). If this account runs out, stop and wait for its reset.

Nothing may pop up or take focus on the user's screen: no windows, no browsers; headless or hidden only. You never question the user directly: questions go in your hand-back, and the manager asks them.

STATUS FILE (the user, 11 Nov 2026, for the Workers page): keep C:\\PROJECTS\\PoE2 Builder\\scratch\\manager\\workers\\$JOB.status.json up to date: write it at the start of your turn, after each meaningful step (a change made, a run booked or finished, a finding), and at the end of the turn. It is one JSON object of plain-English strings: \"now\" (what you're doing), \"why\", \"done\" (list), \"left\" (list), \"handback\" (your latest hand-back summary, or \"\"), \"report\" (the path of your report file) and \"files\" (list of the paths of files you made or changed, absolute). The exact format is in C:\\PROJECTS\\PCQueue\\README.md's \"worker status file\" section once it's there; follow that if it differs.

MANDATORY (the user, 11 Nov 2026): read CLAUDE.md in your folder and follow its rule on \"only / never / none\" claims. Every claim that something is the only X, that no Y exists, that X never or always happens, that X can't apply to players, or that X is monster-only, deprecated or unused, needs an exhaustive search of the mechanic by meaning (its definition, every stat and every source, not one stat id) plus a second source, written next to it as \"(checked: ...)\", or else \"(UNVERIFIED)\". Never zero, drop or exclude a mechanic on an unverified claim.

The user may write to you directly from their Workers page: such a message starts \"THE USER wrote to you directly\". Answer it in plain English; the rule that you never question the user still holds (questions go in your hand-back)."
	local n=$(( $(count turn_started) + 1 )); echo $n > "$W/$JOB.turn_started"
	run_turn new "$TEXT"
	echo $n > "$W/$JOB.turn_ended"
	deliver
	return 0
fi

# more: the message goes into the inbox first
if [ "$WORKER_FROM_USER" = "1" ]; then
	printf '=== %s  %s (account %s)\n%s\n\n' "$(date '+%Y-%m-%d %H:%M')" "$JOB" "$ACCOUNT" "$TEXT" >> "$W/user_messages.log"
	TEXT="THE USER wrote to you directly, from the Workers page ($(date '+%-d %b, %H:%M')):

\"$TEXT\"

Answer them in plain English in your reply: they read it on the Workers page. Do what they ask within your job and the rules; where their words differ from your brief, theirs win (NEVER any charge on any account, still). Write their words and what you did about them into your report under \"The user's words\". The manager sees this too."
fi
local MSG="$INBOX/$(date +%s%N)_$$.txt"
printf '%s' "$TEXT" > "$MSG.part" && mv "$MSG.part" "$MSG"
if take_lock; then deliver; return 0; fi
echo "$JOB is in the middle of a turn: the message waits in its inbox and goes in as soon as that turn ends."
while [ -e "$MSG" ]; do   # wait for it to go in
	sleep 5
	if ! lock_alive; then rm -rf "$LOCK"; take_lock && { deliver; return 0; }; fi
done
local n; n=$(count turn_started)
while [ "$(count turn_ended)" -lt "$n" ] && lock_alive; do sleep 5; done   # and for the turn that carried it to end
local res="$W/$JOB.turn_$n.json"; [ -s "$res" ] || res="$W/$JOB.last.json"
python -c "import json,sys;d=json.load(open(sys.argv[1],encoding='utf-8'));print(str(d.get('result'))[:1500])" "$res" 2>/dev/null
return 0
}

take_lock() { mkdir "$LOCK" 2>/dev/null || return 1; echo $$ > "$LOCK/pid"; }
pin_self() {   # Windows hands a process's processor set and Below Normal priority on to every program it starts
	local wp; wp=$(cat /proc/$$/winpid 2>/dev/null)
	[ -n "$wp" ] || return 0
	local n; n=$(nproc 2>/dev/null || echo 16)
	[ "$n" -gt 2 ] && [ "$n" -le 62 ] || return 0
	local mask=$(( ((1 << n) - 1) & ~3 ))   # (4 Nov, the user: "just leave me two threads, thats all i need": 0 and 1 are theirs)
	powershell.exe -NoProfile -NonInteractive -Command "\$p = Get-Process -Id $wp; \$p.ProcessorAffinity = [IntPtr]$mask; \$p.PriorityClass = 'BelowNormal'" > /dev/null 2>&1
}
lock_alive() { local p; p=$(cat "$LOCK/pid" 2>/dev/null); [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
waiting() { compgen -G "$INBOX/*.txt" > /dev/null; }
count() { cat "$W/$JOB.$1" 2>/dev/null || echo 0; }

run_turn() {   # $1: new or more, $2: the text
	local args=(-p "$2" --permission-mode auto --output-format json --strict-mcp-config)
	[ -n "$POE2_WORKER_MODEL" ] && args+=(--model "$POE2_WORKER_MODEL")
	if [ "$1" = "more" ]; then
		[ -s "$W/$JOB.id" ] || { echo "no session id for $JOB"; return 4; }
		args+=(--resume "$(cat "$W/$JOB.id")")
	fi
	# The folder the worker runs in (10 Nov: a second worker gets its own git worktree). Set POE2_WORKER_DIR on "new";
	# it's kept in <JOB>.dir so every "more" (the Workers page's too) resumes the session in the same folder.
	if [ "$1" = "new" ]; then
		if [ -n "$POE2_WORKER_DIR" ]; then echo "$POE2_WORKER_DIR" > "$W/$JOB.dir"; else rm -f "$W/$JOB.dir"; fi
	fi
	local dir; dir=$(cat "$W/$JOB.dir" 2>/dev/null); [ -n "$dir" ] || dir="/c/PROJECTS/PoE2 Builder"
	(cd "$dir" && MSYS_NO_PATHCONV=1 env $CLEAR CLAUDE_CONFIG_DIR="C:\\Users\\conve\\.claude-account$ACCOUNT" \
		"$EXE" "${args[@]}" < /dev/null > "$W/$JOB.last.json" 2>&1)   # (no stdin: else it waits 3 s and prints a warning before the JSON)
	local code=$?
	cp -f "$W/$JOB.last.json" "$W/$JOB.turn_$(count turn_started).json" 2>/dev/null
	python - "$W/$JOB.last.json" "$W/$JOB.id" "$JOB" "$ACCOUNT" "$code" <<'PY'
import json, sys
path, idp, job, acct, code = sys.argv[1:6]
text = open(path, encoding="utf-8", errors="replace").read()
try:
	d = json.loads(text[text.find("{"):])   # (a warning line may come before the JSON)
	if d.get("session_id"):
		open(idp, "w").write(d["session_id"])
	print("%s on account %s: exit %s, error %s" % (job, acct, code, d.get("is_error")))
	print(str(d.get("result"))[:1500])
except Exception:
	print("%s on account %s: exit %s, not a result:" % (job, acct, code))
	print(text[:800])
PY
}

deliver() {   # runs the inbox's messages as turns, one after another, until it is empty; then lets the lock go
	while :; do
		while waiting; do
			local n=$(( $(count turn_started) + 1 )) text="" f
			echo $n > "$W/$JOB.turn_started"
			for f in "$INBOX"/*.txt; do
				[ -f "$f" ] || continue
				if [ -z "$text" ]; then text=$(cat "$f"); else text="$text"$'\n\n---\n\n'"$(cat "$f")"; fi
				rm -f "$f"
			done
			[ -n "$text" ] && run_turn more "$text"
			echo $n > "$W/$JOB.turn_ended"
		done
		rm -rf "$LOCK"
		waiting || break   # (a message that came in just as the lock went: take it back, unless someone else has)
		take_lock || break
	done
}

main "$@"; exit $?
