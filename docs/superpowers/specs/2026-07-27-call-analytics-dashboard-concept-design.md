# Call Analytics Dashboard Concept Design

**Date:** 2026-07-27

**Status:** Approved

**Scope:** A desktop visual concept for Presvo's authenticated dashboard

## Purpose

Create a polished dashboard mockup that takes inspiration from the reference
screen's airy analytics layout, modular card grid, and calm information density
without copying its branding or visual identity.

The concept must present detailed call analytics using only facts Presvo
already stores or returns. It is a visual direction, not an authorization to
add new dashboard behavior or backend analytics.

## Product Context

Presvo is a France-first AI receptionist for professionals and small
businesses. The dashboard helps an owner confirm that the receptionist is
available, review recent inbound calls, identify follow-up work, and understand
plan usage.

The mockup represents an active account with a configured receptionist named
**Lea** and a 60-minute starter-plan allocation.

## Visual Direction

Use Presvo's approved **Quiet Confidence** direction:

- a warm, paper-like workspace;
- a deep ink command rail;
- tinted white analytics surfaces;
- restrained cobalt interaction accents;
- a limited amber brand detail;
- semantic green, amber, and red status colors;
- moderate radii, subtle borders, and controlled shadows;
- precise sans-serif typography and generous spacing.

The result should feel calm, premium, operational, and information-rich. Avoid
glassmorphism, neon glow, excessive gradients, oversized decoration, and
generic futuristic AI imagery.

## Canvas and Frame

- Render one high-fidelity desktop dashboard at 1440 by 900 pixels in a 16:10
  landscape composition.
- Keep the persistent command rail on the left.
- Use the existing Presvo destinations: Overview, Calls, Lea, Billing, and
  Account.
- Title the workspace **Call analytics**.
- Show the fixed context **Last 7 days · Europe/Paris** as a label rather than
  implying a new date-filter control.
- Do not add global search, export, customization, or other unsupported
  controls.

## Screen Composition

### Receptionist status

Place a compact live-status surface near the top:

- label: **Live**;
- title: **Lea is answering calls**;
- supporting text: **Forwarded calls can be answered by Lea.**

Show the illustrative assigned Presvo number **+33 1 87 65 42 10** as
contextual account information.

### Headline metrics

Present a unified horizontal metric band with internally consistent
illustrative values:

| Metric | Value |
| --- | ---: |
| Calls today | 4 |
| Last 7 days | 19 |
| Change from previous 7 days | +5 |
| Follow-up flagged | 3 |
| Average duration | 2m 07s |
| Minutes remaining | 21 of 60 |

The change must be expressed as a count, matching Presvo's current dashboard
contract, rather than as an invented percentage.

### Call activity

Use the largest analytical surface for a seven-day call-volume chart derived
from call timestamps. The daily values must total 19:

| Day | Calls |
| --- | ---: |
| Monday | 2 |
| Tuesday | 3 |
| Wednesday | 1 |
| Thursday | 4 |
| Friday | 2 |
| Saturday | 3 |
| Sunday | 4 |

Use a restrained cobalt line with a very light area fill. Do not add forecasts
or targets.

### Caller intent

Show a compact breakdown derived from the existing `caller_intent` summary
field:

| Intent | Calls |
| --- | ---: |
| Appointment request | 7 |
| Pricing enquiry | 4 |
| Existing-client request | 3 |
| Opening hours or location | 3 |
| Other | 2 |

These values must total 19. Treat the labels as illustrative caller-summary
examples, not a fixed Presvo taxonomy.

### Sentiment

Show a small distribution based on Presvo's existing `sentiment` summary
field:

| Sentiment | Calls |
| --- | ---: |
| Positive | 11 |
| Neutral | 6 |
| Negative | 2 |

These values must total 19. The visualization must remain secondary to call
activity and should not imply a quality score.

### Plan usage

Show the starter-plan allowance using current billing facts:

- allocated: **60 min**;
- used: **39 min**;
- remaining: **21 min**;
- subscription: **Active**.

Use a simple progress visualization without forecasting exhaustion.

### Recent calls

Include a compact ledger of five recent French calls. Each row shows only
fields Presvo already exposes:

- caller number;
- caller intent;
- follow-up status;
- duration;
- recording availability;
- start time.

Use the following illustrative rows:

| Caller | Intent | Follow-up | Duration | Recording | Started |
| --- | --- | --- | ---: | --- | --- |
| +33 6 12 45 78 90 | Appointment request | Needed | 3m 12s | Available | 10:42 |
| +33 7 81 22 34 56 | Pricing enquiry | Not needed | 1m 48s | Available | 09:17 |
| +33 1 44 58 21 03 | Existing-client request | Needed | 2m 36s | Available | Yesterday, 17:26 |
| +33 6 52 14 09 87 | Opening hours or location | Not needed | 0m 52s | Unavailable | Yesterday, 15:04 |
| +33 7 40 63 18 25 | Other | Not needed | 2m 07s | Available | Yesterday, 11:38 |

## Data Boundaries

The concept may visualize or aggregate:

- call timestamps and counts;
- current and previous seven-day call totals;
- call duration and average duration;
- caller intent;
- sentiment;
- follow-up requirement;
- recording availability;
- charged or remaining minutes;
- receptionist and subscription state.

The concept must not include:

- revenue or recovered-revenue claims;
- conversion, booking, or resolution rates;
- forecasts or predicted demand;
- customer satisfaction scores;
- staff or equipment utilization;
- business-performance targets;
- inferred opportunity value;
- any control or analytics capability that suggests Presvo currently supports
  it when it does not.

## Image-Generation Constraints

- Use the supplied dental dashboard only as a reference for composition,
  density, and visual mood.
- Preserve Presvo's own navigation, terminology, and visual identity.
- Render all specified labels legibly and avoid adding unrelated text.
- Do not include third-party logos, trademarks, watermarks, dental imagery, or
  clinical terminology.
- Produce a realistic, shippable product-UI mockup rather than concept art.

## Acceptance Criteria

The final mockup succeeds when:

1. it is immediately recognizable as a Presvo call-analytics dashboard;
2. it carries the reference screen's calm, modular information density without
   looking copied;
3. every analytical claim maps to a current Presvo field or a direct aggregate
   of current fields;
4. the headline numbers and breakdowns are internally consistent;
5. the dashboard remains readable at a glance and visually prioritizes call
   activity over secondary details.
