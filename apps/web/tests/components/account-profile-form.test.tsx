import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountProfileForm } from "@/components/account/account-profile-form";

const { saveAccountProfileMock } = vi.hoisted(() => ({
  saveAccountProfileMock: vi.fn(),
}));

vi.mock("@/app/(app)/dashboard/account/actions", () => ({
  saveAccountProfileAction: saveAccountProfileMock,
}));

const initialProfile = {
  owner_name: "Maya Martin",
  business_name: "Atelier Martin",
  existing_phone_e164: "+33612345678",
  timezone: "Europe/Paris",
};

function renderProfile(overrides: Partial<React.ComponentProps<typeof AccountProfileForm>> = {}) {
  return render(
    <AccountProfileForm
      email="maya@opevo.test"
      initialProfile={initialProfile}
      nameMaxLength={60}
      readOnly={false}
      securityMode="clerk"
      {...overrides}
    />,
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

describe("account profile form", () => {
  beforeEach(() => {
    saveAccountProfileMock.mockReset().mockResolvedValue({
      status: "success",
      message: "Profile saved.",
      profile: initialProfile,
    });
  });

  it("maps its initial profile and identity to the five labeled controls", () => {
    renderProfile();

    expect(screen.getByRole("region", { name: "Profile" })).toBeInTheDocument();
    expect(screen.getByLabelText("Full name")).toHaveValue("Maya Martin");
    expect(screen.getByLabelText("Email")).toHaveValue("maya@opevo.test");
    expect(screen.getByLabelText("Personal phone")).toHaveValue("+33612345678");
    expect(screen.getByLabelText("Business name")).toHaveValue("Atelier Martin");
    expect(screen.getByLabelText("Timezone")).toHaveValue("Europe/Paris");
  });

  it("presents email as read-only identity data and explains local-mode absence truthfully", () => {
    const { rerender } = renderProfile();

    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "email");
    expect(screen.getByLabelText("Email")).toHaveAttribute("readonly");

    rerender(
      <AccountProfileForm
        email={null}
        initialProfile={initialProfile}
        nameMaxLength={60}
        readOnly={false}
        securityMode="unavailable"
      />,
    );

    expect(screen.getByLabelText("Email")).toHaveValue("");
    expect(screen.getByLabelText("Email")).toHaveAccessibleDescription("Email unavailable in local development");
    expect(screen.getByText("Email unavailable in local development")).toBeInTheDocument();
  });

  it("describes a missing hosted Clerk email as temporarily unavailable", () => {
    renderProfile({ email: null, securityMode: "clerk" });

    expect(screen.getByLabelText("Email")).toHaveAccessibleDescription("Email is temporarily unavailable.");
    expect(screen.getByText("Email is temporarily unavailable.")).toBeInTheDocument();
    expect(screen.queryByText("Email unavailable in local development")).not.toBeInTheDocument();
  });

  it("gives editable identity and phone fields browser-appropriate input semantics", () => {
    renderProfile();

    expect(screen.getByLabelText("Full name")).toHaveAttribute("autocomplete", "name");
    expect(screen.getByLabelText("Business name")).toHaveAttribute("autocomplete", "organization");
    expect(screen.getByLabelText("Personal phone")).toHaveAttribute("type", "tel");
    expect(screen.getByLabelText("Personal phone")).toHaveAttribute("inputmode", "tel");
    expect(screen.getByLabelText("Personal phone")).toHaveAttribute("autocomplete", "tel");
    expect(
      screen.getByText("Changing this forwarding number may pause incoming calls until forwarding is verified again."),
    ).toBeInTheDocument();
  });

  it("keeps every Profile control at least 44px tall", () => {
    renderProfile();

    for (const name of ["Full name", "Email", "Personal phone", "Business name"]) {
      expect(screen.getByLabelText(name)).toHaveClass("min-h-11");
    }
    expect(screen.getByLabelText("Timezone").parentElement).toHaveClass("[&_select]:min-h-11");
  });

  it("shows its existing unsaved changes status after a supported edit", () => {
    renderProfile();

    fireEvent.change(screen.getByLabelText("Business name"), { target: { value: "Atelier Opevo" } });

    expect(screen.getByRole("status", { name: "Unsaved changes" })).toBeInTheDocument();
  });

  it("restores the confirmed baseline when the draft is discarded", () => {
    renderProfile();
    const businessName = screen.getByLabelText("Business name");
    fireEvent.change(businessName, { target: { value: "Atelier Opevo" } });

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));

    expect(businessName).toHaveValue("Atelier Martin");
    expect(screen.queryByRole("status", { name: "Unsaved changes" })).not.toBeInTheDocument();
  });

  it("focuses the first blank name and does not submit an invalid draft", () => {
    renderProfile();
    const fullName = screen.getByLabelText("Full name");
    fireEvent.change(fullName, { target: { value: "   " } });

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(fullName).toHaveFocus();
    expect(saveAccountProfileMock).not.toHaveBeenCalled();
  });

  it("focuses the phone field and does not submit an invalid French phone value", () => {
    renderProfile();
    const phone = screen.getByLabelText("Personal phone");
    fireEvent.change(phone, { target: { value: "not-a-french-number" } });

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(phone).toHaveFocus();
    expect(phone).toHaveAttribute("aria-describedby", "account-profile-phone-description account-profile-phone-error");
    expect(saveAccountProfileMock).not.toHaveBeenCalled();
  });

  it("focuses Personal phone before Business name when both rendered fields are invalid", () => {
    renderProfile();
    const phone = screen.getByLabelText("Personal phone");
    fireEvent.change(phone, { target: { value: "not-a-french-number" } });
    fireEvent.change(screen.getByLabelText("Business name"), { target: { value: "   " } });

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(phone).toHaveFocus();
    expect(saveAccountProfileMock).not.toHaveBeenCalled();
  });

  it("saves only the four supported profile fields with a normalized phone", async () => {
    renderProfile();
    fireEvent.change(screen.getByLabelText("Personal phone"), { target: { value: "06 98 76 54 32" } });

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(saveAccountProfileMock).toHaveBeenCalledWith({
        owner_name: "Maya Martin",
        business_name: "Atelier Martin",
        existing_phone_e164: "+33698765432",
        timezone: "Europe/Paris",
      });
    });
  });

  it("locks every editable control and prevents newer typing while the profile request is pending", async () => {
    const pending = deferred<{ status: "success"; message: string; profile: typeof initialProfile }>();
    saveAccountProfileMock.mockReturnValueOnce(pending.promise);
    renderProfile();
    const businessName = screen.getByLabelText("Business name");
    fireEvent.change(businessName, { target: { value: "Atelier Opevo" } });

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    for (const name of ["Full name", "Personal phone", "Business name", "Timezone"]) {
      expect(screen.getByLabelText(name)).toBeDisabled();
    }
    expect(screen.getByRole("button", { name: "Saving changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();

    businessName.focus();
    fireEvent.keyDown(businessName, { key: "x" });
    expect(businessName).not.toHaveFocus();
    expect(businessName).toHaveValue("Atelier Opevo");

    await act(async () => {
      pending.resolve({
        status: "success",
        message: "Profile saved.",
        profile: { ...initialProfile, business_name: "Atelier Opevo" },
      });
    });
  });

  it("uses the confirmed success profile as the next baseline and clears the dirty bar", async () => {
    saveAccountProfileMock.mockResolvedValueOnce({
      status: "success",
      message: "Profile saved.",
      profile: { ...initialProfile, business_name: "Atelier Confirmed" },
    });
    renderProfile();
    fireEvent.change(screen.getByLabelText("Business name"), { target: { value: "Atelier Draft" } });

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(screen.getByLabelText("Business name")).toHaveValue("Atelier Confirmed");
    });
    expect(screen.queryByRole("status", { name: "Unsaved changes" })).not.toBeInTheDocument();

    const savedStatus = screen.getByRole("status");
    expect(savedStatus).toHaveAttribute("aria-live", "polite");
    expect(savedStatus).toHaveTextContent("Profile saved.");

    fireEvent.change(screen.getByLabelText("Business name"), { target: { value: "Atelier next draft" } });
    expect(screen.queryByText("Profile saved.")).not.toBeInTheDocument();
  });

  it("removes a legacy timezone option after Europe/Paris is confirmed", async () => {
    const legacyProfile = { ...initialProfile, timezone: "Europe/London" };
    saveAccountProfileMock.mockResolvedValueOnce({
      status: "success",
      message: "Profile saved.",
      profile: { ...legacyProfile, timezone: "Europe/Paris" },
    });
    renderProfile({ initialProfile: legacyProfile });

    fireEvent.change(screen.getByLabelText("Timezone"), { target: { value: "Europe/Paris" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(screen.queryByRole("option", { name: "Europe/London" })).not.toBeInTheDocument());
    expect(screen.getByRole("option", { name: "Europe/Paris" })).toBeInTheDocument();
  });

  it("keeps the draft and allows retry after a save error", async () => {
    saveAccountProfileMock
      .mockResolvedValueOnce({ status: "error", code: "request_failed", message: "Try again shortly." })
      .mockResolvedValueOnce({
        status: "success",
        message: "Profile saved.",
        profile: { ...initialProfile, business_name: "Atelier Draft" },
      });
    renderProfile();
    fireEvent.change(screen.getByLabelText("Business name"), { target: { value: "Atelier Draft" } });

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(screen.getByText("Try again shortly.")).toBeInTheDocument());
    expect(screen.getByLabelText("Business name")).toHaveValue("Atelier Draft");
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(saveAccountProfileMock).toHaveBeenCalledTimes(2));
  });

  it("associates server-returned fields with errors and focuses the first affected rendered control", async () => {
    saveAccountProfileMock.mockResolvedValueOnce({
      status: "error",
      code: "invalid_input",
      message: "Review your profile details and try again.",
      fields: ["business_name", "existing_phone_e164"],
    });
    renderProfile();
    const phone = screen.getByLabelText("Personal phone");
    const businessName = screen.getByLabelText("Business name");
    fireEvent.change(businessName, { target: { value: "Atelier retained draft" } });

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(phone).toHaveFocus());
    expect(phone).toHaveAttribute("aria-invalid", "true");
    expect(businessName).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Full name")).toHaveAttribute("aria-invalid", "false");
    expect(phone.closest('[data-slot="field"]')).toHaveTextContent("Review this field and try again.");
    expect(businessName.closest('[data-slot="field"]')).toHaveTextContent("Review this field and try again.");
    expect(businessName).toHaveValue("Atelier retained draft");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  });

  it("disables editable controls and omits the save bar in read-only mode", () => {
    renderProfile({ readOnly: true });

    expect(screen.getByLabelText("Full name")).toBeDisabled();
    expect(screen.getByLabelText("Personal phone")).toBeDisabled();
    expect(screen.getByLabelText("Business name")).toBeDisabled();
    expect(screen.getByLabelText("Timezone")).toBeDisabled();
    expect(screen.queryByRole("status", { name: "Unsaved changes" })).not.toBeInTheDocument();
  });
});
