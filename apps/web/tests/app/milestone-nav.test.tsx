import type { AnchorHTMLAttributes } from "react";

import type { LinkProps } from "next/link";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MilestoneNav } from "@/app/(activation)/activate/_components/milestone-nav";
import {
  type ActivationMilestoneId,
  getMilestoneState,
  selectMilestone,
} from "@/app/(activation)/activate/_components/stage-router";
import type { ActivationSnapshot, ActivationStage } from "@/lib/types/activation";

type MockLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  href: string;
  prefetch?: LinkProps["prefetch"];
};

vi.mock("next/link", () => ({
  default: ({ children, href, prefetch: _prefetch, ...props }: MockLinkProps) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

function buildSnapshot(overrides: Partial<ActivationSnapshot> = {}): ActivationSnapshot {
  return {
    workflow_version: 1,
    stage: "profile_required",
    completed_milestones: [],
    next_action: "complete_profile",
    blockers: ["profile_not_confirmed"],
    warnings: [],
    profile: {
      owner_name: null,
      business_name: null,
      business_type: null,
      public_description: null,
      timezone: null,
      business_hours: null,
      existing_phone_e164: null,
      confirmed_carrier: null,
      receptionist_name: null,
      faqs: [],
      special_instructions: null,
      escalation_notes: null,
      detected_carrier: null,
      detected_number_type: null,
      carrier_lookup_status: null,
      carrier_looked_up_at: null,
      content_revision: 0,
      routing_revision: 0,
    },
    profile_constraints: {
      name_max_length: 100,
      business_type_max_length: 100,
      public_description_max_length: 1_000,
      faq_max_items: 20,
      faq_question_max_length: 200,
      faq_answer_max_length: 1_000,
      special_instructions_max_length: 2_000,
      escalation_notes_max_length: 2_000,
      max_intervals_per_day: 2,
      phone_country: "FR",
    },
    activation: {
      profile_confirmed_at: null,
      provisioning_consented_at: null,
      verification_window_started_at: null,
      verification_window_expires_at: null,
      verification_status: "not_started",
      forwarding_verified_at: null,
      go_live_approved_at: null,
      activated_at: null,
      last_failure_code: null,
    },
    billing: {
      eligible: false,
      plan_tier: null,
      subscription_status: null,
      allocated_minutes: 0,
      minutes_remaining: 0,
      current_period_start: null,
      current_period_end: null,
    },
    number: {
      assigned_e164: null,
      country_code: null,
      provider_ready: false,
      provisioning_status: null,
      can_retry: false,
    },
    forwarding: null,
    runtime_readiness: {
      stage: "subscription_required",
      can_provision_number: false,
      can_activate: false,
      should_enable_phone: false,
      can_route: false,
      blockers: ["profile_not_confirmed"],
      warnings: [],
      policy_version: "runtime-v2",
    },
    evaluated_at: "2026-07-17T10:00:00Z",
    ...overrides,
  };
}

describe("canonical activation milestone selection", () => {
  it.each<[ActivationStage, ActivationMilestoneId]>([
    ["profile_required", "business"],
    ["payment_required", "number"],
    ["provisioning_consent_required", "number"],
    ["provisioning", "number"],
    ["provisioning_failed", "number"],
    ["forwarding_required", "forwarding"],
    ["verification_window_open", "launch"],
    ["ready_to_activate", "launch"],
    ["activating", "launch"],
    ["runtime_paused", "launch"],
  ])("maps %s to %s", (stage, expected) => {
    expect(selectMilestone(buildSnapshot({ stage }), null)).toBe(expected);
  });

  it("moves profile-required owners to receptionist only after the business subset is populated", () => {
    const snapshot = buildSnapshot({
      profile: {
        ...buildSnapshot().profile,
        owner_name: "Maya Laurent",
        business_name: "Atelier Laurent",
        business_type: "Interior design studio",
        timezone: "Europe/Paris",
        business_hours: {
          monday: { closed: false, intervals: [{ start: "09:00", end: "18:00" }] },
          tuesday: { closed: true, intervals: [] },
          wednesday: { closed: true, intervals: [] },
          thursday: { closed: true, intervals: [] },
          friday: { closed: true, intervals: [] },
          saturday: { closed: true, intervals: [] },
          sunday: { closed: true, intervals: [] },
        },
        existing_phone_e164: "+33184801234",
        confirmed_carrier: "orange",
      },
    });

    expect(selectMilestone(snapshot, null)).toBe("receptionist");
    expect(getMilestoneState(snapshot, "business")).toBe("completed");
    expect(getMilestoneState(snapshot, "receptionist")).toBe("current");
  });

  it("allows a query to reopen a completed step but never unlocks a future one", () => {
    const snapshot = buildSnapshot({
      stage: "forwarding_required",
      completed_milestones: ["profile_confirmed", "payment_eligible", "provisioning_consented", "number_provisioned"],
    });

    expect(selectMilestone(snapshot, "business")).toBe("business");
    expect(selectMilestone(snapshot, "number")).toBe("number");
    expect(selectMilestone(snapshot, "launch")).toBe("forwarding");
    expect(selectMilestone(snapshot, "unknown")).toBe("forwarding");
  });
});

describe("milestone navigator", () => {
  it("renders five ordered, labelled milestones with canonical link states", () => {
    const snapshot = buildSnapshot({
      stage: "forwarding_required",
      completed_milestones: ["profile_confirmed", "payment_eligible", "provisioning_consented", "number_provisioned"],
    });

    render(<MilestoneNav snapshot={snapshot} selectedMilestone="forwarding" />);

    const navigation = screen.getByRole("navigation", { name: /Activation progress/i });
    expect(navigation.querySelectorAll("ol > li")).toHaveLength(5);
    expect(screen.getByRole("link", { name: /Business.*Complete/i })).toHaveAttribute(
      "href",
      "/activate?milestone=business",
    );
    expect(screen.getByRole("link", { name: /Number.*Complete/i })).toHaveAttribute(
      "href",
      "/activate?milestone=number",
    );
    expect(screen.getByText("Forwarding").closest("[aria-current='step']")).not.toBeNull();
    expect(screen.queryByRole("link", { name: /Launch.*Locked/i })).not.toBeInTheDocument();
    expect(screen.getByText("Launch").closest("li")).toHaveTextContent(/Locked/i);
  });
});
