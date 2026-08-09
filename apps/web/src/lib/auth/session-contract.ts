export type ServerSessionState = Readonly<{
  isAuthenticated: boolean;
  getToken: () => Promise<string | null>;
}>;
