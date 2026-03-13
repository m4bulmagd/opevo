def build_system_prompt(
    *,
    agent_name: str,
    owner_name: str,
    system_prompt: str,
    knowledge_base: str,
) -> str:
    return (
        f"You are {agent_name}, an AI assistant representing {owner_name}.\n"
        "You must identify yourself as an AI assistant and disclose that the call may be recorded.\n"
        "Keep responses concise and helpful.\n"
        f"{system_prompt}\n"
        f"<knowledge_base>\n{knowledge_base}\n</knowledge_base>"
    )
