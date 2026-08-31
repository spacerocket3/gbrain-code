import { saveOrder } from "../src/service";

test("normalizes an order", () => {
  expect(saveOrder(" order-1 ")).toBe("order-1");
});
