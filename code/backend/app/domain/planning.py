def summarize_plan_from_chat(user_message: str) -> str:
    normalized = " ".join(user_message.split())
    if not normalized:
        return "Clarify your goal and constraints to receive a recommended plan."

    # The product surfaces recommendations only, never internal workflow details.
    return (
        "Recommended plan: clarify goal, confirm budget and timeline, then execute "
        "with milestone checkpoints and final result confirmation."
    )

