"""Void's Claude Code brain: one long-lived `claude -p --input-format stream-json` per chat.

One process per chat (not one per message), so he can answer you right away while his subagents keep working, and a
subagent that finishes later still gets its say: Claude Code runs a turn of its own for it, and Void speaks that as a
short follow-up. Two parts:

  * Stream – turns the raw stream-json events into Void's events (pure: no threads, no processes; see the tests).
    It knows which subagents are running (and how many helpers of their own each one has), and which turn a reply belongs to — yours, or a follow-up — before any of
    it is spoken (a turn's origin is only written on its result, so while a follow-up could be in flight, its text
    is held until then). A message of yours that Claude Code takes into a turn of its own (--replay-user-messages
    echoes it back when it does) is answered by that turn's result, whoever started the turn.
  * Brain – owns the process: starts it (in its own process group), feeds it your messages, restarts it when real
    settings change, picks it up again with --resume after a crash, and stops it and everything it started.

Event shapes come from real captures of Claude Code 2.1.303 (tests/fixtures).
"""
import json
import os
import queue
import signal
import subprocess
import threading
import time

WIN = os.name == "nt"

# how a subagent ended (task_notification's status) → what his little Void does: absorbed / fades / failed
SUB_STATUS = {"completed": "done", "stopped": "stopped", "killed": "stopped"}


def sub_status(s):
    return SUB_STATUS.get(s, "failed")


def result_text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) and b.get("type") == "text" else
                         ("[image]" if isinstance(b, dict) and b.get("type") == "image" else "") for b in c)
    return ""


def tool_output(c):
    """A tool's output for the code panel: long ones keep their head and tail."""
    lines = result_text(c).splitlines()
    if len(lines) > 80:
        lines = lines[:40] + [f"… {len(lines) - 70} more lines …"] + lines[-30:]
    txt = "\n".join(lines)
    return txt if len(txt) <= 6000 else txt[:6000] + "\n…"


def notes_prefix(notes):
    """Memory notes changed since the process started (its system prompt still has the old ones): the new ones go at
    the top of the next message instead of a restart, which would stop his subagents."""
    if not notes:
        return ("(Note from Void, not from the user: the user has cleared your memory notes. Forget the ones you "
                "were given earlier.)\n\n")
    return f"(Note from Void, not from the user: your memory notes changed. These replace the earlier ones.\n{notes})\n\n"


