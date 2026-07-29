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
      email="maya@presvo.test"
      initialProfile={initialProfile}
      nameMaxLength={60}
      readOnly={false}
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
    expect(screen.getByLabelText("Email")).toHaveValue("maya@presvo.test");
    expect(screen.getByLabelText("Personal phone")).toHaveValue("+33612345678");
    expect(screen.getByLabelText("Business name")).toHaveValue("Atelier Martin");
    expect(screen.getByLabelText("Timezone")).toHaveValue("Europe/Paris");
  });

  it("presents email as read-only identity data and names unavailable development email", () => {
    const { rerender } = renderProfile();

    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "email");
    expect(screen.getByLabelText("Email")).toHaveAttribute("readonly");

    rerender(<AccountProfileForm email={null} initialProfile={initialProfile} nameMaxLength={60} readOnly={false} />);

    expect(screen.getByLabelText("Email")).toHaveValue("");
    expect(screen.getByText("Email unavailable in local development")).toBeInTheDocument();
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

  it("shows its existing unsaved changes status after a supported edit", () => {
    renderProfile();

    fireEvent.change(screen.getByLabelText("Business name"), { target: { value: "Atelier Presvo" } });

    expect(screen.getByRole("status", { name: "Unsaved changes" })).toBeInTheDocument();
  });

  it("restores the confirmed baseline when the draft is discarded", () => {
    renderProfile();
    const businessName = screen.getByLabelText("Business name");
    fireEvent.change(businessName, { target: { value: "Atelier Presvo" } });

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

  it("locks save and discard while the profile request is pending", async () => {
    const pending = deferred<{ status: "success"; message: string; profile: typeof initialProfile }>();
    saveAccountProfileMock.mockReturnValueOnce(pending.promise);
    renderProfile();
    fireEvent.change(screen.getByLabelText("Business name"), { target: { value: "Atelier Presvo" } });

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(screen.getByRole("button", { name: "Saving changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();

    await act(async () => {
      pending.resolve({
        status: "success",
        message: "Profile saved.",
        profile: { ...initialProfile, business_name: "Atelier Presvo" },
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

  it("disables editable controls and omits the save bar in read-only mode", () => {
    renderProfile({ readOnly: true });

    expect(screen.getByLabelText("Full name")).toBeDisabled();
    expect(screen.getByLabelText("Personal phone")).toBeDisabled();
    expect(screen.getByLabelText("Business name")).toBeDisabled();
    expect(screen.getByLabelText("Timezone")).toBeDisabled();
    expect(screen.queryByRole("status", { name: "Unsaved changes" })).not.toBeInTheDocument();
  });
});
