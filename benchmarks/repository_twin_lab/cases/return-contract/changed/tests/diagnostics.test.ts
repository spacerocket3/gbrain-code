import { buildDiagnostics } from "../src/diagnostics";

test("returns blocked warnings", () => {
  expect(buildDiagnostics([{ id: "a", blocked: true }]).warnings).toHaveLength(1);
});
