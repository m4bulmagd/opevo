import { describe, expect, it } from "vitest";

import {
  buildCallHistoryHref,
  callHistoryPageCount,
  parseCallHistoryNavigation,
} from "@/lib/calls/call-history-navigation";

describe("call history navigation", () => {
  it("trims q and derives a one-based page offset", () => {
    expect(
      parseCallHistoryNavigation({
        q: [" opening hours ", "ignored"],
        page: "2",
      }),
    ).toEqual({
      query: "opening hours",
      page: 2,
      limit: 20,
      offset: 20,
    });
  });

  it.each([
    undefined,
    "",
    "0",
    "-2",
    "1.5",
    "abc",
    "9007199254740992",
  ])("resolves malformed page %s to page one", (page) => {
    expect(parseCallHistoryNavigation({ page }).page).toBe(1);
    expect(parseCallHistoryNavigation({ page }).offset).toBe(0);
  });

  it("calculates at least one page", () => {
    expect(callHistoryPageCount(0)).toBe(1);
    expect(callHistoryPageCount(40)).toBe(2);
    expect(callHistoryPageCount(41)).toBe(3);
    expect(callHistoryPageCount(21, 10)).toBe(3);
  });

  it("builds canonical links that retain q and omit page one", () => {
    expect(buildCallHistoryHref("opening hours", 2)).toBe("/dashboard/calls?q=opening+hours&page=2");
    expect(buildCallHistoryHref("opening hours", 1)).toBe("/dashboard/calls?q=opening+hours");
    expect(buildCallHistoryHref("", 1)).toBe("/dashboard/calls");
  });
});
