# Assignment Submission — AI-Powered Meeting Scheduler using Google Calendar

**Name:** Chaitu  
**Tool:** LangChain + Gemini + Google Calendar API  
**GitHub:** https://github.com/devchaitanya/AI-Powered-Meeting-Scheduler-using-Google-Calendar

---

## Deliverables

| File | Purpose |
|---|---|
| `tools.py` | All 14 tool definitions |
| `agent.py` | LangChain agent with agentic loop + model fallback |
| `model_selector.py` | Auto-selects best Gemini model by free-tier rate limit |
| `main.py` | Entry point — auth check + chat loop |
| `README.md` | Setup and usage guide |

---

## Task 1 — Setup (10 Marks)

**What was done:**
- Created virtual environment, installed all required packages
- Set `GOOGLE_API_KEY` in `.env`
- Enabled Google Calendar API, created OAuth 2.0 Desktop App credentials
- Added Gmail as test user on OAuth consent screen
- Implemented OOB OAuth flow in `tools.py` → `get_calendar_service()`

**Completion proof:**
```
[Auth OK] Connected to calendar: dhonichaitu7@gmail.com
```
This line prints on every startup confirming authentication works and the Calendar ID is visible.

**OAuth flow location:** [tools.py](tools.py) lines 15–32

---

## Task 2 — Trivial Meeting Creation (30 Marks)

**What was built:**
- `create_event` tool with `@tool` decorator
- `llm.bind_tools([...])` attaches all tools to the LLM
- Agent checks `response.tool_calls` to detect when LLM wants to use a tool
- Tool is executed in Python and returns the event link

**Test inputs and expected results:**

| Input | Expected | Status |
|---|---|---|
| `Schedule a 1-hour meeting called Team Sync tomorrow at 10am` | Event created, link returned | ✅ |
| `Book a 30-minute standup at 9am this Friday` | Event at correct date/time | ✅ |
| `Set up a 45-min call with raj@example.com on Monday at 3pm` | Event with attendee invited | ✅ |

**Key design decision:** Instead of hardcoding the date in the prompt, a `get_current_datetime` tool resolves relative dates ("tomorrow", "this Friday") — the proper LangChain way, as the LLM calls the tool itself rather than guessing.

**Relevant code:** [tools.py](tools.py) — `create_event`, [agent.py](agent.py) — `bind_tools`, `response.tool_calls`

---

## Task 3 — Conflict Detection & Past Date Validation (25 Marks)

**What was built:**

**Tool 1 — `get_calendar_events(date)`**
- Calls `service.events().list()` with `timeMin`, `timeMax` as RFC3339 timestamps
- Sets `singleEvents=True`
- Returns event name, start time, and end time as readable string

**Tool 2 — Conflict-aware `create_event`**
- Guard 1 (past date): `start_dt < datetime.now()` → returns `PAST_DATE_ERROR`
- Guard 2 (overlap): implements exact formula from PDF:
  ```
  A_start < B_end AND A_end > B_start
  ```
  Code: `if start_dt < e_end and end_dt > e_start`

**Agentic loop** (in `agent.py`):
```
LLM call → tool_calls? → execute tools → append ToolMessage → LLM call → repeat
```
Loop exits when LLM returns text with no tool calls. Capped at 5 iterations.

**Test cases:**
```
> Schedule Design Review yesterday at 3pm
→ PAST_DATE_ERROR: cannot schedule — date has already passed

> Schedule at 10am tomorrow  (slot occupied)
→ CONFLICT: 'Team Sync' occupies 10:00–11:00 on 2026-04-09
```

**Relevant code:** [tools.py](tools.py) — `get_calendar_events`, `create_event` guards, [agent.py](agent.py) — agentic loop with `ToolMessage`

---

## Task 4 — Smart Alternative Suggestions (35 Marks)

**What was built:**

**Tool 1 — `find_free_slots(date, duration_minutes)`**
- Scans 9am–6pm working hours window
- Returns every gap large enough to fit the meeting

**Tool 2 — `analyse_booking_patterns()`**
- Fetches last 30 days of events with `maxResults=200`
- Returns: busiest day, lightest day, peak booking hour, average duration

**How alternatives are generated (zero extra LLM calls):**

