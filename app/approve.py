#!/usr/bin/env python3
"""Claude Code PreToolUse hook used by Void's "Ask me first" mode.

Claude Code runs this before a command / web request. It asks the Void server, which shows the request under Void
(and asks out loud); you say or click yes/no. Prints the hook decision JSON. If Void isn't running, it stays out of the
way (exit 0, no decision → Claude Code's normal rules apply).
"""
import json
import os
import sys
import urllib.request


def main():
    try:
        d = json.load(sys.stdin)
    except ValueError:
        d = {}
    port, tok = os.environ.get("VOID_PORT"), os.environ.get("VOID_TOKEN")
    if not port or not tok:
        return 0
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/approve",
        # agent_id / agent_type: only there when one of his subagents is asking (the card says which one)
        data=json.dumps({"tool": d.get("tool_name") or "?", "input": d.get("tool_input") or {},
                         "agent_id": d.get("agent_id"), "agent_type": d.get("agent_type")}).encode(),
        headers={"Content-Type": "application/json", "X-Void-Token": tok})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=900))
    except Exception as e:  # noqa: BLE001
        r = {"ok": False, "why": f"couldn't reach Void to ask the user ({e})"}
    ok = bool(r.get("ok"))
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow" if ok else "deny",
        "permissionDecisionReason": r.get("why") or ("the user approved it" if ok else
                                                       "the user said no — ask what they'd like instead"),
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
