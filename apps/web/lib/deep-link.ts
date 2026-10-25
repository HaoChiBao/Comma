export type GraphDeepLink = {
  focus: string | null;
  selected: string | null;
  actorId: string | null;
  asOf: string | null;
  mode: string | null;
  viewMode: "web" | "atom" | null;
};

export function parseGraphDeepLink(search: string): GraphDeepLink {
  const params = new URLSearchParams(search);
  const viewMode = params.get("view_mode");
  return {
    focus: params.get("focus"),
    selected: params.get("selected"),
    actorId: params.get("actor_id"),
    asOf: params.get("as_of"),
    mode: params.get("mode"),
    viewMode: viewMode === "web" || viewMode === "atom" ? viewMode : null,
  };
}
