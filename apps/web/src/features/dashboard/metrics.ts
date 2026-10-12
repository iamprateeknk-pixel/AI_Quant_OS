function nonzeroDecimal(value: string): boolean | null {
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE][+-]?\d+)?$/.exec(value);
  if (!match) return null;
  return `${match[2]}${match[3] ?? ""}`.split("").some(digit => digit !== "0");
}

export function countOpenPositions(value: unknown): number | null {
  if (!Array.isArray(value)) return null;
  let count = 0;
  for (const item of value) {
    if (typeof item !== "object" || item === null || !("net_quantity" in item) || typeof item.net_quantity !== "string") return null;
    const open = nonzeroDecimal(item.net_quantity);
    if (open === null) return null;
    if (open) count += 1;
  }
  return count;
}
