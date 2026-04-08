from agent import create_scheduler_agent
from tools import get_calendar_service
from model_selector import get_ranked_models

# Task 1 Step 5: Authenticate and print Calendar ID to confirm setup works
service = get_calendar_service()
calendar = service.calendars().get(calendarId='primary').execute()
print(f"[Auth OK] Connected to calendar: {calendar['id']}\n")

# Get ranked model list — agent tries them in order, auto-falls back on 429
ranked_models = get_ranked_models(verbose=True)
print(f"[Model]   Primary: {ranked_models[0]}  |  Fallbacks: {ranked_models[1:]}\n")

agent = create_scheduler_agent(model_names=ranked_models)
print("AI Scheduler Ready! (type 'exit' to quit, 'reset' to start a new conversation)\n")

while True:
    user_input = input("> ").strip()
    if not user_input:
        continue
    if user_input.lower() == "exit":
        break
    if user_input.lower() == "reset":
        agent.reset()
        print("Conversation cleared.\n")
        continue
    result = agent(user_input)
    print(f"\nAssistant: {result}\n")
