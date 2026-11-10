"""Laptop Closed: lid closes -> Vapi calls you. Voice tools: research (Exa), delegate (Manus), status."""
import json, os, subprocess, threading, time, pathlib
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

ENV = pathlib.Path(__file__).parent / ".env"
for line in ENV.read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
E = os.environ.get
app = FastAPI()
LOG, TASKS, CTX = [], {}, {"snapshot": "No snapshot yet."}
STATE = {"lid": "open", "calls": 0}

def log(kind, msg):
    LOG.append({"t": time.strftime("%H:%M:%S"), "kind": kind, "msg": str(msg)[:400]})
    del LOG[:-200]
    print(f"[{kind}] {msg}", flush=True)

# ---------- Jev (TypeSafe System One) with keyword fallback ----------
def jev(state, qname, instructions, criteria):
    r = httpx.post(f"{E('TYPESAFE_API_BASE','https://api.typesafe.ai')}/v1/systemone",
        headers={"Authorization": f"Bearer {E('TYPESAFE_API_KEY')}"}, timeout=15,
        json={"model": "jev-latest", "state": state,
              "questions": {qname: {"type": "choice", "instructions": instructions, "criteria": criteria}}})
    r.raise_for_status()
    a = r.json()["answers"][qname]
    return a["choice"], a["confidence"]

def route(request):
    try:
        c, p = jev(request, "route", "What does the user want done?",
            {"research": "Look something up or answer a factual question",
             "delegate": "Build, write, or produce something that takes a long time",
             "status": "Ask about progress of running tasks"})
        log("jev", f"route={c} ({p:.2f}) for: {request}")
        return c
    except Exception as e:
        t = request.lower()
        c = "status" if "status" in t else "delegate" if any(w in t for w in ("build", "make", "create", "write")) else "research"
        log("jev-fallback", f"route={c} ({e})")
        return c

def notify_decision(result):
    try:
        c, p = jev(result, "notify", "How should the user be told that this background task finished?",
            {"call": "Important or actionable result worth a phone call",
             "text": "Minor result, a message is enough",
             "silent": "Failed, empty or irrelevant result"})
        log("jev", f"notify={c} ({p:.2f})")
        return c
    except Exception as e:
        log("jev-fallback", f"notify=call ({e})")
        return "call"

# ---------- Vapi outbound call ----------
def call_me(first_message):
    r = httpx.post("https://api.vapi.ai/call", timeout=20,
        headers={"Authorization": f"Bearer {E('VAPI_API_KEY')}"},
        json={"assistantId": E("VAPI_ASSISTANT_ID"), "phoneNumberId": E("VAPI_PHONE_NUMBER_ID"),
              "customer": {"number": E("MY_PHONE_NUMBER")},
              "assistantOverrides": {"firstMessage": first_message}})
    STATE["calls"] += 1
    log("call", f"{r.status_code} {r.text[:200]}")
    return r.status_code < 300

# ---------- Lid watcher ----------
def snapshot():
    repo = E("REPO_PATH", ".")
    def g(*a):
        try: return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception: return ""
    branch, commits = g("rev-parse", "--abbrev-ref", "HEAD"), g("log", "-3", "--pretty=%s")
    todo = ""
    p = pathlib.Path(repo) / "TODO.md"
    if p.exists(): todo = p.read_text()[:300]
    if not branch:
        return "I couldn't find a git repo, but I'm here and ready to keep going."
    s = f"You were on branch {branch}. Recent commits: {commits.replace(chr(10), '; ')}."
    if todo: s += f" Your to-do list says: {todo}"
    return s

def lid_closed():
    out = subprocess.run("ioreg -r -k AppleClamshellState | grep AppleClamshellState", shell=True, capture_output=True, text=True).stdout
    return "Yes" in out

def lid_loop():
    was = False
    while True:
        try:
            now = lid_closed()
            STATE["lid"] = "closed" if now else "open"
            if now and not was:
                CTX["snapshot"] = snapshot()
                log("lid", "closed -> calling")
                call_me(f"Laptop closed. {CTX['snapshot']} Want me to keep going? I can research, or hand a build to Manus.")
            was = now
        except Exception as e:
            log("lid-error", e)
        time.sleep(1)

# ---------- Manus ----------
MH = lambda: {"x-manus-api-key": E("MANUS_API_KEY"), "Content-Type": "application/json"}

