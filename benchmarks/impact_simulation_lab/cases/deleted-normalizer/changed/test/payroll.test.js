import assert from "node:assert/strict";
import test from "node:test";

import { payrollHours } from "../src/decoys.js";

test("adds payroll hours", () => {
  assert.equal(payrollHours([4, 6, 8]), 18);
});
