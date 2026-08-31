export const roundAccount = value => Math.round(value * 100) / 100;
export const documentTitle = value => value.trim();
export const inventoryFloor = value => Math.max(0, value);
export const payrollHours = values => values.reduce((total, value) => total + value, 0);
export const reportLabel = value => `Report: ${value}`;
