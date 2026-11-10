"""Laptop Closed: close the lid -> your phone buzzes (push) and/or rings (Vapi call).
Voice tools: research (Exa), delegate (Manus), status, get_context. Jev routes and decides interrupts."""
import json, os, subprocess, threading, time, pathlib
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

ENV = pathlib.Path(__file__).parent / ".env"

def load_env():
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

load_env()
E = os.environ.get
app = FastAPI()
LOG, TASKS = [], {}
CTX = {"snapshot": "No snapshot yet.", "pending": "Hey, it's your laptop. What should I work on?"}
STATE = {"lid": "open", "calls": 0, "pushes": 0}

def log(kind, msg):
    LOG.append({"t": time.strftime("%H:%M:%S"), "kind": kind, "msg": str(msg)[:400]})
    del LOG[:-200]
    print(f"[{kind}] {msg}", flush=True)

# ---------- Jev (TypeSafe System One) with keyword fallback ----------
def jev(state, qname, instructions, criteria):
    r = httpx.post(f"{E('TYPESAFE_API_BASE', 'https://api.typesafe.ai')}/v1/systemone",
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
            {"call": "The task succeeded and produced something the user can look at or use now, like a built or deployed page, app, or report",
             "text": "Only a minor progress update or small note with nothing new to look at",
             "silent": "Failed, empty or irrelevant result"})
        log("jev", f"notify={c} ({p:.2f})")
        return c
    except Exception as e:
        log("jev-fallback", f"notify=call ({e})")
        return "call"

# ---------- Alerts: push (ntfy) and/or phone call (Vapi) ----------
def push(title, body, click=None, priority="urgent"):
    topic = E("NTFY_TOPIC")
    if not topic:
        log("push", "NTFY_TOPIC not set"); return False
    try:
        r = httpx.post(f"https://ntfy.sh/{topic}", data=body.encode(), timeout=10, headers={
            "Title": title.encode("ascii", "ignore").decode(), "Priority": priority, "Tags": "computer",
            "Click": click or f"{E('PUBLIC_URL', '').rstrip('/')}/talk"})
        STATE["pushes"] += 1
        log("push", f"{r.status_code} {title}")
        return r.status_code < 300
    except Exception as e:
        log("push-error", e); return False

def call_me(first_message):
    try:
        r = httpx.post("https://api.vapi.ai/call", timeout=20,
            headers={"Authorization": f"Bearer {E('VAPI_API_KEY')}"},
            json={"assistantId": E("VAPI_ASSISTANT_ID"), "phoneNumberId": E("VAPI_PHONE_NUMBER_ID"),
                  "customer": {"number": E("MY_PHONE_NUMBER")},
                  "assistantOverrides": {"firstMessage": first_message}})
        STATE["calls"] += 1
        log("call", f"{r.status_code} {r.text[:200]}")
        return r.status_code < 300
    except Exception as e:
        log("call-error", e); return False

def alert(title, message, click=None, force=None):
    """force: None = follow NOTIFY_MODE, 'push' = push only."""
    CTX["pending"] = message
    load_env()  # pick up NOTIFY_MODE changes without a restart
    mode = force or E("NOTIFY_MODE", "push")
    ok = False
    if mode in ("push", "both"): ok = push(title, message, click) or ok
    if mode in ("call", "both"): ok = call_me(message) or ok
    return ok

# ---------- Lid watcher ----------
def snapshot():
    repo = E("REPO_PATH", ".")
    def g(*a):
        try: return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception: return ""
    branch, commits = g("rev-parse", "--abbrev-ref", "HEAD"), g("log", "-3", "--pretty=%s")
    if not branch:
        return "I couldn't find a git repo, but I'm here and ready to keep going."
    s = f"You were on branch {branch}. Your last commits were: {commits.replace(chr(10), '; ')}."
    p = pathlib.Path(repo) / "TODO.md"
    if p.exists():
        first = next((l.strip("-* ").strip() for l in p.read_text().splitlines() if l.strip()), "")
        if first: s += f" Next on your list: {first}."
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
                log("lid", "closed -> alerting")
                alert("Laptop closed - keep going?",
                      f"Laptop closed. {CTX['snapshot']} Want me to keep going? I can research, or hand a build to Manus.")
            was = now
        except Exception as e:
            log("lid-error", e)
        time.sleep(1)

# ---------- Manus ----------
MANUS = "https://api.manus.ai/v2"
MH = lambda: {"x-manus-api-key": E("MANUS_API_KEY"), "Content-Type": "application/json"}

