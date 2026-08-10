export function isProtectedPath(pathname: string): boolean {
  return (
    pathname === "/activate" ||
    pathname.startsWith("/activate/") ||
    pathname === "/dashboard" ||
    pathname.startsWith("/dashboard/")
  );
}
