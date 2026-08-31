import assert from "node:assert/strict";
import test from "node:test";

import { roundAccount } from "../src/decoys.js";

test("rounds account totals", () => {
  assert.equal(roundAccount(12.345), 12.35);
});
