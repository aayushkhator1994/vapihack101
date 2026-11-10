# Laptop Closed — build plan for Opus

Updated 18:36. Submissions close 19:15. Steps are in priority order; stop wherever time runs out. Favor working over clean. Test each step with curl before moving on and tell the user what failed.

## Decisions already made

- **Notification is the primary path.** Lid closes or a task finishes, the phone gets a push, tapping it opens a voice call in the browser.
- **The phone call is the upgrade.** The user says outbound calls now work (tested from the Vapi UI). Keep the code, verify it in step 5, and use it if it holds. Until then nothing may depend on it.
- **The repo is this folder.** `https://github.com/aayushkhator1994/vapihack101` exists, is private and empty, and git on this machine can already reach it. Push this project there; then `REPO_PATH=.` is correct and the agent summarizes its own repo.

## Where things stand

**Working**
- `app.py` (FastAPI, port 8000) runs under `.venv`; ngrok tunnels `PUBLIC_URL` to it. Restart with `pkill -f "uvicorn app:app"` then `nohup .venv/bin/uvicorn app:app --port 8000 > server.log 2>&1 &`.
- Vapi assistant "Laptop Closed" exists (`VAPI_ASSISTANT_ID` in `.env`), attached to +13234587904. Tools `research`, `delegate`, `status`, `get_context` post to `PUBLIC_URL/vapi/tools`.
- Tested by curl: Exa research, Jev routing (`POST /v1/systemone`, model `jev-latest`), the tool handler through ngrok.

**Never tested**
- A real voice conversation reaching `/vapi/tools` (payload parsing is from docs).
- Manus `delegate` and its completion callback.
- The lid-close trigger with the lid actually shut.
- Outbound call from our code since the fix (last attempt at 18:09 returned 400 daily-limit).

**Only the user can do**
- Add `VAPI_PUBLIC_KEY` to `.env` (Vapi dashboard, API Keys, the public key).
- Install the ntfy app on their phone and subscribe to the topic from step 2.
- `sudo pmset -a disablesleep 1` before the lid test, `0` afterwards.
- Decide whether to make the GitHub repo public for judges.

Never print key values or commit `.env`.

## Steps

### 1. Git repo and push (4 min)
1. Write `.gitignore`: `.env`, `.venv/`, `server.log`, `__pycache__/`.
2. Write `.env.example` with the key names and comments, values blank.
3. Write `TODO.md` with three real next steps (the opening line reads it aloud).
4. `git init -b main`, then make at least three commits (server, Vapi setup, plan/docs) so "last 3 commits" has content.
5. `git remote add origin https://github.com/aayushkhator1994/vapihack101.git` and push. Check `git status` shows `.env` untracked-and-ignored before the first commit.
6. Leave `REPO_PATH=.`; confirm `snapshot()` now returns the branch and commits.

### 2. Notification path (10 min) — the core demo
1. Add to `.env`: `NTFY_TOPIC=laptop-closed-<12 random chars>` and `NOTIFY_MODE=push` (values: `push`, `call`, `both`).
2. `notify(title, body)` in `app.py`: POST to `https://ntfy.sh/$NTFY_TOPIC` with headers `Title`, `Priority: urgent`, `Tags: computer`, `Click: $PUBLIC_URL/talk`. Log the result.
3. Replace direct `call_me()` uses with `alert(message)`: sends the push when mode is `push` or `both`, places the call when `call` or `both`. Store `message` as `CTX["pending"]`.
4. `GET /talk`: a page that loads the Vapi Web SDK (`@vapi-ai/web` from a CDN; check the Vapi web docs for the current import), calls `vapi.start(assistantId, {firstMessage: <pending message>})` with `VAPI_PUBLIC_KEY`, and shows a big Start button (browsers need a tap before using the mic), a live transcript and an End button.
5. `POST /trigger-call` becomes `POST /trigger`, which calls `alert()` so it follows `NOTIFY_MODE`.
6. Test: curl `/trigger`, confirm the push arrives, tap it, talk, confirm `tool` lines in `server.log`.
7. ngrok's free tier shows a warning page in browsers once; the user taps through before the demo.

### 3. First real conversation (5 min)
- In the `/talk` call, ask a research question and ask for status.
- If tools fail, log the raw body in `vapi_tools()` and fix the parsing (`toolCallList` vs `toolWithToolCallList`, string vs object arguments).
- `research()` returns raw page highlights that sound bad aloud. Switch to Exa's `/answer` endpoint (check the Exa docs for the request and response fields), return about two sentences plus the source site name, and keep the current search as the fallback.

### 4. Manus end to end (8 min)
1. Curl `delegate` with a small task ("one-page landing page for Laptop Closed"). Confirm a task ID and URL in `/state`.
2. Watch `manus_watch()` reach `stopped`, then `notify_decision()`, then `alert()` with the summary. The push's click URL for a finished task should be the Manus `task_url`.
3. Handle `waiting`: reply through `POST /v2/task.sendMessage` with "Continue and use your best judgment."
4. Leave one task running so it is finished before the demo.
5. Skip `webhook.create`; the 10-second poller is enough.

### 5. Phone call upgrade (3 min)
1. Set `NOTIFY_MODE=both`, restart, curl `/trigger`.
2. If the call rings: keep `both` for the demo (push and ring together).
3. If Vapi returns the daily-limit 400 again: set `NOTIFY_MODE=push` and move on. Do not spend more time here.

### 6. Make Jev visibly do work (4 min)
- In `vapi_tools()`, run `route()` on each request and log it, so the dashboard shows a Jev decision per request. Today the LLM picks the tool and Jev is never called in a real conversation.
- `notify_decision()` on task completion: `call` uses `alert()` as configured, `text` forces push only, `silent` only logs.

### 7. Lid test (4 min, needs the user)
1. User runs `sudo pmset -a disablesleep 1`.
2. Close the lid for 30 seconds: the push (and ring, if step 5 passed) should arrive, and `PUBLIC_URL/dashboard` should load on the phone.
3. If the `ioreg` poll misses the close, `/trigger` is the stage backup.

### 8. Demo prep (last 5 min)
- Record one full run as a backup video.
- Script: show the dashboard, close the lid, phone buzzes, tap, ask a research question, ask Manus to build a landing page, end the call, the completion alert arrives.
- Sponsor line: Vapi (voice agent and calls), Deepgram (speech in and out), Exa (research), Manus (cloud work), Jev (routing and interrupt decisions).
- Commit and push the final code.

## Cut unless time remains
- `confirm_risky` spoken-yes gate, AdaL recap video, Manus webhooks.
- A shared secret on `/trigger` and `/vapi/tools` (both are public through ngrok; acceptable for the demo only).

## After the demo
- `sudo pmset -a disablesleep 0`
- `pkill -f "uvicorn app:app"` and stop ngrok.
- Rotate the keys in `.env` if the folder was ever shared.
