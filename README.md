# AI Meeting Scheduler — LangChain + Google Calendar

Natural language meeting scheduling agent built with LangChain and Google Calendar API.

---
## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install langchain langchain-google-genai google-auth google-auth-oauthlib \
            google-api-python-client python-dotenv google-genai
```

**`.env`**
```
GOOGLE_API_KEY=your_gemini_key_here
```

**Google Calendar API** (one-time setup)
1. [console.cloud.google.com](https://console.cloud.google.com) → New Project → Enable **Google Calendar API**
2. Credentials → Create → OAuth 2.0 Client ID → Desktop App → Download JSON → rename to `credentials.json`
3. OAuth consent screen → Test users → add your Gmail address

**First run** — OAuth opens a URL in terminal. Open it in browser, grant access, paste the code back. `token.json` is saved; future runs skip this step.

```bash
python3 main.py
```

Expected output:
```
[Auth OK] Connected to calendar: you@gmail.com

┌─ Available Gemini Models ──────────────────...
│  gemini-2.0-flash-lite    30   1,000,000  ◄ primary
│  gemini-2.0-flash         15   1,000,000  ◄ fallback 1
│  ...
└────────────────────────────────────────────...

[Model] Primary: gemini-2.0-flash-lite  |  Fallbacks: [...]
AI Scheduler Ready!
```

---

## Files

```
credentials.json    ← Google Cloud OAuth credentials
token.json          ← auto-generated on first run
.env                ← GOOGLE_API_KEY
tools.py            ← all 14 calendar tools
agent.py            ← LangChain agentic loop + model fallback
model_selector.py   ← auto-selects best Gemini model by rate limit
main.py             ← chat entry point
```

---

## What it does

| Input | Behaviour |
|---|---|
| `Schedule Team Sync tomorrow at 10am for 1 hour` | Resolves date via tool, creates event, returns link |
| `Book standup at 9am this Friday for 30 min` | Resolves "this Friday" to exact date, creates event |
| `Set up a call with raj@example.com Monday 3pm 45min` | Checks raj's availability, creates event with attendee |
| `Schedule a meeting at 8am today` *(past time)* | Rejected with clear message — no event created |
| `Schedule at 10am tomorrow` *(slot taken)* | Detects conflict, suggests 2–3 free alternatives with reasons |
| `Create weekly standup every Monday at 9am for 10 weeks` | Creates recurring event via RRULE |
| `When can I fit a 2-hour meeting this week?` | Scans forward day-by-day, returns next free slot |
| `Cancel Team Sync on 2026-04-10` | Deletes the event |
| `Move standup from Monday to Wednesday at 10am` | Reschedules, checks for conflicts at new time |
| `Add agenda "review Q1, assign tasks" to Team Sync tomorrow` | Updates event description |
| `Is raj@example.com free Friday at 2pm for 1 hour?` | Freebusy API check before inviting |
| `Find all standup meetings` | Keyword search across upcoming events |
| `Which days am I free this week?` | Computes from live calendar data |
| `How many hours of meetings this week?` | Sums durations from real events |
| `What was my busiest day this month?` | 30-day pattern analysis |

Type `reset` to clear conversation context. Type `exit` to quit.

---

## Tools (14)

**Required by assignment**
| Tool | Task |
|---|---|
| `get_current_datetime` | Resolves relative dates — called before any date computation |
| `get_calendar_events` | Fetches events for a date (`singleEvents=True`, RFC3339 timestamps) |
| `create_event` | Creates event; includes past-date guard + overlap check (`A_start < B_end AND A_end > B_start`); on conflict automatically computes alternatives in Python (no extra LLM calls) |
| `find_free_slots` | Scans 9am–6pm for gaps ≥ required duration |
| `analyse_booking_patterns` | Last 30 days: busiest/lightest days, peak hour, avg duration (`maxResults=200`) |
| `query_calendar_insights` | Answers open-ended questions — free days, meeting hours, busiest day |

**Additional**
| Tool | Purpose |
|---|---|
| `list_upcoming_events` | Next N days agenda grouped by date |
| `cancel_event` | Delete event by title + date |
| `reschedule_event` | Move event, preserves duration and attendees |
| `create_recurring_event` | RRULE daily/weekly/monthly repeating meetings |
| `get_next_available_slot` | Scans forward across days (skips weekends) for next free slot |
| `add_event_description` | Attach agenda/notes to an existing event |
| `check_attendee_availability` | Freebusy API — verify attendee is free before inviting |
| `search_events` | Full-text keyword search across upcoming events |

---

## Architecture

```
User input
    │
    ▼
Agentic loop (max 5 iterations)
    │
    ├─ LLM call → tool_calls?
    │       ├─ Yes → execute tool in Python → append ToolMessage → repeat
    │       └─ No  → extract text → return to user
    │
    └─ On conflict inside create_event:
           Python computes free slots + booking patterns → returns alternatives
           (zero extra LLM calls for Task 3 + Task 4)
```

**Token optimisations**
- System prompt: ~130 tokens (tool descriptions come from docstrings via `bind_tools`)
- History: capped at last 12 messages — older turns dropped
- Tool results in history: truncated to 600 chars
- Conflict resolution: pure Python, no extra LLM calls

**Model selection**
- Queries Gemini API at startup for available models
- Ranks by free-tier RPM (requests/minute)
- On 429 per-minute limit: waits the retry delay, retries same model
- On 429 daily limit: instantly switches to next model in fallback list
- When all models exhausted: shows clear message with reset time
