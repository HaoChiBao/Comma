import type { SidebarTrace } from "./types";

export const FULL_GRAPH_URL = "https://web-production-def1f.up.railway.app/";
declare const COMMA_WEB_URL: string;

type OfficeBrowserHost = {
  context?: {
    requirements?: { isSetSupported(name: string, version: string): boolean };
    ui?: { openBrowserWindow(url: string): void };
  };
};

/** Build a shareable deep link using only the cell's visible posted record. */
export function fullGraphUrl(trace: SidebarTrace, baseUrl = FULL_GRAPH_URL): string {
  const target = typeof COMMA_WEB_URL !== "undefined" && COMMA_WEB_URL ? COMMA_WEB_URL : baseUrl;
  const url = new URL(target);
  if (trace.case_id) url.searchParams.set("focus", trace.case_id);
  if (trace.full_graph_node_id) url.searchParams.set("selected", trace.full_graph_node_id);
  url.searchParams.set("actor_id", trace.actor_id);
  url.searchParams.set("as_of", trace.as_of);
  url.searchParams.set("mode", "audit");
  url.searchParams.set("view_mode", "web");
  return url.toString();
}

/** Prefer Office.js external-browser navigation, which is not subject to WebView popup blocking. */
export function openGraphUrl(
  url: string,
  office: OfficeBrowserHost | undefined,
  openWindow: (url: string, target: string) => unknown,
): boolean {
  try {
    if (
      office?.context?.requirements?.isSetSupported("OpenBrowserWindowApi", "1.1") &&
      office.context.ui?.openBrowserWindow
    ) {
      office.context.ui.openBrowserWindow(url);
      return true;
    }
  } catch {
    // Older or partially implemented hosts use the browser fallback below.
  }
  return openWindow(url, "_blank") != null;
}
