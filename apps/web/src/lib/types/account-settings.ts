export type AccountProfileValues = Readonly<{
  owner_name: string;
  business_name: string;
  existing_phone_e164: string;
  timezone: string;
}>;

export type AccountIdentity = Readonly<{
  email: string | null;
  securityMode: "clerk" | "unavailable";
}>;
