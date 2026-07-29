const CANONICAL_ACCOUNT_TIMEZONE = "Europe/Paris";

function isValidTimezone(value: string): boolean {
  try {
    new Intl.DateTimeFormat("en", { timeZone: value });
    return true;
  } catch {
    return false;
  }
}

export function getAllowedAccountTimezones(savedTimezone: string | null | undefined): string[] {
  if (savedTimezone && savedTimezone !== CANONICAL_ACCOUNT_TIMEZONE && isValidTimezone(savedTimezone)) {
    return [savedTimezone, CANONICAL_ACCOUNT_TIMEZONE];
  }

  return [CANONICAL_ACCOUNT_TIMEZONE];
}

export function isAccountTimezoneAllowed(requestedTimezone: string, savedTimezone: string | null | undefined): boolean {
  return getAllowedAccountTimezones(savedTimezone).includes(requestedTimezone);
}
