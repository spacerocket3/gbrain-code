import assert from "node:assert/strict";
import test from "node:test";

import { reportLabel } from "../src/decoys.js";

test("formats report labels", () => {
  assert.equal(reportLabel("Daily"), "Report: Daily");
});
