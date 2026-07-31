import { describe, expect, it } from "vitest";
import { addDaysIso, daysBetween, todayIsoDate } from "./dates";

describe("todayIsoDate", () => {
  it("returns a well-formed YYYY-MM-DD string", () => {
    expect(todayIsoDate()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe("daysBetween", () => {
  it("computes a positive day count forward", () => {
    expect(daysBetween("2026-07-01", "2026-07-15")).toBe(14);
  });
  it("computes a negative day count backward", () => {
    expect(daysBetween("2026-07-15", "2026-07-01")).toBe(-14);
  });
  it("returns 0 for the same date", () => {
    expect(daysBetween("2026-07-01", "2026-07-01")).toBe(0);
  });
  it("handles a month boundary correctly", () => {
    expect(daysBetween("2026-07-25", "2026-08-05")).toBe(11);
  });
});

describe("addDaysIso", () => {
  it("adds days within the same month", () => {
    expect(addDaysIso("2026-07-01", 5)).toBe("2026-07-06");
  });
  it("rolls over a month boundary", () => {
    expect(addDaysIso("2026-07-28", 5)).toBe("2026-08-02");
  });
  it("handles negative offsets", () => {
    expect(addDaysIso("2026-07-05", -10)).toBe("2026-06-25");
  });
  it("round-trips with daysBetween", () => {
    const start = "2026-07-15";
    const end = addDaysIso(start, 30);
    expect(daysBetween(start, end)).toBe(30);
  });
});
