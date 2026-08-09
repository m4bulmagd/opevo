\set ON_ERROR_STOP on

INSERT INTO calls (
  id,
  user_id,
  caller_number,
  status,
  failure_code,
  state_changed_at,
  started_at,
  ended_at,
  duration_seconds,
  minutes_charged,
  summary_text,
  summary_data,
  summary_transcript_max_sequence,
  created_at,
  updated_at
)
SELECT
  fixture.id,
  owner.id,
  fixture.caller_number,
  fixture.status,
  fixture.failure_code,
  fixture.started_at,
  fixture.started_at,
  fixture.ended_at,
  fixture.duration_seconds,
  fixture.minutes_charged,
  fixture.summary_text,
  fixture.summary_data,
  fixture.summary_transcript_max_sequence,
  fixture.started_at,
  fixture.ended_at
FROM users AS owner
CROSS JOIN (
  VALUES
    (
      '11111111-1111-4111-8111-111111111111'::uuid,
      '+33612345678',
      'completed',
      NULL,
      '2026-07-28 13:00:00+00'::timestamptz,
      '2026-07-28 13:03:03+00'::timestamptz,
      183,
      4,
      'Sophie Bernard requested a Thursday afternoon appointment at the Paris showroom.',
      '{"caller_intent":"Book a showroom appointment","action_items":["Confirm Thursday at 15:00 by SMS","Send the showroom address"],"sentiment":"positive","follow_up_required":true}'::json,
      4
    ),
    (
      '22222222-2222-4222-8222-222222222222'::uuid,
      '+33142123456',
      'completed',
      NULL,
      '2026-07-27 08:30:00+00'::timestamptz,
      '2026-07-27 08:31:12+00'::timestamptz,
      72,
      2,
      'The caller checked opening hours and accessibility information.',
      '{"caller_intent":"Check opening hours","action_items":[],"sentiment":"neutral","follow_up_required":false}'::json,
      2
    ),
    (
      '33333333-3333-4333-8333-333333333333'::uuid,
      '+33755010203',
      'failed',
      'caller_disconnected',
      '2026-07-26 15:15:00+00'::timestamptz,
      '2026-07-26 15:15:18+00'::timestamptz,
      18,
      1,
      NULL,
      NULL,
      NULL
    ),
    (
      '44444444-4444-4444-8444-444444444444'::uuid,
      NULL,
      'completed',
      NULL,
      '2026-07-24 10:00:00+00'::timestamptz,
      '2026-07-24 10:02:05+00'::timestamptz,
      125,
      3,
      'A private caller asked for details about delivery within Île-de-France.',
      '{"caller_intent":"Ask about delivery","action_items":["Share the delivery guide"],"sentiment":"neutral","follow_up_required":true}'::json,
      2
    )
) AS fixture (
  id,
  caller_number,
  status,
  failure_code,
  started_at,
  ended_at,
  duration_seconds,
  minutes_charged,
  summary_text,
  summary_data,
  summary_transcript_max_sequence
)
WHERE owner.external_user_id = 'local_opevo_user'
ON CONFLICT (id) DO NOTHING;

INSERT INTO call_messages (
  id,
  call_id,
  speaker,
  text,
  sequence_number,
  created_at,
  updated_at
)
VALUES
  (
    'aaaaaaaa-1111-4111-8111-111111111111'::uuid,
    '11111111-1111-4111-8111-111111111111'::uuid,
    'ASSISTANT',
    'Bonjour, vous êtes bien chez Atelier Marceau. Comment puis-je vous aider ?',
    1,
    '2026-07-28 13:00:08+00'::timestamptz,
    '2026-07-28 13:00:08+00'::timestamptz
  ),
  (
    'aaaaaaaa-2222-4222-8222-222222222222'::uuid,
    '11111111-1111-4111-8111-111111111111'::uuid,
    'CALLER',
    'Je voudrais prendre rendez-vous pour découvrir votre showroom à Paris.',
    2,
    '2026-07-28 13:00:19+00'::timestamptz,
    '2026-07-28 13:00:19+00'::timestamptz
  ),
  (
    'aaaaaaaa-3333-4333-8333-333333333333'::uuid,
    '11111111-1111-4111-8111-111111111111'::uuid,
    'ASSISTANT',
    'Préférez-vous jeudi matin ou jeudi après-midi ?',
    3,
    '2026-07-28 13:00:31+00'::timestamptz,
    '2026-07-28 13:00:31+00'::timestamptz
  ),
  (
    'aaaaaaaa-4444-4444-8444-444444444444'::uuid,
    '11111111-1111-4111-8111-111111111111'::uuid,
    'CALLER',
    'Jeudi à quinze heures serait parfait, merci.',
    4,
    '2026-07-28 13:00:45+00'::timestamptz,
    '2026-07-28 13:00:45+00'::timestamptz
  ),
  (
    'bbbbbbbb-1111-4111-8111-111111111111'::uuid,
    '22222222-2222-4222-8222-222222222222'::uuid,
    'CALLER',
    'Quels sont vos horaires cette semaine ?',
    1,
    '2026-07-27 08:30:09+00'::timestamptz,
    '2026-07-27 08:30:09+00'::timestamptz
  ),
  (
    'bbbbbbbb-2222-4222-8222-222222222222'::uuid,
    '22222222-2222-4222-8222-222222222222'::uuid,
    'ASSISTANT',
    'Le showroom est ouvert du lundi au vendredi, de neuf heures à dix-huit heures.',
    2,
    '2026-07-27 08:30:20+00'::timestamptz,
    '2026-07-27 08:30:20+00'::timestamptz
  )
ON CONFLICT (id) DO NOTHING;