When `create_event` detects a conflict, it immediately runs in Python:
1. `_get_free_slots()` on the originally requested date → Alternative 1 & 2
2. `_get_booking_patterns()` → identifies lightest day of week
3. `_get_free_slots()` on lightest day → Alternative 3 with reason

All of this happens inside a single tool call response — no extra LLM invocations for Task 4. This avoids rate limit exhaustion.

**Example output:**
```
CONFLICT on 2026-04-09: 'Team Sync' is already booked 10:00–11:00.

Ranked alternatives for your 60-min 'Design Review':
  1. Same day (2026-04-09) at 11:00 [reason: keeps it on your originally planned day]
  2. Same day (2026-04-09) at 14:00 [reason: another open window the same day]
  3. Wednesday 2026-04-10 at 09:00 [reason: your lightest day — 1 meeting vs 4 on your busiest day]
```

**Relevant code:** [tools.py](tools.py) — `find_free_slots`, `analyse_booking_patterns`, `_get_free_slots`, `_get_booking_patterns`, `create_event` conflict block

---

## Bonus — Calendar Intelligence Queries (20 Marks)

**What was built:**

`query_calendar_insights(question: str)` — fetches live calendar data and computes:
- Free days this week
- Total meeting time this week (hours + minutes)
- Events by day this week
- Busiest day this month
- Total events this month
- Today's agenda

**Test inputs:**
```
> Which days am I free this week?
> How many hours of meetings do I have this week?
> What was my busiest day this month?
> What do I have today?
```

All answers computed from real Google Calendar data — no hallucination.

**Relevant code:** [tools.py](tools.py) — `query_calendar_insights`

---

## Additional Features (Beyond PDF Requirements)

| Feature | Tool | What it does |
|---|---|---|
| Upcoming agenda | `list_upcoming_events(days)` | Next N days, grouped by date |
| Cancel meeting | `cancel_event(title, date)` | Delete by partial title match |
| Reschedule | `reschedule_event(...)` | Move event, preserves duration + attendees, checks conflicts |
| Recurring meetings | `create_recurring_event(...)` | RRULE daily/weekly/monthly with N occurrences |
| Next free slot | `get_next_available_slot(duration, from_date)` | Scans forward across days, skips weekends |
| Add agenda | `add_event_description(title, date, desc)` | Attach notes to existing event |
| Attendee check | `check_attendee_availability(email, date, ...)` | Google Freebusy API before inviting |
| Search | `search_events(keyword, days)` | Full-text search across upcoming events |
| Auto model selection | `model_selector.py` | Queries Gemini API, picks highest-RPM available model |
| Model fallback | `agent.py` | On 429: waits if per-minute, switches model if daily quota exhausted |
| Conversation memory | `agent.py` — `history` list | Context persists across turns; type `reset` to clear |
| Token management | `agent.py` | History capped at 12 messages; tool results truncated to 600 chars |

---

## LangChain Internals Used

| Concept | Where used |
|---|---|
| `@tool` decorator | Converts Python functions to LangChain tools with auto JSON schema |
| `llm.bind_tools(ALL_TOOLS)` | Attaches all 14 tools to the LLM |
| `response.tool_calls` | Detects when LLM has decided to call a tool |
| `ToolMessage` | Passes tool results back into conversation history |
| `SystemMessage` + `HumanMessage` | Structures the conversation with `convert_system_message_to_human=True` for Gemini |
| Agentic loop | Keeps invoking until LLM returns text with no tool calls |

---

## Architecture Summary

```
python3 main.py
    │
    ├── OAuth check → print Calendar ID          (Task 1)
    ├── model_selector → ranked Gemini models
    └── chat loop
            │
            ▼
        run_agent(user_input)
            │
            ├── append HumanMessage to history
            └── agentic loop (max 5 iterations)
                    │
                    ├── _trim_history() → keep last 12 msgs
                    ├── llm_with_tools.invoke(history)
                    │
                    ├── tool_calls present?
                    │       ├── YES → execute in Python
                    │       │          └── create_event + CONFLICT?
                    │       │                → compute alternatives in Python (Task 3+4)
                    │       │                → zero extra LLM calls
                    │       │          → append ToolMessage (truncated to 600 chars)
                    │       │          → loop again
                    │       │
                    │       └── NO  → extract text → return to user
                    │
                    └── 429 RESOURCE_EXHAUSTED?
                            ├── per-minute → wait retryDelay → retry same model
                            └── daily      → switch to next model in fallback list
```
