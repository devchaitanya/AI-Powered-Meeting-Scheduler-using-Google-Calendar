from dotenv import load_dotenv
load_dotenv()

import time
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage, AIMessage
from model_selector import parse_retry_delay, is_daily_exhausted
from tools import (
    get_current_datetime,
    get_calendar_events,
    create_event,
    list_upcoming_events,
    find_free_slots,
    cancel_event,
    reschedule_event,
    analyse_booking_patterns,
    query_calendar_insights,
    search_events,
    get_next_available_slot,
    create_recurring_event,
    add_event_description,
    check_attendee_availability,
)

# ── Kept short intentionally: tool descriptions come from docstrings via bind_tools
SYSTEM_PROMPT = """You are an AI meeting scheduler connected to Google Calendar.

RULES (follow strictly):
1. Any relative date (today/tomorrow/this Friday/next Monday) → call get_current_datetime FIRST.
2. Calendar questions (free days, meeting hours, busiest day, today's agenda) → call query_calendar_insights.
3. Show schedule / upcoming agenda → call list_upcoming_events.
4. "Find me a time" / next free slot → call get_next_available_slot.
5. Recurring meetings (every Monday, weekly standup) → call create_recurring_event.
6. Inviting an attendee → call check_attendee_availability first, then create_event.
7. create_event returns CONFLICT → alternatives are already included. Present them. No more tool calls.
8. create_event returns PAST_DATE_ERROR → tell the user, stop immediately. No more tool calls.
9. Keyword search → call search_events.
10. Never ask for info a tool can provide. Only ask when the meeting title is completely absent."""

ALL_TOOLS = [
    get_current_datetime,
    get_calendar_events,
    list_upcoming_events,
    create_event,
    create_recurring_event,
    find_free_slots,
    get_next_available_slot,
    cancel_event,
    reschedule_event,
    add_event_description,
    check_attendee_availability,
    analyse_booking_patterns,
    query_calendar_insights,
    search_events,
]

# ── Token-saving constants ────────────────────────────────────────────────────
MAX_ITERATIONS       = 5    # Task 3+4 now needs ≤3 LLM calls; hard cap at 5
MAX_HISTORY_MESSAGES = 12   # keep last 6 turns (human+AI pairs) — older turns dropped
TOOL_RESULT_MAX_CHARS = 600 # truncate long tool results stored in history


def _extract_text(response) -> str:
    """Handle Gemini returning content as a list of blocks or a plain string."""
    content = response.content
    if isinstance(content, list):
        parts = [
            block["text"] if isinstance(block, dict) and "text" in block else str(block)
            for block in content
        ]
        return "\n".join(p for p in parts if p).strip()
    return (content or "").strip()


def _trim_tool_content(content: str) -> str:
    """Truncate long tool results so they don't bloat the token count."""
    if len(content) <= TOOL_RESULT_MAX_CHARS:
        return content
    return content[:TOOL_RESULT_MAX_CHARS] + f"\n…[{len(content)-TOOL_RESULT_MAX_CHARS} chars truncated]"


def _trim_history(history: list) -> list:
    """
    Keep SystemMessage(s) + last MAX_HISTORY_MESSAGES non-system messages.
    This prevents unbounded token growth across multi-turn conversations.
    """
    system_msgs = [m for m in history if isinstance(m, SystemMessage)]
    other_msgs  = [m for m in history if not isinstance(m, SystemMessage)]
    if len(other_msgs) > MAX_HISTORY_MESSAGES:
        other_msgs = other_msgs[-MAX_HISTORY_MESSAGES:]
    return system_msgs + other_msgs


def _make_llm(model_name: str):
    return ChatGoogleGenerativeAI(
        model=model_name,
        temperature=0,
        convert_system_message_to_human=True,
        max_retries=1,   # we handle fallback ourselves; don't let langchain retry on 429
        timeout=30,
    )


def create_scheduler_agent(model_names: list[str] = None):
    if not model_names:
        model_names = ["gemini-1.5-flash"]

    # Start with the primary (best) model; fall back on quota errors
    current_model_idx = 0
    llm = _make_llm(model_names[current_model_idx])
    llm_with_tools = llm.bind_tools(ALL_TOOLS)
    tool_map = {t.name: t for t in ALL_TOOLS}

    history = [SystemMessage(content=SYSTEM_PROMPT)]

    def _switch_model(error_str: str) -> bool:
        """
        Try the next model in the fallback list. Returns True if we can retry.
        - Per-minute limit (short delay) → wait and retry same model.
        - Daily limit or long delay      → switch to next model immediately.
        """
        nonlocal current_model_idx, llm, llm_with_tools

        delay = parse_retry_delay(error_str)
        daily = is_daily_exhausted(error_str)

        if not daily and 0 < delay <= 30:
            print(f"\n  [Rate limit] Waiting {delay}s then retrying {model_names[current_model_idx]}…")
            time.sleep(delay + 1)
            return True  # retry same model after wait

        # Daily exhausted or delay > 30s — switch to next model
        if current_model_idx + 1 < len(model_names):
            current_model_idx += 1
            next_model = model_names[current_model_idx]
            print(f"\n  [Quota exhausted] Switching to {next_model}…")
            llm = _make_llm(next_model)
            llm_with_tools = llm.bind_tools(ALL_TOOLS)
            return True

        return False  # all models exhausted

    def run_agent(user_input: str):
        history.append(HumanMessage(content=user_input))

        for _ in range(MAX_ITERATIONS):
            try:
                response = llm_with_tools.invoke(_trim_history(history))
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    if _switch_model(err):
                        continue  # retry with next model or after wait
                    # All models exhausted — show clean message
                    delay = parse_retry_delay(err)
                    if delay:
                        return (
                            f"All Gemini models have hit their free-tier quota for today.\n"
                            f"Shortest retry window: {delay}s (per-minute limit).\n"
                            f"Daily quota resets at midnight Pacific Time.\n"
                            f"Tip: Visit https://ai.dev/rate-limit to check your usage."
                        )
                    return (
                        "All Gemini models have hit their free-tier daily quota.\n"
                        "Daily quota resets at midnight Pacific Time.\n"
                        "Tip: Visit https://ai.dev/rate-limit to check your usage."
                    )
                return f"Error: {e}"

            history.append(response)

            if not response.tool_calls:
                text = _extract_text(response)
                return text if text else "Done."

            for tool_call in response.tool_calls:
                name = tool_call["name"]
                args = tool_call["args"]
                print(f"\n  [Tool] {name}({args})")
                try:
                    result = str(tool_map[name].invoke(args))
                except Exception as e:
                    result = f"Tool error: {e}"
                print(f"  [↳ ] {result[:200]}{'…' if len(result) > 200 else ''}")
                history.append(
                    ToolMessage(
                        content=_trim_tool_content(result),
                        tool_call_id=tool_call["id"]
                    )
                )

        return "Could not complete in the allowed steps. Please rephrase."

    def reset():
        history.clear()
        history.append(SystemMessage(content=SYSTEM_PROMPT))

    run_agent.reset = reset
    return run_agent
