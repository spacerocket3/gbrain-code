export function buildDiagnostics(rows: Array<{ id: string; blocked: boolean }>) {
  return {
    warnings: rows.filter((row) => row.blocked),
  };
}
