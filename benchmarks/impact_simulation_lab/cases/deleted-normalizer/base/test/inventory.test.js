import assert from "node:assert/strict";
import test from "node:test";

import { inventoryFloor } from "../src/decoys.js";

test("prevents negative inventory", () => {
  assert.equal(inventoryFloor(-4), 0);
});
