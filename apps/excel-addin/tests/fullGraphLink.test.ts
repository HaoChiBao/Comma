import { describe, expect, it } from "vitest";
import { fullGraphUrl, openGraphUrl } from "../src/taskpane/fullGraphLink";
import type { SidebarTrace } from "../src/taskpane/types";

const trace = {
  actor_id: "actor-decagon-finance",
  as_of: "2025-09-30T23:59:00+00:00",
  case_id: "CASE 001/close",
  full_graph_node_id: "artifact:JE-900:v1",
} as SidebarTrace;

describe("full graph deep link", () => {
  it("encodes the focused case and visible cell record with its snapshot context", () => {
    const url = new URL(fullGraphUrl(trace));
    expect(url.origin + url.pathname).toBe("https://web-production-def1f.up.railway.app/");
    expect(url.searchParams.get("focus")).toBe("CASE 001/close");
    expect(url.searchParams.get("selected")).toBe("artifact:JE-900:v1");
    expect(url.searchParams.get("actor_id")).toBe("actor-decagon-finance");
    expect(url.searchParams.get("as_of")).toBe("2025-09-30T23:59:00+00:00");
    expect(url.searchParams.get("mode")).toBe("audit");
    expect(url.searchParams.get("view_mode")).toBe("web");
  });

  it("falls back to case scope when the cell has no visible own record", () => {
    const url = new URL(fullGraphUrl({ ...trace, full_graph_node_id: null }));
    expect(url.searchParams.get("focus")).toBe("CASE 001/close");
    expect(url.searchParams.has("selected")).toBe(false);
  });

  it("opens the general graph without leaking source identity when there is no case mapping", () => {
    const url = new URL(fullGraphUrl({ ...trace, case_id: null, full_graph_node_id: null }));
    expect(url.searchParams.has("focus")).toBe(false);
    expect(url.searchParams.has("selected")).toBe(false);
  });

  it("uses the Office browser API when Excel supports it", () => {
    const opened: string[] = [];
    const office = {
      context: {
        requirements: { isSetSupported: (name: string, version: string) => name === "OpenBrowserWindowApi" && version === "1.1" },
        ui: { openBrowserWindow: (url: string) => opened.push(url) },
      },
    };
    const fallback = () => { throw new Error("popup fallback should not run"); };
    expect(openGraphUrl("https://comma.test", office, fallback)).toBe(true);
    expect(opened).toEqual(["https://comma.test"]);
  });

  it("uses the popup fallback when the Office browser API is unsupported", () => {
    const opened: string[] = [];
    const office = { context: { requirements: { isSetSupported: () => false }, ui: { openBrowserWindow: () => {} } } };
    expect(openGraphUrl("https://comma.test", office, (url, target) => { opened.push(`${url}:${target}`); return {}; })).toBe(true);
    expect(opened).toEqual(["https://comma.test:_blank"]);
  });
});
