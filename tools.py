import os
import datetime
from collections import defaultdict
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from langchain_core.tools import tool

SCOPES = ['https://www.googleapis.com/auth/calendar']
TZ_OFFSET = "+05:30"
TZ_NAME = "Asia/Kolkata"


# ── Auth helper ───────────────────────────────────────────────────────────────

def get_calendar_service():
    creds = None
    if os.path.exists('token.json'):
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            'credentials.json', SCOPES,
            redirect_uri='urn:ietf:wg:oauth:2.0:oob'
        )
        auth_url, _ = flow.authorization_url(prompt='consent')
        print("\n-> Go to this URL:\n", auth_url)
        code = input("\n-> Paste the authorization code here: ")
        flow.fetch_token(code=code)
        creds = flow.credentials
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('calendar', 'v3', credentials=creds)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _naive_dt(dt_str: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(dt_str).replace(tzinfo=None)


def _fetch_events_for_date(service, date: str):
    day_start = datetime.datetime.strptime(date, "%Y-%m-%d")
    day_end = day_start + datetime.timedelta(days=1)
    result = service.events().list(
        calendarId='primary',
        timeMin=day_start.isoformat() + TZ_OFFSET,
        timeMax=day_end.isoformat() + TZ_OFFSET,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    return result.get('items', [])


def _fmt_event(e) -> str:
    start = e['start'].get('dateTime', e['start'].get('date', ''))
    end   = e['end'].get('dateTime',   e['end'].get('date', ''))
    if 'T' in start:
        start = _naive_dt(start).strftime('%H:%M')
    if 'T' in end:
        end = _naive_dt(end).strftime('%H:%M')
    return f"  • {e.get('summary', 'Untitled')}: {start} → {end}"


def _get_free_slots(service, date: str, duration_minutes: int) -> list:
    """Internal: returns list of (slot_start, slot_end) naive datetimes."""
    events = _fetch_events_for_date(service, date)
    day = datetime.datetime.strptime(date, "%Y-%m-%d")
    work_start = day.replace(hour=9,  minute=0)
    work_end   = day.replace(hour=18, minute=0)
    busy = sorted([
        (_naive_dt(e['start']['dateTime']), _naive_dt(e['end']['dateTime']))
        for e in events if e['start'].get('dateTime')
    ])
    slots, cursor = [], work_start
    for b_start, b_end in busy:
        if cursor + datetime.timedelta(minutes=duration_minutes) <= b_start:
            slots.append((cursor, b_start))
        cursor = max(cursor, b_end)
    if cursor + datetime.timedelta(minutes=duration_minutes) <= work_end:
        slots.append((cursor, work_end))
    return slots


def _get_booking_patterns(service) -> dict:
    """Internal: returns {busiest_day, lightest_day, peak_hour, avg_duration}."""
    now = datetime.datetime.now()
    result = service.events().list(
        calendarId='primary',
        timeMin=(now - datetime.timedelta(days=30)).isoformat() + TZ_OFFSET,
        timeMax=now.isoformat() + TZ_OFFSET,
        singleEvents=True,
        orderBy='startTime',
        maxResults=200
    ).execute()
    events = result.get('items', [])
    if not events:
        return {"busiest_day": None, "lightest_day": None, "peak_hour": 10, "avg_duration": 30}
    day_counts = defaultdict(int)
    hour_counts = defaultdict(int)
    durations = []
    for e in events:
        s  = e['start'].get('dateTime')
        en = e['end'].get('dateTime')
        if not s:
            continue
        s_dt = _naive_dt(s)
        day_counts[s_dt.strftime('%A')] += 1
        hour_counts[s_dt.hour] += 1
        if en:
            durations.append(int((_naive_dt(en) - s_dt).total_seconds() // 60))
    return {
        "busiest_day":  max(day_counts, key=day_counts.get) if day_counts else None,
        "lightest_day": min(day_counts, key=day_counts.get) if day_counts else None,
        "peak_hour":    max(hour_counts, key=hour_counts.get) if hour_counts else 10,
        "avg_duration": sum(durations) // len(durations) if durations else 30,
        "day_counts":   dict(day_counts),
    }


def _next_date_for_weekday(weekday_name: str) -> str | None:
    """Return the YYYY-MM-DD of the next weekday occurrence, skipping weekends."""
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    if weekday_name not in days[:5]:   # only Mon–Fri are valid working days
        return None
    target = days.index(weekday_name)
    today = datetime.date.today()
    delta = (target - today.weekday() + 7) % 7
    if delta == 0:
        delta = 7  # never return today — always the *next* occurrence
    return (today + datetime.timedelta(days=delta)).strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 0 — Current date/time
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def get_current_datetime() -> str:
    """
    Returns the current date, time, and day of the week (IST).
    MUST be called first whenever the user mentions relative dates or times
    such as 'today', 'tomorrow', 'this Friday', 'next Monday', 'in 2 days'.
    """
    now = datetime.datetime.now()
    return (
        f"Current date : {now.strftime('%Y-%m-%d')} ({now.strftime('%A')})\n"
        f"Current time : {now.strftime('%H:%M')} IST"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 1 — Fetch events for a specific date
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def get_calendar_events(date: str) -> str:
    """
    Fetch all calendar events on a given date.
    Args:
        date: YYYY-MM-DD
    Returns:
        Human-readable list with event name, start time, and end time.
    """
    service = get_calendar_service()
    events = _fetch_events_for_date(service, date)
    if not events:
        return f"No events on {date}."
    lines = [f"Events on {date}:"] + [_fmt_event(e) for e in events]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 2 — Create event (with past-date guard + conflict check)
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def create_event(
    title: str,
    date: str,
    start_time: str,
    duration_minutes: int,
    attendee_email: str = None
) -> str:
    """
    Create a meeting in Google Calendar.
    - Rejects past dates immediately.
    - On conflict: automatically computes free slots and smart alternatives
      (Task 3 + Task 4) in Python — no extra LLM calls needed.
    Args:
        title           : Meeting title
        date            : YYYY-MM-DD
        start_time      : HH:MM (24-hour)
        duration_minutes: Duration in minutes
        attendee_email  : Optional attendee email
    Returns:
        Confirmation link on success, or a detailed conflict + alternatives message.
    """
    now = datetime.datetime.now()
    start_dt = datetime.datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)

    # Guard 1 — past date (Task 3)
    if start_dt < now:
        return (
            f"PAST_DATE_ERROR: '{title}' cannot be scheduled — "
            f"{date} {start_time} has already passed "
            f"(current time: {now.strftime('%Y-%m-%d %H:%M')})."
        )

    service = get_calendar_service()

    # Guard 2 — conflict check (Task 3): A_start < B_end AND A_end > B_start
    conflict_event = None
    for e in _fetch_events_for_date(service, date):
        s_str = e['start'].get('dateTime')
        e_str = e['end'].get('dateTime')
        if not s_str:
            continue
        e_start = _naive_dt(s_str)
        e_end   = _naive_dt(e_str)
        if start_dt < e_end and end_dt > e_start:
            conflict_event = (e.get('summary', 'Untitled'), e_start, e_end)
            break

    if conflict_event:
        # ── Task 4: compute alternatives entirely in Python, zero extra LLM calls ──
        conflict_name, c_start, c_end = conflict_event

        # Alt 1 — free slots on the same requested day
        same_day_slots = _get_free_slots(service, date, duration_minutes)

        # Alt 2 & 3 — analyse patterns → find lightest day → get slots there
        patterns = _get_booking_patterns(service)
        lightest = patterns["lightest_day"]
        lightest_date = _next_date_for_weekday(lightest) if lightest else None
        lightest_slots = _get_free_slots(service, lightest_date, duration_minutes) if lightest_date else []

        # Format the response
        lines = [
            f"CONFLICT on {date}: '{conflict_name}' is already booked "
            f"{c_start.strftime('%H:%M')}–{c_end.strftime('%H:%M')}.",
            "",
            f"Here are ranked alternatives for your {duration_minutes}-min '{title}':",
        ]

        rank = 1
        if same_day_slots:
            s, e_ = same_day_slots[0]
            lines.append(
                f"  {rank}. Same day ({date}) at {s.strftime('%H:%M')} "
                f"[reason: keeps it on your originally planned day]"
            )
            rank += 1
            if len(same_day_slots) > 1:
                s2, e2 = same_day_slots[1]
                lines.append(
                    f"  {rank}. Same day ({date}) at {s2.strftime('%H:%M')} "
                    f"[reason: another open window the same day]"
                )
                rank += 1

        if lightest_date and lightest_slots:
            s, e_ = lightest_slots[0]
            lines.append(
                f"  {rank}. {lightest} {lightest_date} at {s.strftime('%H:%M')} "
                f"[reason: your lightest day — "
                f"{patterns['day_counts'].get(lightest, 0)} meetings vs "
                f"{patterns['day_counts'].get(patterns['busiest_day'], 0)} on your busiest day]"
            )

        if rank == 1:
            lines.append("  No free slots found in the next 7 days. Try a different week.")

        return "\n".join(lines)

    # No conflict — create the event
    event_body = {
        'summary': title,
        'start': {'dateTime': start_dt.isoformat() + TZ_OFFSET, 'timeZone': TZ_NAME},
        'end':   {'dateTime': end_dt.isoformat()   + TZ_OFFSET, 'timeZone': TZ_NAME},
    }
    if attendee_email:
        event_body['attendees'] = [{'email': attendee_email}]

    created = service.events().insert(calendarId='primary', body=event_body).execute()
    return f"SUCCESS: '{title}' created on {date} at {start_time}. Link: {created.get('htmlLink')}"


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 3 — List upcoming events
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def list_upcoming_events(days: int = 7) -> str:
    """
    List all upcoming calendar events for the next N days (default 7).
    Args:
        days: Number of days to look ahead (1–30)
    Returns:
        Events grouped by date.
    """
    service = get_calendar_service()
    now = datetime.datetime.now()
    end = now + datetime.timedelta(days=max(1, min(days, 30)))

    result = service.events().list(
        calendarId='primary',
        timeMin=now.isoformat() + TZ_OFFSET,
        timeMax=end.isoformat() + TZ_OFFSET,
        singleEvents=True,
        orderBy='startTime',
        maxResults=50
    ).execute()

    events = result.get('items', [])
    if not events:
        return f"No upcoming events in the next {days} day(s)."

    by_date = defaultdict(list)
    for e in events:
        s = e['start'].get('dateTime', e['start'].get('date', ''))
        date_key = s[:10]
        by_date[date_key].append(_fmt_event(e))

    lines = [f"Upcoming events (next {days} day(s)):"]
    for date_key in sorted(by_date):
        dt = datetime.datetime.strptime(date_key, "%Y-%m-%d")
        lines.append(f"\n{dt.strftime('%A, %d %b %Y')}:")
        lines.extend(by_date[date_key])
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 4 — Find free slots on a date
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def find_free_slots(date: str, duration_minutes: int) -> str:
    """
    Find all free time windows on a date within working hours (9am–6pm).
    Args:
        date            : YYYY-MM-DD
        duration_minutes: Minimum gap size needed (in minutes)
    Returns:
        List of available time windows.
    """
    service = get_calendar_service()
    events = _fetch_events_for_date(service, date)

    day = datetime.datetime.strptime(date, "%Y-%m-%d")
    work_start = day.replace(hour=9,  minute=0)
    work_end   = day.replace(hour=18, minute=0)

    busy = sorted([
        (_naive_dt(e['start']['dateTime']), _naive_dt(e['end']['dateTime']))
        for e in events if e['start'].get('dateTime')
    ])

    free, cursor = [], work_start
    for b_start, b_end in busy:
        if cursor + datetime.timedelta(minutes=duration_minutes) <= b_start:
            free.append(f"  • {cursor.strftime('%H:%M')} – {b_start.strftime('%H:%M')}")
        cursor = max(cursor, b_end)
    if cursor + datetime.timedelta(minutes=duration_minutes) <= work_end:
        free.append(f"  • {cursor.strftime('%H:%M')} – {work_end.strftime('%H:%M')}")

    if not free:
        return f"No free slots on {date} for a {duration_minutes}-min meeting."
    return f"Free slots on {date} (≥{duration_minutes} min):\n" + "\n".join(free)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 5 — Cancel an event
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def cancel_event(title: str, date: str) -> str:
    """
    Cancel (delete) a calendar event by its title and date.
    Args:
        title: Exact or partial event title to search for
        date : YYYY-MM-DD
    Returns:
        Confirmation or error message.
    """
    service = get_calendar_service()
    events = _fetch_events_for_date(service, date)

    matches = [e for e in events if title.lower() in e.get('summary', '').lower()]
    if not matches:
        return f"No event matching '{title}' found on {date}."
    if len(matches) > 1:
        names = ", ".join(f"'{e.get('summary')}'" for e in matches)
        return f"Multiple matches found: {names}. Please be more specific."

    event = matches[0]
    service.events().delete(calendarId='primary', eventId=event['id']).execute()
    return f"Cancelled: '{event.get('summary')}' on {date}."


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 6 — Reschedule an event
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def reschedule_event(title: str, old_date: str, new_date: str, new_start_time: str) -> str:
    """
    Reschedule an existing calendar event to a new date and time.
    Preserves the original duration and attendees.
    Args:
        title          : Event title (partial match ok)
        old_date       : Current date of the event (YYYY-MM-DD)
        new_date       : Target date (YYYY-MM-DD)
        new_start_time : New start time HH:MM (24-hour)
    Returns:
        Confirmation with new event link, or error message.
    """
    service = get_calendar_service()
    events = _fetch_events_for_date(service, old_date)
    matches = [e for e in events if title.lower() in e.get('summary', '').lower()]

    if not matches:
        return f"No event matching '{title}' on {old_date}."
    if len(matches) > 1:
        names = ", ".join(f"'{e.get('summary')}'" for e in matches)
        return f"Multiple matches: {names}. Be more specific."

    event = matches[0]
    s_str = event['start'].get('dateTime')
    e_str = event['end'].get('dateTime')
    if not s_str:
        return "Cannot reschedule an all-day event."

    old_start = _naive_dt(s_str)
    old_end   = _naive_dt(e_str)
    duration  = int((old_end - old_start).total_seconds() // 60)

    new_start = datetime.datetime.strptime(f"{new_date} {new_start_time}", "%Y-%m-%d %H:%M")
    new_end   = new_start + datetime.timedelta(minutes=duration)

    # Conflict check on new slot
    now = datetime.datetime.now()
    if new_start < now:
        return f"Cannot reschedule to {new_date} {new_start_time} — that time has passed."

    for e in _fetch_events_for_date(service, new_date):
        if e['id'] == event['id']:
            continue
        s2 = e['start'].get('dateTime')
        e2 = e['end'].get('dateTime')
        if not s2:
            continue
        if new_start < _naive_dt(e2) and new_end > _naive_dt(s2):
            return (
                f"Conflict on {new_date}: '{e.get('summary')}' is at "
                f"{_naive_dt(s2).strftime('%H:%M')}–{_naive_dt(e2).strftime('%H:%M')}."
            )

    event['start'] = {'dateTime': new_start.isoformat() + TZ_OFFSET, 'timeZone': TZ_NAME}
    event['end']   = {'dateTime': new_end.isoformat()   + TZ_OFFSET, 'timeZone': TZ_NAME}

    updated = service.events().update(
        calendarId='primary', eventId=event['id'], body=event
    ).execute()
    return (
        f"Rescheduled '{event.get('summary')}' to {new_date} at {new_start_time} "
        f"({duration} min). Link: {updated.get('htmlLink')}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 7 — Analyse booking patterns (last 30 days)
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def analyse_booking_patterns() -> str:
    """
    Analyse the user's Google Calendar for the last 30 days.
    Returns busiest/lightest days, peak hours, and average meeting duration.
    Used to suggest smart alternatives when a slot is blocked.
    """
    service = get_calendar_service()
    now = datetime.datetime.now()
    result = service.events().list(
        calendarId='primary',
        timeMin=(now - datetime.timedelta(days=30)).isoformat() + TZ_OFFSET,
        timeMax=now.isoformat() + TZ_OFFSET,
        singleEvents=True,
        orderBy='startTime',
        maxResults=200
    ).execute()

    events = result.get('items', [])
    if not events:
        return "No events in the last 30 days to analyse."

    day_counts  = defaultdict(int)
    hour_counts = defaultdict(int)
    durations   = []

    for e in events:
        s  = e['start'].get('dateTime')
        en = e['end'].get('dateTime')
        if not s:
            continue
        s_dt = _naive_dt(s)
        day_counts[s_dt.strftime('%A')] += 1
        hour_counts[s_dt.hour] += 1
        if en:
            durations.append(int((_naive_dt(en) - s_dt).total_seconds() // 60))

    busiest = max(day_counts, key=day_counts.get)
    lightest = min(day_counts, key=day_counts.get)
    peak_hr  = max(hour_counts, key=hour_counts.get)
    avg_dur  = sum(durations) // len(durations) if durations else 0
    breakdown = ", ".join(f"{d}: {c}" for d, c in sorted(day_counts.items()))

    return (
        f"Last 30 days ({len(events)} events):\n"
        f"  Busiest day  : {busiest} ({day_counts[busiest]} meetings)\n"
        f"  Lightest day : {lightest} ({day_counts[lightest]} meetings)\n"
        f"  Peak hour    : {peak_hr:02d}:00\n"
        f"  Avg duration : {avg_dur} min\n"
        f"  Per-day count: {breakdown}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 8 — Calendar insights (Bonus — answers open-ended questions)
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def query_calendar_insights(question: str) -> str:
    """
    Answer open-ended questions about the user's calendar using real data.
    Examples:
      - 'Which days am I free this week?'
      - 'How many hours of meetings do I have this week?'
      - 'What was my busiest day this month?'
      - 'How many meetings do I have today?'
    Args:
        question: The natural language question about the calendar.
    Returns:
        A data-driven answer computed from live calendar data.
    """
    service = get_calendar_service()
    now = datetime.datetime.now()

    # ── This week (Mon–Sun) ──────────────────────────────────────────────────
    week_start = (now - datetime.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end = week_start + datetime.timedelta(days=7)

    # ── This month (1st → now) ───────────────────────────────────────────────
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def _fetch(t_min, t_max, limit=200):
        return service.events().list(
            calendarId='primary',
            timeMin=t_min.isoformat() + TZ_OFFSET,
            timeMax=t_max.isoformat() + TZ_OFFSET,
            singleEvents=True,
            orderBy='startTime',
            maxResults=limit
        ).execute().get('items', [])

    week_events  = _fetch(week_start, week_end)
    month_events = _fetch(month_start, now)
    today_events = _fetch(
        now.replace(hour=0, minute=0, second=0),
        now.replace(hour=23, minute=59, second=59)
    )

    # ── Week stats ───────────────────────────────────────────────────────────
    week_by_day = defaultdict(list)
    week_minutes = 0
    for e in week_events:
        s  = e['start'].get('dateTime')
        en = e['end'].get('dateTime')
        if s:
            s_dt = _naive_dt(s)
            week_by_day[s_dt.strftime('%A %Y-%m-%d')].append(e.get('summary', 'Untitled'))
            if en:
                week_minutes += int((_naive_dt(en) - s_dt).total_seconds() // 60)

    all_week_days = [
        (week_start + datetime.timedelta(days=i)).strftime('%A %Y-%m-%d')
        for i in range(7)
    ]
    free_days = [d for d in all_week_days if d not in week_by_day]

    # ── Month stats ──────────────────────────────────────────────────────────
    month_by_day = defaultdict(int)
    month_minutes = 0
    for e in month_events:
        s  = e['start'].get('dateTime')
        en = e['end'].get('dateTime')
        if s:
            month_by_day[_naive_dt(s).strftime('%A %Y-%m-%d')] += 1
            if en:
                month_minutes += int((_naive_dt(en) - _naive_dt(s)).total_seconds() // 60)

    busiest_month = max(month_by_day, key=month_by_day.get) if month_by_day else "N/A"
    wh, wm = divmod(week_minutes, 60)
    mh, mm = divmod(month_minutes, 60)

    # ── Today ────────────────────────────────────────────────────────────────
    today_lines = [_fmt_event(e) for e in today_events] or ["  No meetings today."]

    week_detail = "\n".join(
        f"  {day}: {', '.join(titles)}" for day, titles in sorted(week_by_day.items())
    ) or "  No meetings this week."

    return (
        f"Calendar Insights — answering: \"{question}\"\n\n"
        f"TODAY ({now.strftime('%A %d %b')}):\n" + "\n".join(today_lines) + "\n\n"
        f"THIS WEEK:\n"
        f"  Free days        : {', '.join(free_days) if free_days else 'None — fully booked'}\n"
        f"  Total meeting time: {wh}h {wm}m\n"
        f"  Total events     : {len(week_events)}\n"
        f"  Events by day    :\n{week_detail}\n\n"
        f"THIS MONTH:\n"
        f"  Busiest day      : {busiest_month} ({month_by_day.get(busiest_month, 0)} meetings)\n"
        f"  Total events     : {len(month_events)}\n"
        f"  Total meeting time: {mh}h {mm}m"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 9 — Search events by keyword
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def search_events(keyword: str, days: int = 30) -> str:
    """
    Search for calendar events by keyword across upcoming days.
    Uses Google Calendar's full-text search on event titles, descriptions,
    and attendee names.
    Args:
        keyword: Word or phrase to search for (e.g. 'standup', 'raj', 'sprint')
        days   : How many days ahead to search (default 30, max 90)
    Returns:
        Matching events with date and time.
    """
    service = get_calendar_service()
    now = datetime.datetime.now()
    end = now + datetime.timedelta(days=min(days, 90))

    result = service.events().list(
        calendarId='primary',
        q=keyword,
        timeMin=now.isoformat() + TZ_OFFSET,
        timeMax=end.isoformat() + TZ_OFFSET,
        singleEvents=True,
        orderBy='startTime',
        maxResults=20
    ).execute()

    events = result.get('items', [])
    if not events:
        return f"No upcoming events matching '{keyword}' in the next {days} days."

    lines = [f"Events matching '{keyword}':"]
    for e in events:
        s = e['start'].get('dateTime', e['start'].get('date', ''))
        date_str = s[:10]
        time_str = _naive_dt(s).strftime('%H:%M') if 'T' in s else 'All day'
        lines.append(f"  • {e.get('summary', 'Untitled')} — {date_str} at {time_str}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 10 — Find next available slot (scans forward across days)
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def get_next_available_slot(duration_minutes: int, preferred_date: str = None) -> str:
    """
    Find the next available time slot for a meeting of a given duration.
    Scans forward day by day (starting from preferred_date or today)
    within working hours (9am–6pm), skipping weekends.
    Args:
        duration_minutes: Required meeting length in minutes
        preferred_date  : YYYY-MM-DD to start searching from (default: today)
    Returns:
        The first available date and time window found.
    """
    service = get_calendar_service()
    now = datetime.datetime.now()

    if preferred_date:
        start = datetime.datetime.strptime(preferred_date, "%Y-%m-%d")
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    for offset in range(14):  # scan up to 2 weeks ahead
        day = start + datetime.timedelta(days=offset)
        if day.weekday() >= 5:  # skip Saturday (5) and Sunday (6)
            continue

        date_str = day.strftime("%Y-%m-%d")
        events = _fetch_events_for_date(service, date_str)

        work_start = day.replace(hour=9, minute=0, second=0, microsecond=0)
        work_end   = day.replace(hour=18, minute=0, second=0, microsecond=0)

        # If scanning today, don't look at slots already passed
        if day.date() == now.date():
            work_start = max(work_start, now.replace(second=0, microsecond=0))

        busy = sorted([
            (_naive_dt(e['start']['dateTime']), _naive_dt(e['end']['dateTime']))
            for e in events if e['start'].get('dateTime')
        ])

        cursor = work_start
        for b_start, b_end in busy:
            if cursor + datetime.timedelta(minutes=duration_minutes) <= b_start:
                return (
                    f"Next available slot for {duration_minutes} min:\n"
                    f"  {day.strftime('%A, %d %b %Y')} — "
                    f"{cursor.strftime('%H:%M')} to "
                    f"{(cursor + datetime.timedelta(minutes=duration_minutes)).strftime('%H:%M')}"
                )
            cursor = max(cursor, b_end)

        if cursor + datetime.timedelta(minutes=duration_minutes) <= work_end:
            return (
                f"Next available slot for {duration_minutes} min:\n"
                f"  {day.strftime('%A, %d %b %Y')} — "
                f"{cursor.strftime('%H:%M')} to "
                f"{(cursor + datetime.timedelta(minutes=duration_minutes)).strftime('%H:%M')}"
            )

    return f"No available slot for {duration_minutes} min found in the next 14 working days."


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 11 — Create recurring event
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def create_recurring_event(
    title: str,
    start_date: str,
    start_time: str,
    duration_minutes: int,
    frequency: str,
    occurrences: int,
    attendee_email: str = None
) -> str:
    """
    Create a recurring meeting in Google Calendar (e.g. weekly standup, daily check-in).
    Args:
        title           : Meeting title
        start_date      : First occurrence date YYYY-MM-DD
        start_time      : HH:MM (24-hour)
        duration_minutes: Duration in minutes
        frequency       : 'DAILY', 'WEEKLY', or 'MONTHLY'
        occurrences     : Total number of occurrences (e.g. 10 for 10 weeks)
        attendee_email  : Optional attendee email
    Returns:
        Confirmation with event link.
    """
    freq = frequency.upper()
    if freq not in ('DAILY', 'WEEKLY', 'MONTHLY'):
        return "Invalid frequency. Use DAILY, WEEKLY, or MONTHLY."
    if occurrences < 1 or occurrences > 52:
        return "occurrences must be between 1 and 52."

    now = datetime.datetime.now()
    start_dt = datetime.datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")
    if start_dt < now:
        return f"PAST_DATE_ERROR: {start_date} {start_time} has already passed."

    end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)

    event_body = {
        'summary': title,
        'start': {'dateTime': start_dt.isoformat() + TZ_OFFSET, 'timeZone': TZ_NAME},
        'end':   {'dateTime': end_dt.isoformat()   + TZ_OFFSET, 'timeZone': TZ_NAME},
        'recurrence': [f'RRULE:FREQ={freq};COUNT={occurrences}'],
    }
    if attendee_email:
        event_body['attendees'] = [{'email': attendee_email}]

    service = get_calendar_service()
    created = service.events().insert(calendarId='primary', body=event_body).execute()
    return (
        f"Recurring event created: '{title}' — {freq.lower()} for {occurrences} occurrences "
        f"starting {start_date} at {start_time}. Link: {created.get('htmlLink')}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 12 — Add description/agenda to an existing event
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def add_event_description(title: str, date: str, description: str) -> str:
    """
    Add or update the description (agenda, notes, links) on an existing event.
    Useful for attaching a meeting agenda after the event is created.
    Args:
        title      : Event title (partial match ok)
        date       : YYYY-MM-DD
        description: Text to set as the event description / agenda
    Returns:
        Confirmation or error message.
    """
    service = get_calendar_service()
    events = _fetch_events_for_date(service, date)
    matches = [e for e in events if title.lower() in e.get('summary', '').lower()]

    if not matches:
        return f"No event matching '{title}' on {date}."
    if len(matches) > 1:
        names = ", ".join(f"'{e.get('summary')}'" for e in matches)
        return f"Multiple matches: {names}. Be more specific."

    event = matches[0]
    event['description'] = description

    updated = service.events().update(
        calendarId='primary', eventId=event['id'], body=event
    ).execute()
    return (
        f"Description added to '{updated.get('summary')}' on {date}. "
        f"Link: {updated.get('htmlLink')}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 13 — Check attendee availability before inviting
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def check_attendee_availability(
    email: str,
    date: str,
    start_time: str,
    duration_minutes: int
) -> str:
    """
    Check whether an attendee is free for a proposed time slot using
    Google Calendar's Freebusy API. Call this before inviting someone
    to avoid scheduling conflicts on their end.
    Args:
        email           : Attendee's Google account email
        date            : YYYY-MM-DD
        start_time      : HH:MM (24-hour)
        duration_minutes: Duration in minutes
    Returns:
        Whether the attendee is free or busy during that window.
    """
    service = get_calendar_service()
    start_dt = datetime.datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
    end_dt   = start_dt + datetime.timedelta(minutes=duration_minutes)

    body = {
        "timeMin": start_dt.isoformat() + TZ_OFFSET,
        "timeMax": end_dt.isoformat()   + TZ_OFFSET,
        "items":   [{"id": email}]
    }
    result = service.freebusy().query(body=body).execute()
    busy_times = result.get('calendars', {}).get(email, {}).get('busy', [])

    if not busy_times:
        return (
            f"{email} is FREE on {date} from {start_time} "
            f"to {end_dt.strftime('%H:%M')} ({duration_minutes} min). Safe to invite."
        )

    conflicts = []
    for b in busy_times:
        b_start = _naive_dt(b['start']).strftime('%H:%M')
        b_end   = _naive_dt(b['end']).strftime('%H:%M')
        conflicts.append(f"{b_start}–{b_end}")

    return (
        f"{email} is BUSY on {date} during: {', '.join(conflicts)}. "
        f"Consider a different time or check find_free_slots."
    )
