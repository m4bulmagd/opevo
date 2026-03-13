from agent.prompt_builder import build_system_prompt


def test_prompt_builder_wraps_knowledge_base() -> None:
    prompt = build_system_prompt(
        agent_name="Ava",
        owner_name="Sam",
        knowledge_base="Hours: 9-5",
    )

    assert "<knowledge_base>" in prompt


def test_prompt_builder_keeps_required_disclosure() -> None:
    prompt = build_system_prompt(
        agent_name="Ava",
        owner_name="Sam",
        knowledge_base="",
    )

    assert "AI assistant" in prompt
    assert "recorded" in prompt
