import { buildDiagnostics } from "./diagnostics";

export function warningCount(rows: Array<{ id: string; blocked: boolean }>) {
  return buildDiagnostics(rows).warnings.length;
}