def manus_watch(tid):
    nudged = False
    while True:
        time.sleep(10)
        try:
            d = httpx.get(f"{MANUS}/task.detail", params={"task_id": tid}, headers=MH(), timeout=20).json()["task"]
            TASKS[tid]["status"] = d["status"]
            if d["status"] == "waiting" and not nudged:
                r = httpx.post(f"{MANUS}/task.sendMessage", headers=MH(), timeout=20,
                    json={"task_id": tid, "message": {"content": "Continue and use your best judgment."}})
                nudged = True
                log("manus", f"{tid} waiting -> nudged ({r.status_code} {r.text[:120]})")
            if d["status"] in ("stopped", "error") and not d.get("has_running_background_jobs"):
                m = httpx.get(f"{MANUS}/task.listMessages", headers=MH(), timeout=20,
                              params={"task_id": tid, "order": "desc", "limit": 20}).json().get("messages", [])
                txt = next((x["assistant_message"]["content"] for x in m if x["type"] == "assistant_message"), "")
                TASKS[tid]["result"] = txt
                log("manus", f"{tid} {d['status']}: {txt[:120]}")
                title = TASKS[tid]["title"]
                how = notify_decision(f"Task '{title}' {d['status']}: {txt[:500]}")
                msg = f"Your task {title} is {'done' if d['status'] == 'stopped' else 'failed'}. {txt[:300]}"
                if how == "call":
                    alert(f"Manus finished: {title}", msg, click=TASKS[tid]["url"])
                elif how == "text":
                    alert(f"Manus finished: {title}", msg, click=TASKS[tid]["url"], force="push")
                return
        except Exception as e:
            log("manus-error", e)

def delegate(task):
    r = httpx.post(f"{MANUS}/task.create", headers=MH(), timeout=30, json={"message": {"content": task}}).json()
    if not r.get("ok"):
        return f"Manus error: {r.get('error', {}).get('message')}"
    tid = r["task_id"]
    TASKS[tid] = {"title": task[:60], "status": "running", "url": r.get("task_url"), "result": ""}
    threading.Thread(target=manus_watch, args=(tid,), daemon=True).start()
    log("manus", f"created {tid} {r.get('task_url')}")
    return "Started in the cloud. I'll ping you when it's done."

# ---------- Exa ----------
def short(text, n=2):
    import re
    text = re.sub(r"\s*\[\d+\]", "", text)
    parts = [p.strip() for p in text.replace("\n", " ").split(". ") if p.strip()]
    return ". ".join(parts[:n]).rstrip(".") + "."

def research(query):
    H = {"x-api-key": E("EXA_API_KEY")}
    try:
        r = httpx.post("https://api.exa.ai/answer", timeout=25, headers=H, json={"query": query, "text": False})
        r.raise_for_status()
        d = r.json()
        ans = d.get("answer") or ""
        if isinstance(ans, dict): ans = json.dumps(ans)
        if ans:
            src = (d.get("citations") or [{}])[0]
            site = (src.get("url") or "").split("/")[2] if src.get("url") else ""
            return short(ans) + (f" Source: {site}." if site else "")
    except Exception as e:
        log("exa-answer-fallback", e)
    d = httpx.post("https://api.exa.ai/search", timeout=25, headers=H,
        json={"query": query, "numResults": 3, "contents": {"highlights": {"numSentences": 2}}}).json()
    res = d.get("results", [])
    if not res: return "I found nothing useful."
    out = " ".join(((x.get("highlights") or [x.get("title", "")])[0])[:250] for x in res[:2])
    return " ".join(out.replace("#", " ").split())[:400] + f" Source: {res[0].get('url', '').split('/')[2] if res[0].get('url') else ''}."

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
    for tc in msg.get("toolCallList") or msg.get("toolWithToolCallList") or []:
        tc = tc.get("toolCall", tc)
        fn = tc.get("function") or {}
        name = tc.get("name") or fn.get("name")
        args = tc.get("arguments") or fn.get("arguments") or {}
        if isinstance(args, str): args = json.loads(args or "{}")
        log("tool", f"{name} {args}")
        text = args.get("query") or args.get("task")
        if text:  # Jev decision on the dashboard
            threading.Thread(target=route, args=(text,), daemon=True).start()
        try:
            if name == "research": res = research(args["query"])
            elif name == "delegate": res = delegate(args["task"])
            elif name == "status": res = status()
            elif name == "get_context": res = CTX["snapshot"]
            elif name == "route": res = route(args["request"])
            else: res = "Unknown tool"
        except Exception as e:
            log("tool-error", e); res = f"That failed: {e}"
        log("tool-result", f"{name}: {res}")
        results.append({"toolCallId": tc["id"], "result": res})
    if not results:
        log("tool-raw", json.dumps(body)[:400])
    return {"results": results}

