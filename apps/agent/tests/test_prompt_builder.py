from agent.prompt_builder import build_system_prompt


def test_prompt_builder_wraps_knowledge_base() -> None:
    prompt = build_system_prompt(
        agent_name="Ava",
        owner_name="Sam",
        system_prompt="Be helpful.",
        knowledge_base="Hours: 9-5",
    )

    assert "<knowledge_base>" in prompt


def test_prompt_builder_includes_system_prompt() -> None:
    prompt = build_system_prompt(
        agent_name="Ava",
        owner_name="Sam",
        system_prompt="Be helpful.",
        knowledge_base="Hours: 9-5",
    )

    assert "Be helpful." in prompt
    assert "OWNER INSTRUCTIONS" in prompt


def test_prompt_builder_omits_owner_instructions_when_system_prompt_empty() -> None:
    prompt = build_system_prompt(
        agent_name="Ava",
        owner_name="Sam",
        system_prompt="",
        knowledge_base="Hours: 9-5",
    )

    assert "OWNER INSTRUCTIONS" not in prompt


def test_prompt_builder_keeps_required_disclosure() -> None:
    prompt = build_system_prompt(
        agent_name="Ava",
        owner_name="Sam",
        system_prompt="Be helpful.",
        knowledge_base="",
    )

    assert "AI assistant" in prompt
    assert "recorded" in prompt