def manus_watch(tid):
    seen_final = False
    while not seen_final:
        time.sleep(10)
        try:
            d = httpx.get("https://api.manus.ai/v2/task.detail", params={"task_id": tid}, headers=MH(), timeout=20).json()["task"]
            TASKS[tid]["status"] = d["status"]
            if d["status"] in ("stopped", "error") and not d.get("has_running_background_jobs"):
                m = httpx.get("https://api.manus.ai/v2/task.listMessages", headers=MH(), timeout=20,
                              params={"task_id": tid, "order": "desc", "limit": 20}).json().get("messages", [])
                txt = next((x["assistant_message"]["content"] for x in m if x["type"] == "assistant_message"), "")
                TASKS[tid]["result"] = txt
                seen_final = True
                log("manus", f"{tid} {d['status']}: {txt[:120]}")
                how = notify_decision(f"Task '{TASKS[tid]['title']}' {d['status']}: {txt[:500]}")
                if how == "call":
                    call_me(f"Your task {TASKS[tid]['title']} is {d['status']}. {txt[:300]} The link is in your dashboard.")
        except Exception as e:
            log("manus-error", e)

def delegate(task):
    r = httpx.post("https://api.manus.ai/v2/task.create", headers=MH(), timeout=30, json={"message": {"content": task}}).json()
    if not r.get("ok"): return f"Manus error: {r.get('error',{}).get('message')}"
    tid = r["task_id"]
    TASKS[tid] = {"title": task[:60], "status": "running", "url": r.get("task_url"), "result": ""}
    threading.Thread(target=manus_watch, args=(tid,), daemon=True).start()
    log("manus", f"created {tid}")
    return "Started. I'll call you back when it's done."

# ---------- Exa ----------
def research(query):
    r = httpx.post("https://api.exa.ai/search", timeout=25, headers={"x-api-key": E("EXA_API_KEY")},
        json={"query": query, "numResults": 3, "contents": {"highlights": {"numSentences": 2}}}).json()
    res = r.get("results", [])
    if not res: return "I found nothing useful."
    out = " ".join(((x.get("highlights") or [x.get("title", "")])[0])[:250] for x in res[:2])
    out = " ".join(out.replace("#", " ").split())[:450]
    return f"{out} Source: {res[0].get('url')}"

def status():
    if not TASKS: return "No tasks running."
    return "; ".join(f"{t['title']}: {t['status']}" for t in TASKS.values())

# ---------- HTTP ----------
@app.post("/vapi/tools")
async def vapi_tools(req: Request):
    body = await req.json()
    msg = body.get("message", {})
    if msg.get("type") != "tool-calls":
        return {}
    results = []
    for tc in msg.get("toolCallList") or msg.get("toolWithToolCallList", []):
        tc = tc.get("toolCall", tc)
        name = tc.get("name") or tc.get("function", {}).get("name")
        args = tc.get("arguments") or tc.get("function", {}).get("arguments") or {}
        if isinstance(args, str): args = json.loads(args or "{}")
        log("tool", f"{name} {args}")
        try:
            if name == "research": res = research(args["query"])
            elif name == "delegate": res = delegate(args["task"])
            elif name == "status": res = status()
            elif name == "get_context": res = CTX["snapshot"]
            elif name == "route":  # optional helper
                res = route(args["request"])
            else: res = "Unknown tool"
        except Exception as e:
            log("tool-error", e); res = f"That failed: {e}"
        results.append({"toolCallId": tc["id"], "result": res})
    return {"results": results}

@app.post("/manus-webhook")
async def manus_webhook(req: Request):
    log("webhook", (await req.body())[:200]); return {"ok": True}

@app.post("/heartbeat")
async def heartbeat(): return {"ok": True}

@app.post("/trigger-call")
def trigger():
    CTX["snapshot"] = snapshot()
    return {"ok": call_me(f"Manual test. {CTX['snapshot']} What should I work on?")}

@app.get("/state")
def state(): return {"state": STATE, "tasks": TASKS, "log": LOG[::-1]}

@app.get("/dashboard", response_class=HTMLResponse)
def dash():
    return """<!doctype html><meta name=viewport content="width=device-width"><title>Laptop Closed</title>
<body style="font:14px ui-monospace,monospace;background:#0b0b10;color:#d8d8e0;padding:16px">
<h2>Laptop Closed <span id=lid></span></h2><h3>Tasks</h3><div id=tasks></div><h3>Log</h3><div id=log></div>
<script>async function r(){const s=await (await fetch('/state')).json();
lid.textContent='lid: '+s.state.lid+' · calls: '+s.state.calls;
tasks.innerHTML=Object.values(s.tasks).map(t=>`<div>${t.status} — <a style=color:#8cf href="${t.url}">${t.title}</a></div>`).join('')||'none';
log.innerHTML=s.log.map(l=>`<div><b>${l.t}</b> [${l.kind}] ${l.msg.replace(/</g,'&lt;')}</div>`).join('')}
r();setInterval(r,2000)</script>"""

@app.on_event("startup")
def boot():
    threading.Thread(target=lid_loop, daemon=True).start()
    log("boot", "server up")
