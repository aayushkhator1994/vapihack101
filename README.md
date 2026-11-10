# Laptop Closed

Close your laptop and keep shipping. When the lid shuts, your phone buzzes and rings: a voice agent that knows what you were working on picks up where you left off, researches by voice, and hands long builds to the cloud. It calls you back when they finish.

## How it works

1. **Lid closes.** A watcher polls macOS `AppleClamshellState` every second and takes a snapshot: git branch, last 3 commits, the top `TODO.md` item.
2. **Your phone gets you.** A push notification (ntfy) and a phone call (Vapi) arrive within seconds. Tapping the push opens a browser voice call with the same agent.
3. **You talk, it works.**
   - `research` — Exa answers the question in two spoken sentences with a source.
   - `delegate` — Manus takes the long job (build a page, write a report) in the cloud.
   - `status` / `get_context` — what's running, what you were doing.
4. **Jev decides when to interrupt.** Jev routes each request, and when a Manus task finishes it scores the result as call, push only, or stay silent.
5. **Callback.** The finished task arrives as a call and a push linking to the result.

## Built with

| Sponsor | Role |
| --- | --- |
| Vapi | Voice agent, outbound callback calls, browser web calls |
| Deepgram | Speech-to-text (Nova 3) and voice (Aura 2) |
| Exa | Live research answers with sources |
| Manus | Long-running build tasks in the cloud |
| Jev (TypeSafe) | Request routing and interrupt decisions |

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn httpx
cp .env.example .env        # fill in keys
ngrok http 8000             # put the https URL in PUBLIC_URL
.venv/bin/python setup_vapi.py   # creates the assistant, attaches your number
.venv/bin/uvicorn app:app --port 8000
sudo pmset -a disablesleep 1     # keep the Mac awake with the lid shut (undo with 0)
```

Subscribe to your `NTFY_TOPIC` in the ntfy phone app. `NOTIFY_MODE` is `push`, `call` or `both`.

- `/dashboard` — live log of lid state, Jev decisions, tasks
- `/talk` — browser voice call
- `POST /trigger` — fire the alert manually

## What's next

The glue server runs locally for the demo; in production it would be hosted, with the laptop only sending lid events. The work itself (Exa, Manus) already runs in the cloud.
