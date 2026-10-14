#!/usr/bin/env python3
"""Replay a multi-session demo of module 10 with real, concurrent psql sessions.

Usage (from the repository root, with the course container running):
    python3 modules/10-transactions-concurrency/examples/sessions.py \
        modules/10-transactions-concurrency/examples/demos/lost_update.txt

Each session letter (A, B, C, ...) is a separate `psql` process inside the
container named by $COURSE_PG_CONTAINER (default: course-pg). Demo format:
    A: BEGIN;             -- send this statement to session A
    B: UPDATE ...;        -- ... and this one to session B
    !sleep 1.5            -- pause
    !wait 3               -- wait 3 s (instead of 0.7 s) after the next step
Lines starting with '--' are comments. After each step the script prints the
output every session produced in the meantime, so a statement that was blocked
shows its result at the step that released it.
"""
import queue
import re
import subprocess
import sys
import threading
import os
import time

CONTAINER = os.environ.get("COURSE_PG_CONTAINER", "course-pg")
DEFAULT_WAIT = float(sys.argv[2]) if len(sys.argv) > 2 else 0.7


class Session:
    def __init__(self, name):
        self.name = name
        self.p = subprocess.Popen(
            ["docker", "exec", "-i", CONTAINER, "sh", "-c",
             "stdbuf -oL -eL psql -U postgres -d bazaar -X -v VERBOSITY=default 2>&1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.q = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.q.put(line.rstrip("\n"))

    def send(self, sql):
        self.p.stdin.write(sql + "\n")
        self.p.stdin.flush()

    def drain(self):
        out = []
        while True:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                return out


def main():
    steps = []
    for raw in open(sys.argv[1]):
        line = raw.rstrip("\n")
        if not line.strip() or line.strip().startswith("--"):
            continue
        if line[0] in " \t" and steps:
            steps[-1][1] += "\n" + line.strip()
            continue
        if line.startswith("!"):
            steps.append(["!", line[1:]])
            continue
        who, sql = line.split(":", 1)
        steps.append([who.strip(), sql.strip()])

    sessions = {}
    n = 0
    wait = DEFAULT_WAIT
    for who, sql in steps:
        if who == "!":
            cmd, arg = sql.split()
            if cmd == "sleep":
                time.sleep(float(arg))
            elif cmd == "wait":
                wait = float(arg)
            continue
        s = sessions.setdefault(who, Session(who))
        if not n:
            time.sleep(0.3)
        n += 1
        s.send(sql)
        time.sleep(wait)
        wait = DEFAULT_WAIT
        print(f"=== step {n} [{who}] {sql}")
        for name in sorted(sessions):
            lines = sessions[name].drain()
            lines = [re.sub(r"^psql:<stdin>:\d+: ", "", l) for l in lines]
            if lines:
                tag = "" if name == who else "  (released)"
                print(f"--- {name}{tag}")
                print("\n".join(lines))
    for s in sessions.values():
        s.p.stdin.close()
    time.sleep(0.5)
    for name, s in sorted(sessions.items()):
        rest = s.drain()
        if rest:
            print(f"--- {name} (at exit)")
            print("\n".join(rest))


if __name__ == "__main__":
    main()