class Stream:
    """stream-json in, Void's events out. feed(obj) → [(dest, event)], dest is:
         "ask"     – for the message you're waiting on (streamed live)
         "end"     – that message's result: it's answered
         "bus"     – for the page at any time (subagents, follow-ups)
         "session" – the chat's session id
    oneshot: the old one-process-per-message way. There Claude Code holds every result back until it exits, so a
    turn's origin comes too late to sort anything by: it all streams to your message, as it always did."""

    def __init__(self, describe=None, oneshot=False):
        self.describe = describe or (lambda name, inp: ("", {}))
        self.oneshot = oneshot
        self.tasks = {}            # running subagents: task_id → {id, type, desc, bg, run, final, waiting}
        self.kids = {}             # their own running subagents (any depth): task_id → (his subagent's task_id, bg)
        self.runs = {}             # run_id → task_id (finished ones too: follow-ups name their run)
        self.launches = {}         # Agent tool_use id → {type, desc}, until it starts (or fails to)
        self.void_stopped = set()  # run ids Void stopped itself: their follow-ups aren't said
        self.awaiting = {}         # run ids whose follow-up turn could still come → results seen since
        self.ask_open = False      # a message of yours is waiting for its result
        self.taken = False         # …and the turn now running took it in (its echo came back)
        self.turn = []             # the current turn's events, held until we know whose turn it is
        self.streamed = False      # this turn's text came as deltas (so the whole message isn't sent again)
        self.inited = False        # the process said hello (system/init) at least once
        self.in_turn = False       # a turn is under way: from its system/init to its result

    # ---- subagents
    def running(self):
        return [{"id": t["id"], "type": t["type"], "desc": t["desc"], "waiting": t["waiting"], "kids": self._kids(t["id"])}
                for t in self.tasks.values()]

    def _kids(self, tid):
        return sum(1 for top, _ in self.kids.values() if top == tid)

    def _kid_count(self, out, top):
        if top in self.tasks:
            out.append(("bus", {"t": "sub_kids", "id": top, "n": self._kids(top)}))

    def _drop_kids(self, top):
        """His subagent ended: its own helpers go with its little Void (whatever they still do, it isn't shown)."""
        self.kids = {k: v for k, v in self.kids.items() if v[0] != top}

    def end_all(self, status):
        """The process is gone: every subagent ends with it."""
        out = [("bus", {"t": "sub_end", "id": tid, "status": status}) for tid in self.tasks]
        self.tasks.clear()
        self.kids.clear()
        self.launches.clear()
        self.awaiting.clear()
        self.turn = []
        self.ask_open = self.taken = False
        self.in_turn = False
        return out

    def stop_all(self):
        """Void stops everything (the Stop button): whatever they report back isn't said."""
        self.void_stopped |= {t["run"] for t in self.tasks.values() if t.get("run")}
        self.void_stopped |= set(self.awaiting)

    # ---- the main agent's events: live for your message, or held until the turn's result says whose it is
    def _main(self, out, ev):
        if self.oneshot or (self.ask_open and not self.awaiting and not self.turn):
            out.append(("ask", ev))
        else:
            self.turn.append(ev)

    def feed(self, o):
        out = []
        ty, st = o.get("type"), o.get("subtype")
        if ty == "system":
            self._system(o, st, out)
        elif o.get("parent_tool_use_id"):
            pass                    # a subagent's own messages: never his words, never his terminal
        elif ty == "stream_event":
            ev = o.get("event") or {}
            d = ev.get("delta") or {}
            if ev.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                self.streamed = True
                self._main(out, {"t": "delta", "text": d.get("text", "")})
            elif ev.get("type") == "message_start":
                self._main(out, {"t": "break"})
        elif ty == "assistant":
            for b in (o.get("message") or {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and not self.streamed and b.get("text"):
                    self._main(out, {"t": "break"})
                    self._main(out, {"t": "delta", "text": b["text"]})
                elif b.get("type") == "tool_use":
                    name, inp = b.get("name", "tool"), b.get("input")
                    if name in ("Agent", "Task") and isinstance(inp, dict):
                        self.launches[b.get("id", "")] = {"type": str(inp.get("subagent_type") or "agent"),
                                                          "desc": str(inp.get("description") or "")}
                    detail, panel = self.describe(name, inp)
                    self._main(out, {"t": "tool", "name": name, "detail": detail, "id": b.get("id", ""), "input": panel})
        elif ty == "user" and o.get("isReplay"):
            self.taken = self.taken or self.ask_open      # your message, echoed as Claude Code picks it up
        elif ty == "user":
            content = (o.get("message") or {}).get("content")
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    tid = b.get("tool_use_id", "")
                    launch = self.launches.pop(tid, None)
                    if launch and b.get("is_error"):     # the subagent never started: a brief failed little Void
                        sid = "launch-" + tid
                        out.append(("bus", {"t": "sub_start", "id": sid, "type": launch["type"], "desc": launch["desc"],
                                            "bg": False}))
                        out.append(("bus", {"t": "sub_end", "id": sid, "status": "failed"}))
                    self._main(out, {"t": "toolres", "id": tid, "text": tool_output(b.get("content")),
                                     "is_error": bool(b.get("is_error"))})
        elif ty == "result":
            self.in_turn = False
            self._result(o, out)
        return out

    def _system(self, o, st, out):
        if st == "init":
            self.inited = True
            self.in_turn = True
            self.streamed = False
            if o.get("session_id"):
                out.append(("session", o["session_id"]))
            self._main(out, {"t": "init", "model": o.get("model", "")})
        elif st == "task_started":
            # only his own subagents get a little Void: not shell commands, not a subagent's own helpers (those are
            # counted on the little Void of the subagent they belong to, however deep, when we can tell which)
            if o.get("task_type") != "local_agent" or not o.get("task_id"):
                return
            par = o.get("parent_task_id")
            if par or o.get("owned_by_subagent") or (o.get("spawn_depth") or 1) > 1:
                top = par if par in self.tasks else (self.kids.get(par) or (None,))[0]
                if top:
                    self.kids[o["task_id"]] = (top, bool(o.get("is_backgrounded")))
                    self._kid_count(out, top)
                return
            tid = o["task_id"]
            launch = self.launches.pop(o.get("tool_use_id", ""), None) or {}
            t = {"id": tid, "type": str(o.get("subagent_type") or launch.get("type") or "agent"),
                 "desc": str(o.get("description") or launch.get("desc") or ""), "bg": bool(o.get("is_backgrounded")),
                 "run": o.get("run_id"), "final": None, "waiting": False}
            self.tasks[tid] = t
            if t["run"]:
                self.runs[t["run"]] = tid
            out.append(("bus", {"t": "sub_start", "id": tid, "type": t["type"], "desc": t["desc"], "bg": t["bg"]}))
        elif st == "task_progress":
            if o.get("task_id") in self.tasks:
                out.append(("bus", {"t": "sub_tick", "id": o["task_id"], "tool": str(o.get("last_tool_name") or "")}))
        elif st == "task_updated":
            t = self.tasks.get(o.get("task_id"))
            s = (o.get("patch") or {}).get("status")
            if t and s and s not in ("running", "pending"):
                t["final"] = s
        elif st == "task_notification":
            kid = self.kids.pop(o.get("task_id"), None)
            if kid:
                self._kid_count(out, kid[0])
            t = self.tasks.pop(o.get("task_id"), None)
            if t:
                self._drop_kids(t["id"])
                out.append(("bus", {"t": "sub_end", "id": t["id"], "status": sub_status(o.get("status"))}))
                if t["bg"] and t["run"]:            # Claude Code will take a turn of its own to tell about it
                    self.awaiting[t["run"]] = 0
                    if not self.oneshot and t["run"] not in self.void_stopped:   # (its words are held till then: the page wakes him now)
                        out.append(("bus", {"t": "followup_soon", "id": t["id"]}))
        elif st == "background_tasks_changed":
            # a backstop: a background subagent that's gone from the list without a notification has ended
            live = {x.get("task_id") for x in o.get("tasks") or [] if isinstance(x, dict)}
            for tid in [k for k, t in self.tasks.items() if t["bg"] and k not in live]:
                t = self.tasks.pop(tid)
                self._drop_kids(tid)
                out.append(("bus", {"t": "sub_end", "id": tid, "status": sub_status(t["final"] or "stopped")}))
            gone = [k for k, (_, bg) in self.kids.items() if bg and k not in live]
            for top in dict.fromkeys(self.kids.pop(k)[0] for k in gone):
                self._kid_count(out, top)

    def _result(self, o, out):
        if o.get("session_id"):
            out.append(("session", o["session_id"]))
        held, self.turn, self.streamed = self.turn, [], False
        taken, self.taken = self.taken, False
        origin = o.get("origin") or {}
        run = origin.get("runId") if origin.get("kind") == "task-notification" else None
        if run is not None:
            self.awaiting.pop(run, None)
        for k in list(self.awaiting):              # a follow-up that never came (two notifications, one turn): let go
            self.awaiting[k] += 1
            if self.awaiting[k] >= 2:
                del self.awaiting[k]
        ok = not o.get("is_error")
        if self.oneshot:                           # (a follow-up's words already went out with the rest)
            if not origin:
                self._answer(o, ok, held, out)
            return
        if origin and not (taken and self.ask_open):   # Claude Code's own turn (a subagent or a peer): a follow-up
            if run in self.void_stopped:
                return
            text = o.get("result") or "".join(e.get("text", "") for e in held if e.get("t") == "delta")
            if text.strip():
                out.append(("bus", {"t": "followup", "text": text, "ok": ok}))
            return
        if not self.ask_open:                      # an answer nobody's waiting for (the message was stopped)
            return
        self.ask_open = False
        self._answer(o, ok, held, out)

    @staticmethod
    def _answer(o, ok, held, out):
        out.extend(("ask", e) for e in held)
        denied = o.get("permission_denials") or []
        if denied:
            out.append(("ask", {"t": "denied", "tools": sorted({d.get("tool_name", "?") for d in denied})}))
        out.append(("end", {"t": "result", "text": o.get("result") or "", "is_error": not ok,
                            "cost": o.get("total_cost_usd"), "turns": o.get("num_turns")}))


def kill_tree(p, grace=1.5):
    """Stop a process and everything it started. On Linux/macOS it was started in its own process group, and the whole
    group goes (even when the leader is already gone: its children may not be)."""
    if p is None:
        return
    if WIN:
        if p.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, creationflags=0x08000000)
        return
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        p.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    try:                                           # anything still in the group (the leader, or a child that hung on)
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        p.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


def own_group_kw():
    """Popen arguments that put the child in its own process group (Windows: its own console group, no window)."""
    return {"creationflags": 0x00000200 | 0x08000000} if WIN else {"start_new_session": True}


class _Ask:
    def __init__(self):
        self.q = queue.Queue()


class Brain:
    """The long-lived process. One message at a time (the server's lock), any number of subagents.

    A launch (built by the server for every message) is a dict:
      cmd      the command line (no message in it; --resume when the chat has a session)
      perm     what he's allowed to do (+ the folder): a change restarts him even if it stops his subagents
      sig      everything else baked in at start (system prompt, extra flags): a change restarts him once he's idle
      session  the chat it's meant to be (a different one restarts him)
      model    for this message ("" = default): switched with a control request, no restart
      notes    the memory notes now (they go in front of the message when they've changed)
      cwd, env"""
    IDLE_S = 600                                   # nothing to do for this long → let the process go

    def __init__(self, publish, on_session=None, describe=None, popen=subprocess.Popen, killer=kill_tree,
                 clock=time.monotonic, watchdog=True):
        self.publish, self.on_session = publish, on_session or (lambda sid: None)
        self.describe, self.popen, self.killer, self.clock = describe, popen, killer, clock
        self.lock = threading.RLock()
        self.proc = None
        self.gen = 0                               # which process the reader thread belongs to
        self.stream = Stream(describe)
        self.cur = None                            # the message being answered
        self.perm = self.sig = self.session = None
        self.model = None
        self.notes_known = ""
        self.stopping = False
        self.err = []
        self.last_used = clock()
        if watchdog:
            threading.Thread(target=self._watch, daemon=True).start()

    # ---- what the server asks
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def running(self):
        with self.lock:
            return self.stream.running()

    def agent_info(self, agent_id, agent_type=None):
        """Who's asking (an approval card from a subagent): its type and task. None for him."""
        if not agent_id:
            return None
        with self.lock:
            t = self.stream.tasks.get(agent_id)
        if t:
            return {"id": t["id"], "type": t["type"], "desc": t["desc"]}
        return {"id": str(agent_id), "type": str(agent_type or "agent"), "desc": ""}

    def set_waiting(self, agent_id, on):
        with self.lock:
            t = self.stream.tasks.get(agent_id)
            if not t:
                return
            t["waiting"] = bool(on)
        self.publish(t="sub_wait", id=agent_id, on=bool(on))

    def ask(self, launch, text, emit):
        """Send one message and pass its events to emit() until it's answered. Returns "done", or "retry_nopartial" /
        "retry_fresh" / "unsupported" when the process couldn't start that way (nothing was said yet)."""
        note = None
        with self.lock:
            self.last_used = self.clock()
            self.stopping = False
            if self.alive():
                why = self._restart_reason(launch)
                if why:
                    n = len(self.stream.tasks)
                    self._shutdown_locked("stopped")
                    if n and why == "perm":
                        note = f"restarted Claude for the new settings — stopped {n} background helper{'s' if n > 1 else ''}"
            a = self.cur = _Ask()
            for attempt in range(2):               # (the process died while idle: start a new one, once)
                if not self.alive():
                    self._spawn(launch)
                if launch.get("model", "") != self.model:
                    self.model = launch.get("model", "")
                    self._write({"type": "control_request", "request_id": f"m{time.time_ns()}",
                                 "request": {"subtype": "set_model", "model": self.model or "default"}})
                msg = text
                if launch.get("notes", "") != self.notes_known:
                    msg = notes_prefix(launch.get("notes", "")) + text
                self.stream.ask_open = True
                if self._write({"type": "user", "message": {"role": "user", "content": msg}}):
                    self.notes_known = launch.get("notes", "")
                    break
                self.stream.ask_open = False
                if attempt == 0 and self.stream.inited:
                    self._drop_dead_locked()
                    continue
                break                              # (a brand-new process that won't take input: its exit says why)
        if note:
            emit(t="note", text=note)
        while True:
            item = a.q.get()
            if isinstance(item, tuple):
                return item[1]
            emit(**item)

    def stop(self, wait=3.0):
        """The Stop button: stop his answer and every subagent. Interrupt first (the chat stays warm); if he doesn't
        stop within `wait` seconds, the whole process group goes. No turn under way: your message just ends, he stays."""
        with self.lock:
            if not self.alive():
                return False
            self.stopping = True
            self.stream.stop_all()
            a = self.cur
            self._write({"type": "control_request", "request_id": f"i{time.time_ns()}", "request": {"subtype": "interrupt"}})
            gen = self.gen
            if not self.stream.in_turn:            # (an interrupt then brings no result: nothing to wait for)
                if a is not None:
                    self.cur, self.stream.ask_open = None, False
                    a.q.put({"t": "stopped"})
                    a.q.put(("end", "done"))
                return True

        def fallback():
            time.sleep(wait)
            with self.lock:
                if self.gen == gen and a is not None and self.cur is a:
                    self._shutdown_locked("stopped")
        threading.Thread(target=fallback, daemon=True).start()
        return True

    def shutdown(self, wait=True):
        """New chat, another folder, quitting: stop the process and everything it started (wait: until it's gone)."""
        with self.lock:
            p = self._shutdown_locked("stopped", kill=False)
        if p and wait:
            self.killer(p)
        elif p:
            threading.Thread(target=self.killer, args=(p,), daemon=True).start()
        return bool(p)

    def idle_check(self, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            # (a follow-up still awaited doesn't count: after this long without a word it isn't coming)
            idle = self.alive() and self.cur is None and not self.stream.tasks and now - self.last_used > self.IDLE_S
            p = self._shutdown_locked("stopped", kill=False) if idle else None
        if p:
            self.killer(p)
        return bool(p)

    # ---- inside
    def _restart_reason(self, L):
        if L.get("session") != self.session:
            return "chat"
        if L.get("perm") != self.perm:
            return "perm"
        if L.get("sig") != self.sig and not self.stream.tasks and not self.stream.awaiting:
            return "sig"                           # (with subagents running it waits: the old settings until they're done)
        return None

    def _spawn(self, L):
        if self.proc is not None:                  # (gone, but its reader hasn't noticed yet: end its subagents here)
            self._drop_dead_locked()
        self.gen += 1
        self.stream = Stream(self.describe)
        self.err = []
        self.perm, self.sig, self.session = L.get("perm"), L.get("sig"), L.get("session")
        self.model = L.get("model", "")
        self.notes_known = L.get("notes", "")
        try:
            p = self.popen(L["cmd"], cwd=L.get("cwd"), env=L.get("env"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, bufsize=1, encoding="utf-8", errors="replace",
                           **own_group_kw())
        except OSError as e:
            self.proc = None
            self.err = [str(e)]
            a, self.cur = self.cur, None
            if a:
                a.q.put({"t": "error", "text": f"couldn't start Claude Code: {e}"})
                a.q.put(("end", "done"))
            return
        self.proc = p
        gen = self.gen
        err = self.err
        t_err = threading.Thread(target=lambda: err.extend(p.stderr), daemon=True)
        t_err.start()
        threading.Thread(target=self._read, args=(p, gen, t_err), daemon=True).start()

    def _write(self, obj):
        p = self.proc
        if p is None:
            return False
        try:
            p.stdin.write(json.dumps(obj) + "\n")
            p.stdin.flush()
            return True
        except (OSError, ValueError):              # (a closed pipe: the process is gone)
            return False

    def _route(self, outs):
        for dest, ev in outs:
            if dest == "session":
                self.session = ev
                self.on_session(ev)
            elif dest == "bus":
                self.publish(**ev)
            elif self.cur is not None:
                if dest == "end":
                    a, self.cur = self.cur, None
                    a.q.put({"t": "stopped"} if self.stopping else ev)
                    a.q.put(("end", "done"))
                else:
                    self.cur.q.put(ev)

    def _read(self, p, gen, t_err):
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            with self.lock:
                if gen != self.gen:
                    return
                self.last_used = self.clock()
                self._route(self.stream.feed(o))
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        t_err.join(timeout=2)                      # (so a crash's message is all there)
        with self.lock:
            if gen != self.gen:                    # (stopped on purpose: shutdown() already tidied up)
                return
            self._died_locked(p)
        self.killer(p)                             # its children may still be running

    def _died_locked(self, p):
        """The process ended on its own (a crash, or it refused to start)."""
        self.gen += 1
        self.proc = None
        err = "".join(self.err).strip()
        low = err.lower()
        a, self.cur = self.cur, None
        inited = self.stream.inited
        self._route(self.stream.end_all("stopped" if self.stopping else "failed"))
        if a is None:
            return
        if self.stopping:
            a.q.put({"t": "stopped"})
            status = "done"
        elif not inited and "input-format" in low:
            status = "unsupported"
        elif not inited and ("include-partial-messages" in low or "unknown option" in low):
            status = "retry_nopartial"
        elif not inited and self.session and ("session" in low or "conversation" in low):
            status = "retry_fresh"
        else:
            last = err.splitlines()[-1].strip()[:240] if err else ""
            code = p.poll()
            a.q.put({"t": "error", "text": "Claude Code stopped unexpectedly" + (f" (exit code {code})" if code else "")
                                           + (f": {last}" if last else "") + " — your next message starts it again."})
            status = "done"
        a.q.put(("end", status))

    def _drop_dead_locked(self):
        p = self.proc
        if p is not None:
            self.gen += 1
            self.proc = None
            self._route(self.stream.end_all("failed"))
            threading.Thread(target=self.killer, args=(p,), daemon=True).start()

    def _shutdown_locked(self, status, kill=True):
        p, self.proc = self.proc, None
        self.gen += 1
        a, self.cur = self.cur, None
        if a:
            a.q.put({"t": "stopped"})
            a.q.put(("end", "done"))
        self._route(self.stream.end_all(status))
        if p is None:
            return None
        try:
            p.stdin.close()                        # (the last resort if the kill misses: no input → it ends itself)
        except (OSError, ValueError):
            pass
        if kill:
            threading.Thread(target=self.killer, args=(p,), daemon=True).start()
        return p

    def _watch(self):
        while True:
            time.sleep(20)
            try:
                self.idle_check()
            except Exception:  # noqa: BLE001
                pass
