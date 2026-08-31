import assert from "node:assert/strict";
import test from "node:test";

import { buildPlannerRow } from "../src/planner.js";

test("normalizes planner contract codes", () => {
  assert.deepEqual(buildPlannerRow(" ab-12 "), { contractCode: "AB-12" });
});
