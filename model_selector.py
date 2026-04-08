"""
Lists available Gemini models using the new google.genai SDK,
cross-references known free-tier rate limits, and returns a ranked
fallback list so the agent can switch models automatically on 429.
"""

import os
import re
from google import genai

RATE_LIMITS = {
    "gemini-2.0-flash-lite":          {"rpm": 30, "tpm": 1_000_000, "tool_use": True},
    "gemini-2.0-flash":               {"rpm": 15, "tpm": 1_000_000, "tool_use": True},
    "gemini-1.5-flash":               {"rpm": 15, "tpm": 1_000_000, "tool_use": True},
    "gemini-1.5-flash-8b":            {"rpm": 15, "tpm": 1_000_000, "tool_use": True},
    "gemini-2.5-flash-preview-04-17": {"rpm": 10, "tpm":   500_000, "tool_use": True},
    "gemini-2.5-flash":               {"rpm": 10, "tpm":   500_000, "tool_use": True},
    "gemini-2.5-pro":                 {"rpm":  5, "tpm":   500_000, "tool_use": True},
    "gemini-1.5-pro":                 {"rpm":  2, "tpm":    32_000, "tool_use": True},
}

FALLBACK_ORDER = ["gemini-1.5-flash", "gemini-1.5-flash-8b", "gemini-2.0-flash"]


def get_ranked_models(verbose: bool = True) -> list[str]:
    """
    Returns an ordered list of model names (best first by RPM).
    The agent tries them in order and falls back on 429.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError("GOOGLE_API_KEY not set in environment.")

    try:
        client = genai.Client(api_key=api_key)
        api_models = list(client.models.list())
    except Exception as e:
        print(f"[Model Selector] Cannot reach API: {e}. Using fallback list.")
        return FALLBACK_ORDER

    available = []
    for m in api_models:
        # New SDK: model name is already short (no "models/" prefix needed)
        name = m.name.replace("models/", "")
        if name in RATE_LIMITS and RATE_LIMITS[name]["tool_use"]:
            available.append((name, RATE_LIMITS[name]))

    if not available:
        print("[Model Selector] No known models found. Using fallback list.")
        return FALLBACK_ORDER

    available.sort(key=lambda x: (x[1]["rpm"], len(x[0])), reverse=True)
    ranked = [name for name, _ in available]

    if verbose:
        col = 38
        print("\n┌─ Available Gemini Models ──────────────────────────────────────┐")
        print(f"│  {'Model':<{col}} {'RPM':>4}  {'TPM':>12}  │")
        print(f"│  {'─'*col}  {'─'*4}  {'─'*12}  │")
        for i, (name, limits) in enumerate(available):
            tag = " ◄ primary" if i == 0 else (f" ◄ fallback {i}" if i < 3 else "")
            print(f"│  {name:<{col}} {limits['rpm']:>4}  {limits['tpm']:>12,}{tag}")
        print("└────────────────────────────────────────────────────────────────┘\n")

    return ranked


def parse_retry_delay(error_str: str) -> int:
    """Extract retry-delay seconds from a 429 error string. Returns 0 if not found."""
    match = re.search(r"retryDelay.*?(\d+)s", error_str)
    return int(match.group(1)) if match else 0


def is_daily_exhausted(error_str: str) -> bool:
    return "PerDay" in error_str
