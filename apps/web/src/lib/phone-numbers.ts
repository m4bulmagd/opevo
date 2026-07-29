export function normalizeFrenchNumber(value: string): string | null {
  const compact = value.trim().replace(/[\s().-]/g, "");
  if (/^0[1-9]\d{8}$/.test(compact)) return `+33${compact.slice(1)}`;
  if (/^\+33[1-9]\d{8}$/.test(compact)) return compact;
  if (/^0033[1-9]\d{8}$/.test(compact)) return `+${compact.slice(2)}`;
  return null;
}

export function formatFrenchNumber(value: string): string {
  const normalized = normalizeFrenchNumber(value);
  const local = normalized ? `0${normalized.slice(3)}` : value.replace(/\D/g, "").slice(0, 10);
  return local.replace(/(\d{2})(?=\d)/g, "$1 ").trim();
}
