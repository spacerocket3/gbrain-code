create function public.get_order_summary()
returns table(order_count bigint)
language sql
stable
as $$
  select count(*) from public.orders;
$$;
