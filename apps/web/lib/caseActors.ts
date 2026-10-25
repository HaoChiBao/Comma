import { api, type ActorRow, type CaseRow } from "@/lib/api";

export async function actorsForCase(caseId: string): Promise<ActorRow[]> {
  const row = await api<CaseRow>(`/api/v1/cases/${caseId}`);
  const people = await api<ActorRow[]>("/api/v1/actors");
  return people.filter((person) => person.organization_id === row.organization_id);
}

export function accountantId(actors: ActorRow[]): string {
  return actors.find((person) => person.role === "accountant")?.id || actors[0]?.id || "";
}

export function auditorId(actors: ActorRow[]): string {
  return actors.find((person) => person.role === "auditor")?.id || actors[0]?.id || "";
}