@app.post("/manus-webhook")
async def manus_webhook(req: Request):
    log("webhook", (await req.body())[:200]); return {"ok": True}

@app.post("/trigger")
@app.post("/trigger-call")
def trigger(mode: str = None):
    load_env()
    CTX["snapshot"] = snapshot()
    ok = alert("Laptop Closed test", f"Hey, it's your laptop. {CTX['snapshot']} What should I work on?",
               force=mode if mode in ("push", "call", "both") else None)
    return {"ok": ok, "mode": mode or E("NOTIFY_MODE", "push")}

@app.get("/state")
def state(): return {"state": STATE, "tasks": TASKS, "log": LOG[::-1]}

@app.get("/talk", response_class=HTMLResponse)
def talk():
    load_env()
    cfg = json.dumps({"key": E("VAPI_PUBLIC_KEY", ""), "assistant": E("VAPI_ASSISTANT_ID", ""), "first": CTX["pending"]})
    return TALK_HTML.replace("__CFG__", cfg)

TALK_HTML = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Laptop Closed</title><style>
body{margin:0;font:16px -apple-system,system-ui,sans-serif;background:#0b0b10;color:#e8e8f0;display:flex;flex-direction:column;align-items:center;padding:24px 16px;min-height:100vh;box-sizing:border-box}
h1{font-size:20px;margin:8px 0 4px}p.sub{color:#9a9aae;margin:0 0 28px;text-align:center}
button{width:180px;height:180px;border-radius:50%;border:0;font-size:20px;font-weight:600;color:#fff;background:#2563eb;cursor:pointer}
button.live{background:#dc2626}#st{margin:16px 0;color:#9a9aae}
#tx{width:100%;max-width:520px;margin-top:8px}.l{padding:8px 12px;border-radius:10px;margin:6px 0;background:#1a1a24}.l.u{background:#1e3a8a;margin-left:15%}
</style></head><body><h1>Laptop Closed</h1><p class=sub id=first></p>
<button id=b>Talk</button><div id=st>Tap to start</div><div id=tx></div>
<script type=module>
import Vapi from "https://esm.sh/@vapi-ai/web@2.7.1";
const C=__CFG__; first.textContent=C.first;
if(!C.key){st.textContent="Add VAPI_PUBLIC_KEY to .env, then reload";b.disabled=true}
const vapi=new Vapi(C.key); let live=false;
b.onclick=async()=>{ if(live){vapi.stop();return}
  st.textContent="Connecting..."; try{await vapi.start(C.assistant,{firstMessage:C.first})}catch(e){st.textContent="Error: "+(e.message||e)} };
vapi.on("call-start",()=>{live=true;b.textContent="End";b.classList.add("live");st.textContent="Live - talk now"});
vapi.on("call-end",()=>{live=false;b.textContent="Talk";b.classList.remove("live");st.textContent="Call ended"});
vapi.on("error",e=>{st.textContent="Error: "+(e?.error?.message||e?.message||JSON.stringify(e)).slice(0,160)});
vapi.on("message",m=>{ if(m.type==="transcript"&&m.transcriptType==="final"){
  const d=document.createElement("div");d.className="l"+(m.role==="user"?" u":"");d.textContent=m.transcript;tx.prepend(d)} });
</script></body></html>"""

@app.get("/dashboard", response_class=HTMLResponse)
def dash():
    return """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width"><title>Laptop Closed</title>
<body style="font:14px ui-monospace,monospace;background:#0b0b10;color:#d8d8e0;padding:16px">
<h2>Laptop Closed <span id=lid></span></h2><p><a style=color:#8cf href=/talk>Open voice call</a></p>
<h3>Tasks</h3><div id=tasks></div><h3>Log</h3><div id=log></div>
<script>async function r(){const s=await (await fetch('/state')).json();
lid.textContent='lid: '+s.state.lid+' · pushes: '+s.state.pushes+' · calls: '+s.state.calls;
tasks.innerHTML=Object.values(s.tasks).map(t=>`<div>${t.status} — <a style=color:#8cf href="${t.url}">${t.title}</a></div>`).join('')||'none';
log.innerHTML=s.log.map(l=>`<div><b>${l.t}</b> [${l.kind}] ${l.msg.replace(/</g,'&lt;')}</div>`).join('')}
r();setInterval(r,2000)</script>"""

@app.on_event("startup")
def boot():
    threading.Thread(target=lid_loop, daemon=True).start()
    log("boot", "server up")
