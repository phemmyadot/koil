// Date helpers. index.html had a UTC-offset-trick todayIsoDate(); trades.html/position.html had
// a separate manual padStart-concat version. Same output in practice, but two implementations
// of the same thing is exactly the drift risk this rewrite exists to remove -- one impl here.

export function todayIsoDate(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function daysBetween(isoA: string, isoB: string): number {
  const a = new Date(isoA + "T00:00:00Z").getTime();
  const b = new Date(isoB + "T00:00:00Z").getTime();
  return Math.round((b - a) / 86_400_000);
}

export function addDaysIso(iso: string, days: number): string {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

// closedAt is a UTC timestamp -- comparing raw date substrings would compare UTC calendar
// dates, not the user's actual local day, so both sides are parsed into Date and compared via
// local getFullYear/getMonth/getDate instead.
export function isClosedToday(closedAt: string): boolean {
  const closedLocal = new Date(closedAt);
  const now = new Date();
  return (
    closedLocal.getFullYear() === now.getFullYear() &&
    closedLocal.getMonth() === now.getMonth() &&
    closedLocal.getDate() === now.getDate()
  );
}
