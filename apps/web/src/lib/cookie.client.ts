// Client-side cookie utilities.
// These functions manage cookies in the browser only.
// Server actions handle cookie updates on the server side.

type ClientCookieStore = {
  set: (options: { name: string; value: string; expires?: number | Date; path?: string }) => Promise<void>;
  get: (name: string) => Promise<{ name: string; value: string } | null>;
  delete: (options: { name: string; path?: string }) => Promise<void>;
};

function getCookieStore() {
  return (window as Window & { cookieStore?: ClientCookieStore }).cookieStore;
}

export function setClientCookie(key: string, value: string, days = 7) {
  const expires = new Date(Date.now() + days * 864e5).toUTCString();
  const cookieStore = getCookieStore();

  if (cookieStore) {
    void cookieStore.set({
      name: key,
      value,
      expires: new Date(expires),
      path: "/",
    });
    return;
  }

  // biome-ignore lint/suspicious/noDocumentCookie: Cookie Store API is not available in every supported browser yet.
  document.cookie = `${key}=${value}; expires=${expires}; path=/`;
}

export function getClientCookie(key: string) {
  const cookieStore = getCookieStore();

  if (cookieStore) {
    return cookieStore.get(key).then((cookie) => cookie?.value);
  }

  return document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${key}=`))
    ?.split("=")[1];
}

export function deleteClientCookie(key: string) {
  const cookieStore = getCookieStore();

  if (cookieStore) {
    void cookieStore.delete({ name: key, path: "/" });
    return;
  }

  // biome-ignore lint/suspicious/noDocumentCookie: Cookie Store API is not available in every supported browser yet.
  document.cookie = `${key}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/`;
}
