"""One-time: create the Vapi assistant, attach it to your number, write VAPI_ASSISTANT_ID to .env."""
import os, pathlib, re, httpx
env = pathlib.Path(".env"); txt = env.read_text()
cfg = dict(l.split("=", 1) for l in txt.splitlines() if "=" in l and not l.startswith("#"))
H = {"Authorization": f"Bearer {cfg['VAPI_API_KEY']}"}
url = cfg["PUBLIC_URL"].rstrip("/") + "/vapi/tools"
def tool(name, desc, props, req):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req}},
            "server": {"url": url}, "messages": [{"type": "request-start", "content": "One sec."}] if name != "status" else []}
s = {"type": "string"}
body = {
  "name": "Laptop Closed",
  "firstMessage": "Hey, it's your laptop. What should I work on?",
  "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "en"},
  "voice": {"provider": "deepgram", "model": "aura-2", "voiceId": "thalia"},
  "model": {"provider": "openai", "model": "gpt-4o",
    "messages": [{"role": "system", "content":
      "You are the user's coding agent on a phone call; their laptop lid is closed. Keep every reply to one or two short spoken sentences. "
      "Use research for facts, delegate for anything that takes a long time (building pages, reports), status for progress, get_context to recall what they were working on. "
      "After delegating, say you'll call back when done. Never read URLs aloud."}],
    "tools": [
      tool("research", "Search the web and return a short answer with a source.", {"query": s}, ["query"]),
      tool("delegate", "Hand a long-running task to Manus in the cloud.", {"task": s}, ["task"]),
      tool("status", "Report progress of running tasks.", {}, []),
      tool("get_context", "What the user was working on when the lid closed.", {}, [])]},
}
r = httpx.post("https://api.vapi.ai/assistant", headers=H, json=body, timeout=30)
print(r.status_code, r.text[:300]); r.raise_for_status()
aid = r.json()["id"]
txt = re.sub(r"^VAPI_ASSISTANT_ID=.*$", f"VAPI_ASSISTANT_ID={aid}", txt, flags=re.M)
env.write_text(txt)
p = httpx.patch(f"https://api.vapi.ai/phone-number/{cfg['VAPI_PHONE_NUMBER_ID']}", headers=H, json={"assistantId": aid}, timeout=30)
print("attach number:", p.status_code, p.text[:200])
