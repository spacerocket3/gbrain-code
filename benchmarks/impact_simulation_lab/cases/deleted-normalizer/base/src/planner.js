import { normalizeContract } from "./normalizer.js";

export function buildPlannerRow(value) {
  return { contractCode: normalizeContract(value) };
}
