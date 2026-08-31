export function buildDiagnostics(rows: Array<{ id: string; blocked: boolean }>) {
  const warnings = rows.filter((row) => row.blocked);
  return {
    warnings,
    blockedIds: new Set(warnings.map((row) => row.id)),
  };
}
