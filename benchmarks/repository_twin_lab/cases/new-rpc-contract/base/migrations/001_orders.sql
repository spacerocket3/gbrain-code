create table public.orders(id uuid primary key);

create function public.get_orders()
returns setof public.orders
language sql
stable
as $$
  select * from public.orders;
$$;
