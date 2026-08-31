import assert from "node:assert/strict";
import test from "node:test";

import { documentTitle } from "../src/decoys.js";

test("normalizes document titles", () => {
  assert.equal(documentTitle(" Contract "), "Contract");
});
