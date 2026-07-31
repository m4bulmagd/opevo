from collections.abc import Callable
from dataclasses import dataclass

from presvo_contracts import (
    CallCompletionAcknowledgement,
    CallCompletionRequest,
    CustomerCallDispatch,
    ForwardingVerificationDispatch,
    TranscriptAppendAcknowledgement,
    TranscriptAppendRequest,
    VerificationCompletionAcknowledgement,
    VerificationCompletionRequest,
    create_contract,
    parse_contract,
    parse_dispatch,
)
from presvo_contracts.realtime import (
    AgentSessionEndedEvent,
    CallFinalizedEvent,
    CallStartedEvent,
    TranscriptObservedEvent,
    parse_realtime_event,
)
from presvo_contracts.versioning import VersionedContract


@dataclass(frozen=True)
class ContractCase:
    fixture_name: str
    producer: VersionedContract
    parser: Callable[[object], VersionedContract]


CONTRACT_CASES = (
    ContractCase(
        "customer_call_dispatch",
        create_contract(
            CustomerCallDispatch,
            job_type="customer_call",
            call_id="11111111-1111-4111-8111-111111111111",
            user_id="22222222-2222-4222-8222-222222222222",
            agent_config_id="33333333-3333-4333-8333-333333333333",
            agent_identity="agent-call-11111111-1111-4111-8111-111111111111",
            agent_name="Fixture Agent",
            owner_name="Fixture Owner",
            owner_context="Fixture customer-success context.",
            system_prompt="Help the fixture caller clearly.",
            knowledge_base="Fixture knowledge base.",
            pipeline_mode="stt_llm_tts",
            minutes_remaining=12,
            allowed_duration_seconds=300,
            dispatch_token="fixture-dispatch-token",
        ),
        parse_dispatch,
    ),
    ContractCase(
        "forwarding_verification_dispatch",
        create_contract(
            ForwardingVerificationDispatch,
            job_type="forwarding_verification",
            verification_session_id="44444444-4444-4444-8444-444444444444",
            user_id="22222222-2222-4222-8222-222222222222",
            agent_identity="agent-verification-44444444-4444-4444-8444-444444444444",
            completion_token="fixture-completion-token",
            message="Forwarding test successful. Return to Presvo to go live.",
            tts_provider="speechmatics",
        ),
        parse_dispatch,
    ),
    ContractCase(
        "transcript_append_request",
        create_contract(
            TranscriptAppendRequest,
            segment={"sequence_number": 1, "speaker": "CALLER", "text": "Fixture caller text."},
        ),
        lambda value: parse_contract(TranscriptAppendRequest, value),
    ),
    ContractCase(
        "transcript_append_acknowledgement",
        create_contract(TranscriptAppendAcknowledgement, status="stored", sequence_number=1),
        lambda value: parse_contract(TranscriptAppendAcknowledgement, value),
    ),
    ContractCase(
        "call_completion_request",
        create_contract(
            CallCompletionRequest,
            duration_seconds=42,
            transcript=[{"sequence_number": 1, "speaker": "CALLER", "text": "Fixture caller text."}],
        ),
        lambda value: parse_contract(CallCompletionRequest, value),
    ),
    ContractCase(
        "call_completion_acknowledgement",
        create_contract(
            CallCompletionAcknowledgement, status="accepted", queued=True, job_id="fixture-job-001"
        ),
        lambda value: parse_contract(CallCompletionAcknowledgement, value),
    ),
    ContractCase(
        "verification_completion_request",
        create_contract(VerificationCompletionRequest),
        lambda value: parse_contract(VerificationCompletionRequest, value),
    ),
    ContractCase(
        "verification_completion_acknowledgement",
        create_contract(
            VerificationCompletionAcknowledgement,
            status="verified",
            session_id="44444444-4444-4444-8444-444444444444",
        ),
        lambda value: parse_contract(VerificationCompletionAcknowledgement, value),
    ),
    ContractCase(
        "transcript_observed_event",
        create_contract(
            TranscriptObservedEvent,
            type="transcript_observed",
            user_id="22222222-2222-4222-8222-222222222222",
            call_id="11111111-1111-4111-8111-111111111111",
            sequence_number=1,
            speaker="CALLER",
            text="Fixture caller text.",
        ),
        parse_realtime_event,
    ),
    ContractCase(
        "call_started_event",
        create_contract(
            CallStartedEvent,
            type="call_started",
            user_id="22222222-2222-4222-8222-222222222222",
            call_id="11111111-1111-4111-8111-111111111111",
            room_name="fixture-room-001",
        ),
        parse_realtime_event,
    ),
    ContractCase(
        "agent_session_ended_event",
        create_contract(
            AgentSessionEndedEvent,
            type="agent_session_ended",
            user_id="22222222-2222-4222-8222-222222222222",
            call_id="11111111-1111-4111-8111-111111111111",
            duration_seconds=42,
        ),
        parse_realtime_event,
    ),
    ContractCase(
        "call_finalized_event",
        create_contract(
            CallFinalizedEvent,
            type="call_finalized",
            user_id="22222222-2222-4222-8222-222222222222",
            call_id="11111111-1111-4111-8111-111111111111",
            minutes_charged=1,
            summary_text="Fixture call summary.",
        ),
        parse_realtime_event,
    ),
)
