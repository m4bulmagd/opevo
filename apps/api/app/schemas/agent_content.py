from typing import Annotated

from pydantic import StringConstraints


AGENT_NAME_MAX_LENGTH = 80
OWNER_NAME_MAX_LENGTH = 255
OWNER_CONTEXT_MAX_LENGTH = 4_000
SYSTEM_PROMPT_MAX_LENGTH = 8_000
KNOWLEDGE_BASE_MAX_LENGTH = 32_000

AgentName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=AGENT_NAME_MAX_LENGTH,
    ),
]
OwnerName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=OWNER_NAME_MAX_LENGTH,
    ),
]
OwnerContext = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        max_length=OWNER_CONTEXT_MAX_LENGTH,
    ),
]
SystemPrompt = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        max_length=SYSTEM_PROMPT_MAX_LENGTH,
    ),
]
KnowledgeBase = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        max_length=KNOWLEDGE_BASE_MAX_LENGTH,
    ),
]
