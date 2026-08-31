import { loadOrders } from "../src/orders";

test("loads orders", () => {
  expect(loadOrders(client)).toBeDefined();
});
