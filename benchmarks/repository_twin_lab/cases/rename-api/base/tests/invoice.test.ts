import { invoiceTotal } from "../src/invoice";

test("calculates an invoice", () => {
  expect(invoiceTotal(4)).toBe(5);
});
