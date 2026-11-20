# RUBIKS: your AI operator, on every screen

Rubiks is an AI agent that lives on your Windows PC and does real work there: files, apps, browser, terminal, screen vision, WhatsApp, memory, and long multi-step tasks. Version 6 turns it into one brain you can reach from **your phone, tablet, desktop browser, or terminal**, with logins, live activity, and approvals before anything risky.

```
 phone ─┐
 tablet ┼──►  RUBIKS server on your PC  ──►  54 guarded PC tools  ──►  your PC
 desktop┤     (login · device modes ·        (read · open · act · risky)
 rubiks ┘      approvals · audit log)         Gemini brain or offline mode
```

## Quick start

```powershell
pip install -r requirements.txt
.\rubiks.bat            # chat in the terminal (starts the server for you)
.\rubiks.bat serve --lan   # serve the app to your phone on the same Wi-Fi
```

Open `http://127.0.0.1:7575` on the PC. The first account you create becomes the **owner**. Add a free Gemini key in **Settings → Brain** (or `rubiks key`). Without a key, Rubiks runs in **offline mode**: quick PC commands only.

Keys live in `.env` (never committed). One key per line:

```
GEMINI_API_KEYS="AQ.…"            # Google AI Studio key (AIza… or AQ.…); comma-separate several to rotate
ELEVENLABS_API_KEY="sk_…"         # optional: Rubiks' voice on every device
ELEVENLABS_VOICE_ID="…"           # the ElevenLabs voice to use
```

**Models:** Rubiks uses `gemini-3.8-flash` and automatically falls back (3-flash-preview → flash-latest → 3.7 → 3.5 → flash-lite) when Google retires a model or one is overloaded, then sticks with the one that worked for 10 minutes. Override with `RUBIKS_MODEL`.

**Voice:** with ElevenLabs set, every reply's speaker button, voice-mode replies (speak to it, it speaks back), and the original `rubiks voice` assistant all use your voice. Otherwise it falls back to the browser or edge-tts voice.

On your phone: run `rubiks pair` (or Settings → Your devices) for the link, open it on the same Wi-Fi, then **Share → Add to Home Screen**.

## Device modes: the AI changes with the screen

When you sign in, Rubiks asks where you're using it (and suggests one it detected). The mode changes the layout **and** how the AI behaves:

| Mode | Layout | How Rubiks answers | Approvals |
|---|---|---|---|
| **Phone** (Pocket) | One column, chats and live activity as bottom sheets, composer and approvals in thumb reach, voice input | 3 sentences max, no tables, says what changed on the PC, can send you a screenshot of the PC | Asks for anything that changes the PC |
| **Tablet** (Studio) | Chats beside the conversation, live activity as a drawer | ~120 words, light formatting | Asks for anything that changes the PC |
| **Desktop** (Command) | Chats · conversation · live rail (approvals, task progress, PC vitals, action log), Ctrl+K palette | Full detail, tables and code | Asks only for risky actions (or none with Full autopilot) |
| **Terminal** (`rubiks`) | Your shell | Plain monospace output | Same as desktop; can also be approved from your phone |

## Safety: approvals and audit

Every tool the AI can call is wrapped by `rubiks_core/guard.py` and has a tier:

- **read**: stats, search, files, screen. Always runs.
- **open**: apps, sites, media. Always runs.
- **act**: typing, clicking, writing files. Runs at the desk; asks on phone or tablet.
- **risky**: terminal, Python, sending WhatsApp, closing programs, creating accounts, self-upgrades, autonomous tasks. Always asks, unless the owner turns on *Full autopilot* (desktop and terminal only).

Approval requests reach **every device you're signed in on**. A command typed in the terminal can be approved from your phone. Everything is written to an audit log (`rubiks audit`, or the live rail).

## Terminal CLI

```
rubiks                      interactive chat
rubiks ask "question"       one answer; pipe-friendly:  Get-Content log.txt | rubiks ask "what failed?"
rubiks task "goal"          start an autonomous task and watch its steps
rubiks status               PC vitals, brain, phone link, running task
rubiks audit -n 20          what Rubiks did, from which device
rubiks serve --lan          run the server for phones on your Wi-Fi
rubiks pair | invite | key  phone link · teammate invite · add Gemini key
rubiks voice                the original voice assistant (main.py)
```

## Layout

```
server.py              Flask server: web app + API + live event stream
rubiks_cli.py          terminal client (rubiks.bat)
rubiks_core/
  auth.py              accounts, sessions, invites (first user = owner)
  devices.py           per-device system prompts and personas
  guard.py             tool tiers, approvals, audit
  engine.py            Gemini engine (key rotation) + offline engine
  events.py            per-user live bus (cross-device approvals/activity)
web/                   installable web app (PWA): index.html, app.css, app.js, sw.js
*.py (root)            the original tools and voice assistant, reused by the server
```

## Privacy

Accounts, chats and the audit log live in `%LOCALAPPDATA%\Rubiks` on your PC. Only your messages (and screenshots you ask it to read) go to the AI model you connect. Keys stay in `.env` on your PC.

## Notes

- Phone access over plain `http://` on your Wi-Fi works for chat and approvals. Installing as a full PWA on Android, and voice input, need HTTPS. Put it behind Cloudflare Tunnel or Tailscale for access from anywhere.
- The original voice HUD (`main.py`, `rubiks_hud.html`) still works via `rubiks voice`, but it does **not** go through the approval guard yet.
- Design: graphite/silver/white with liquid glass on floating layers (top bar, composer, panels, sheets, menus). Respects reduced motion and reduced transparency. Motion rules live in `.claude/skills/ux-motion/SKILL.md`.
