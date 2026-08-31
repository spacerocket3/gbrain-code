export function loadOrders(client: { rpc: (name: string) => unknown }) {
  return client.rpc("get_orders");
}
