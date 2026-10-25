"""Stand-in for `claude -p --output-format stream-json` on the Windows test machine. With --input-format stream-json
(Void's long-lived brain, see app/agents.py) it answers each message on stdin and keeps running, like the real one."""
import json, sys
sys.stdout.reconfigure(encoding="utf-8")
def out(o): print(json.dumps(o, ensure_ascii=False), flush=True)      # raw UTF-8, like the real Claude Code
def turn(msg=None):
    if msg is not None and "--replay-user-messages" in sys.argv:
        out({"type": "user", "isReplay": True, "message": msg})        # (your message, echoed as it's picked up)
    out({"type": "system", "subtype": "init", "session_id": "ci"})
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "dir", "description": "List files"}}]}})
    out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "app  install.ps1"}]}})
    text = "[proud] Hi from Windows! Everything works — even ünïcödé ✓."
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
    out({"type": "result", "subtype": "success", "session_id": "ci", "result": text})
if "--input-format" in sys.argv:
    for line in sys.stdin:                                            # (control requests need no answer here)
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") == "user":
            turn(o.get("message"))
else:
    sys.stdin.read()
    turn()
