import {
  ActionIcon,
  Badge,
  Group,
  Paper,
  Progress,
  ScrollArea,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Tooltip,
} from '@mantine/core';

import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';

import { BASE, Loading, OrderLink, PartLink, downloadCsv, money, num, pct, qty, usePluginData } from './shared';

/* ------------------------------------------------------------------ helpers */

function useWidget<T>(context: InvenTreePluginContext, name: string) {
  return usePluginData<T>(context, name === 'capacity' ? `${BASE}/dashboard/` : `${BASE}/dashboard/${name}/`);
}

function Frame({ children, onRefresh, loading }: { children: any; onRefresh?: () => void; loading?: boolean }) {
  return (
    <Stack gap={6} h='100%' style={{ position: 'relative' }}>
      {onRefresh && (
        <Tooltip label='Refresh (data is cached for 2 minutes)'>
          <ActionIcon
            size='xs'
            variant='subtle'
            loading={loading}
            onClick={onRefresh}
            style={{ position: 'absolute', top: 0, right: 0, zIndex: 2 }}
          >
            ↻
          </ActionIcon>
        </Tooltip>
      )}
      <ScrollArea h='100%' type='auto'>
        {children}
      </ScrollArea>
    </Stack>
  );
}

function Tile({ label, value, sub, color, onClick }: { label: string; value: any; sub?: any; color?: string; onClick?: () => void }) {
  return (
    <Paper withBorder p='xs' radius='md' onClick={onClick} style={onClick ? { cursor: 'pointer' } : undefined}>
      <Text size='xs' c='dimmed' lineClamp={1}>
        {label}
      </Text>
      <Text fw={700} size='lg' c={color}>
        {value}
      </Text>
      {sub && (
        <Text size='xs' c='dimmed' lineClamp={2}>
          {sub}
        </Text>
      )}
    </Paper>
  );
}

function Empty({ children }: { children: any }) {
  return (
    <Text size='sm' c='dimmed' p='xs'>
      {children}
    </Text>
  );
}

/** "IPN-0021 | WADA Logger - Selling Version" -> "WADA Logger - Selling Version" */
function shortName(name: string) {
  const parts = name.split(' | ');
  return parts.length > 1 ? parts.slice(1).join(' | ') : name;
}

function useCurrency(context: InvenTreePluginContext) {
  return String(context.globalSettings?.getSetting?.('INVENTREE_DEFAULT_CURRENCY') || 'EUR');
}

function refreshable(reload: () => void, context: InvenTreePluginContext, url: string, setData: (d: any) => void) {
  return () =>
    context.api
      .get(url, { params: { refresh: 1 } })
      .then((res) => setData(res.data))
      .catch(() => reload());
}

/* ------------------------------------------------------------------ 1. overview */

type Overview = {
  currency: string;
  products: { part: number; name: string; can_build: number; in_stock: string; unit_cost: string | null; limiting: { name: string }[] }[];
  inventory_value: string;
  inventory_estimated: string;
  active_builds: number;
  overdue_builds: number;
  units_in_progress: string;
  parts_to_buy: number;
  parts_to_build: number;
  purchase_estimate: string;
  open_orders: number;
  open_orders_value: string;
  overdue_orders: number;
  fees_counted: string;
  fees_uplift_percent: string | null;
};

function OverviewWidget({ context }: { context: InvenTreePluginContext }) {
  const url = `${BASE}/dashboard/overview/`;
  const { data, setData, error, loading, reload } = useWidget<Overview>(context, 'overview');
  if (!data) return <Loading loading={loading} error={error} />;
  const cur = data.currency;
  const go = (path: string) => () => context.navigate(path);

  return (
    <Frame onRefresh={refreshable(reload, context, url, setData)} loading={loading}>
      <Stack gap='xs'>
        <SimpleGrid cols={{ base: 2, sm: 4, lg: Math.min(data.products.length + 5, 7) }} spacing='xs'>
          {data.products.map((p) => (
            <Tile
              key={p.part}
              label={`Can build · ${shortName(p.name)}`}
              value={p.can_build}
              color={p.can_build === 0 ? 'red' : p.can_build < 5 ? 'orange' : 'green'}
              sub={`${qty(p.in_stock)} in stock${p.unit_cost ? ` · ${money(p.unit_cost, cur)}/unit` : ''}${p.limiting[0] ? ` · limited by ${shortName(p.limiting[0].name)}` : ''}`}
              onClick={go(`/part/${p.part}/`)}
            />
          ))}
          <Tile
            label='Inventory value (landed)'
            value={money(data.inventory_value, cur, 0)}
            sub={num(data.inventory_estimated) ? `${money(data.inventory_estimated, cur, 0)} built stock at BOM cost` : undefined}
            onClick={go('/stock/location/index/')}
          />
          <Tile
            label='Active build orders'
            value={data.active_builds}
            color={data.overdue_builds ? 'red' : undefined}
            sub={`${qty(data.units_in_progress)} units to finish${data.overdue_builds ? ` · ${data.overdue_builds} overdue` : ''}`}
            onClick={go('/manufacturing/index/')}
          />
          <Tile
            label='Parts to order'
            value={data.parts_to_buy}
            color={data.parts_to_buy ? 'orange' : 'green'}
            sub={`${!data.parts_to_buy ? 'nothing to buy' : num(data.purchase_estimate) ? `≈ ${money(data.purchase_estimate, cur, 0)} landed` : 'no price on file'}${data.parts_to_build ? ` · ${data.parts_to_build} to build` : ''}`}
          />
          <Tile
            label='Open purchase orders'
            value={data.open_orders}
            color={data.overdue_orders ? 'red' : undefined}
            sub={`${money(data.open_orders_value, cur, 0)}${data.overdue_orders ? ` · ${data.overdue_orders} overdue` : ''}`}
            onClick={go('/purchasing/index/')}
          />
          <Tile
            label='Fees paid (12 months)'
            value={money(data.fees_counted, cur, 0)}
            sub={data.fees_uplift_percent ? `${pct(data.fees_uplift_percent)} on top of goods` : undefined}
          />
        </SimpleGrid>
      </Stack>
    </Frame>
  );
}

/* ------------------------------------------------------------------ 2. capacity */

type CapRow = {
  part: number;
  name: string;
  units: string;
  in_stock: string;
  native_can_build: number | null;
  multi_level: number;
  limiting: { part: number; name: string; missing: string }[];
  unit_cost: string | null;
  unit_cost_complete: boolean;
  used_in_other_assemblies: boolean;
};

function CapacityWidget({ context }: { context: InvenTreePluginContext }) {
  const cur = useCurrency(context);
  const { data, setData, error, loading, reload } = useWidget<{ assemblies: CapRow[] }>(context, 'capacity');
  if (!data) return <Loading loading={loading} error={error} />;
  const rows = [...data.assemblies].sort(
    (a, b) => Number(a.used_in_other_assemblies) - Number(b.used_in_other_assemblies) || a.name.localeCompare(b.name),
  );
  if (!rows.length) return <Empty>No assemblies with a bill of materials.</Empty>;

  return (
    <Frame onRefresh={refreshable(reload, context, `${BASE}/dashboard/`, setData)} loading={loading}>
      <Table striped verticalSpacing={4} fz='sm' stickyHeader>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Assembly</Table.Th>
            <Table.Th>In stock</Table.Th>
            <Table.Th>
              <Tooltip label='Including building sub-assemblies from components. Small number = single-level figure.'>
                <span>Can build</span>
              </Tooltip>
            </Table.Th>
            <Table.Th>Bottleneck</Table.Th>
            <Table.Th>Unit cost</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => (
            <Table.Tr key={r.part}>
              <Table.Td>
                <Group gap={6} wrap='nowrap'>
                  <PartLink context={context} part={r.part} name={r.name} />
                  {r.used_in_other_assemblies && (
                    <Text size='xs' c='dimmed' style={{ whiteSpace: 'nowrap' }}>
                      (sub)
                    </Text>
                  )}
                </Group>
              </Table.Td>
              <Table.Td>{qty(r.in_stock, r.units)}</Table.Td>
              <Table.Td>
                <Group gap={4} wrap='nowrap'>
                  <Badge color={r.multi_level === 0 ? 'red' : r.multi_level < 5 ? 'orange' : 'green'}>{r.multi_level}</Badge>
                  {r.native_can_build !== null && r.native_can_build !== r.multi_level && (
                    <Text size='xs' c='dimmed'>
                      ({r.native_can_build})
                    </Text>
                  )}
                </Group>
              </Table.Td>
              <Table.Td>
                <Text size='xs' lineClamp={1}>
                  {r.limiting.length ? r.limiting.map((l) => l.name).join(', ') : '—'}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text size='sm' c={r.unit_cost_complete ? undefined : 'orange'}>
                  {money(r.unit_cost, cur)}
                  {r.unit_cost_complete ? '' : ' *'}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {rows.some((r) => !r.unit_cost_complete) && (
        <Text size='xs' c='dimmed' mt={4}>
          * some component prices missing
        </Text>
      )}
    </Frame>
  );
}

/* ------------------------------------------------------------------ 3. production */

type BuildRow = {
  build: number;
  reference: string;
  title: string;
  part: { part: number; name: string; units: string };
  quantity: string;
  completed: string;
  status: string;
  target_date: string | null;
  overdue: boolean;
  lines: number;
  lines_allocated: number;
  shortages: { part: number; name: string; units: string; missing: string }[];
};

function ProductionWidget({ context }: { context: InvenTreePluginContext }) {
  const { data, setData, error, loading, reload } = useWidget<{ builds: BuildRow[] }>(context, 'production');
  if (!data) return <Loading loading={loading} error={error} />;
  if (!data.builds.length) return <Empty>No active build orders.</Empty>;

  return (
    <Frame onRefresh={refreshable(reload, context, `${BASE}/dashboard/production/`, setData)} loading={loading}>
      <Stack gap='xs'>
        {data.builds.map((b) => {
          const done = num(b.completed) ?? 0;
          const total = num(b.quantity) || 1;
          const allocPct = b.lines ? (b.lines_allocated / b.lines) * 100 : 100;
          return (
            <Paper key={b.build} withBorder p='xs' radius='md'>
              <Group justify='space-between' wrap='nowrap' gap='xs'>
                <Group gap={6} wrap='nowrap'>
                  <Text
                    size='sm'
                    fw={600}
                    c={context.theme.primaryColor}
                    style={{ cursor: 'pointer' }}
                    onClick={() => context.navigate(`/manufacturing/build-order/${b.build}/`)}
                  >
                    {b.reference}
                  </Text>
                  <PartLink context={context} part={b.part.part} name={b.part.name} />
                </Group>
                <Group gap={4} wrap='nowrap'>
                  <Badge size='sm' variant='light'>
                    {b.status}
                  </Badge>
                  {b.target_date && (
                    <Badge size='sm' variant='light' color={b.overdue ? 'red' : 'gray'}>
                      {b.overdue ? 'overdue ' : 'due '}
                      {b.target_date}
                    </Badge>
                  )}
                </Group>
              </Group>
              {b.title && (
                <Text size='xs' c='dimmed' lineClamp={1}>
                  {b.title}
                </Text>
              )}
              <Group gap='xs' mt={4} wrap='nowrap'>
                <Text size='xs' w={90}>
                  Built {qty(done)}/{qty(total)}
                </Text>
                <Progress value={(done / total) * 100} style={{ flex: 1 }} color='green' />
              </Group>
              <Group gap='xs' wrap='nowrap'>
                <Text size='xs' w={90}>
                  Allocated {b.lines_allocated}/{b.lines}
                </Text>
                <Progress value={allocPct} style={{ flex: 1 }} color={allocPct >= 100 ? 'green' : 'blue'} />
              </Group>
              {b.shortages.length > 0 && (
                <Text size='xs' c='red' mt={2}>
                  Missing: {b.shortages.map((s) => `${s.name} (${qty(s.missing, s.units)})`).join(', ')}
                </Text>
              )}
            </Paper>
          );
        })}
      </Stack>
    </Frame>
  );
}

/* ------------------------------------------------------------------ 4. purchasing */

type NeedRow = {
  part: number;
  name: string;
  ipn: string;
  units: string;
  action: 'buy' | 'build';
  required: string;
  available: string;
  shortage: string;
  on_order: string;
  orders: { order: number; reference: string; target_date: string | null }[];
  to_get: string;
  reasons: string[];
  no_supplier: boolean;
  estimate: null | { supplier: string; sku: string; packs: string; pack_quantity: string; price_total?: string; uplift_percent?: string; landed_total?: string };
};

function PurchasingWidget({ context }: { context: InvenTreePluginContext }) {
  const { data, setData, error, loading, reload } = useWidget<{
    currency: string;
    parts: NeedRow[];
    total_by_supplier: Record<string, string>;
    total: string;
  }>(context, 'purchasing');
  if (!data) return <Loading loading={loading} error={error} />;
  const cur = data.currency;
  if (!data.parts.length)
    return <Empty>Nothing to order: active build orders and minimum stock levels are covered.</Empty>;

  const exportCsv = () =>
    downloadCsv('purchasing-needs.csv', [
      ['Part', 'IPN', 'Units', 'Action', 'Why', 'Required', 'Usable stock', 'Short', 'On order', 'To get', 'Supplier', 'SKU', 'Packs', 'Pack qty', `Est. landed (${cur})`],
      ...data.parts.map((p) => [
        p.name, p.ipn, p.units, p.action, p.reasons.join('; '), p.required, p.available, p.shortage, p.on_order, p.to_get,
        p.estimate?.supplier ?? '', p.estimate?.sku ?? '', p.estimate?.packs ?? '', p.estimate?.pack_quantity ?? '', p.estimate?.landed_total ?? '',
      ]),
    ]);

  return (
    <Frame onRefresh={refreshable(reload, context, `${BASE}/dashboard/purchasing/`, setData)} loading={loading}>
      <Table striped verticalSpacing={4} fz='sm' stickyHeader>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Part</Table.Th>
            <Table.Th>Short</Table.Th>
            <Table.Th>On order</Table.Th>
            <Table.Th>To get</Table.Th>
            <Table.Th>Supplier · est. landed</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {data.parts.map((p) => (
            <Table.Tr key={p.part}>
              <Table.Td>
                <PartLink context={context} part={p.part} name={p.name} />
                <Text size='xs' c='dimmed' lineClamp={1}>
                  {p.reasons.join(' · ')}
                </Text>
              </Table.Td>
              <Table.Td>{qty(p.shortage, p.units)}</Table.Td>
              <Table.Td>
                {qty(p.on_order, p.units)}
                {p.orders.map((o) => (
                  <OrderLink key={o.order} context={context} order={o.order} reference={o.reference} />
                ))}
              </Table.Td>
              <Table.Td>
                <Group gap={4} wrap='nowrap'>
                  <Text size='sm' fw={600}>
                    {qty(p.to_get, p.units)}
                  </Text>
                  {p.action === 'build' && (
                    <Badge size='xs' color='grape' variant='light'>
                      build
                    </Badge>
                  )}
                </Group>
              </Table.Td>
              <Table.Td>
                {p.estimate ? (
                  <>
                    <Text size='sm'>
                      {p.estimate.supplier}
                      {p.estimate.landed_total ? ` · ${money(p.estimate.landed_total, cur)}` : ''}
                    </Text>
                    <Text size='xs' c='dimmed'>
                      {p.estimate.sku} · {qty(p.estimate.packs)} × {qty(p.estimate.pack_quantity, p.units)}
                      {p.estimate.uplift_percent ? ` · fees ${pct(p.estimate.uplift_percent)}` : ''}
                    </Text>
                  </>
                ) : p.no_supplier ? (
                  <Text size='xs' c='red'>
                    No supplier part
                  </Text>
                ) : (
                  <Text size='xs' c='dimmed'>
                    {num(p.to_get) ? '—' : 'covered by open orders'}
                  </Text>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <Group gap='md' mt={6} justify='space-between'>
        <Group gap='md'>
          {Object.entries(data.total_by_supplier).map(([s, v]) => (
            <Text size='xs' key={s}>
              {s}: <b>{money(v, cur)}</b>
            </Text>
          ))}
          {num(data.total) ? (
            <Text size='xs'>
              Total ≈ <b>{money(data.total, cur)}</b>
            </Text>
          ) : null}
        </Group>
        <Text size='xs' c={context.theme.primaryColor} style={{ cursor: 'pointer' }} onClick={exportCsv}>
          Export CSV
        </Text>
      </Group>
    </Frame>
  );
}

/* ------------------------------------------------------------------ 5. incoming */

type Incoming = {
  currency: string;
  open: { order: number; reference: string; supplier: string; status: string; target_date: string | null; overdue: boolean; lines: number; lines_remaining: number; value: string | null }[];
  open_value: string;
  overdue: number;
  recent: { order: number; reference: string; supplier: string; date: string }[];
};

function IncomingWidget({ context }: { context: InvenTreePluginContext }) {
  const { data, setData, error, loading, reload } = useWidget<Incoming>(context, 'incoming');
  if (!data) return <Loading loading={loading} error={error} />;
  const cur = data.currency;

  return (
    <Frame onRefresh={refreshable(reload, context, `${BASE}/dashboard/incoming/`, setData)} loading={loading}>
      <Stack gap={6}>
        {data.open.length === 0 ? (
          <Empty>No open purchase orders.</Empty>
        ) : (
          data.open.map((o) => (
            <Paper key={o.order} withBorder p={6} radius='md'>
              <Group justify='space-between' wrap='nowrap'>
                <Group gap={6} wrap='nowrap'>
                  <OrderLink context={context} order={o.order} reference={o.reference} />
                  <Text size='xs' c='dimmed'>
                    {o.supplier}
                  </Text>
                </Group>
                <Text size='xs'>{money(o.value, cur)}</Text>
              </Group>
              <Group gap={4}>
                <Badge size='xs' variant='light'>
                  {o.status}
                </Badge>
                <Badge size='xs' variant='light' color={o.overdue ? 'red' : 'gray'}>
                  {o.target_date ? `${o.overdue ? 'overdue' : 'expected'} ${o.target_date}` : 'no date'}
                </Badge>
                <Text size='xs' c='dimmed'>
                  {o.lines_remaining}/{o.lines} lines to receive
                </Text>
              </Group>
            </Paper>
          ))
        )}
        {data.recent.length > 0 && (
          <>
            <Text size='xs' fw={600} mt={4}>
              Received in the last 30 days
            </Text>
            {data.recent.map((o) => (
              <Group key={o.order} gap={6}>
                <OrderLink context={context} order={o.order} reference={o.reference} />
                <Text size='xs' c='dimmed'>
                  {o.supplier} · {o.date}
                </Text>
              </Group>
            ))}
          </>
        )}
      </Stack>
    </Frame>
  );
}

/* ------------------------------------------------------------------ 6. inventory */

type Inventory = {
  currency: string;
  total: string;
  external: string;
  estimated: string;
  unpriced_items: number;
  categories: { category: string; value: string; items: number }[];
  top_parts: { part: number; name: string; units: string; value: string; quantity: string }[];
};

function InventoryWidget({ context }: { context: InvenTreePluginContext }) {
  const { data, setData, error, loading, reload } = useWidget<Inventory>(context, 'inventory');
  if (!data) return <Loading loading={loading} error={error} />;
  const cur = data.currency;
  const max = Math.max(...data.categories.map((c) => num(c.value) ?? 0), 1);

  return (
    <Frame onRefresh={refreshable(reload, context, `${BASE}/dashboard/inventory/`, setData)} loading={loading}>
      <Stack gap={6}>
        <Group gap='lg'>
          <Text fw={700} size='xl'>
            {money(data.total, cur, 0)}
          </Text>
          <Text size='xs' c='dimmed'>
            {num(data.external) ? `${money(data.external, cur, 0)} at external locations · ` : ''}
            {num(data.estimated) ? `${money(data.estimated, cur, 0)} built stock at BOM cost · ` : ''}
            {data.unpriced_items ? `${data.unpriced_items} items without price` : 'all items priced'}
          </Text>
        </Group>
        {data.categories.map((c) => (
          <Group key={c.category} gap='xs' wrap='nowrap'>
            <Text size='xs' w='40%' lineClamp={1}>
              {c.category}
            </Text>
            <Progress value={((num(c.value) ?? 0) / max) * 100} style={{ flex: 1 }} />
            <Text size='xs' w={80} ta='right'>
              {money(c.value, cur, 0)}
            </Text>
          </Group>
        ))}
        <Text size='xs' fw={600} mt={4}>
          Highest value parts
        </Text>
        {data.top_parts.map((p) => (
          <Group key={p.part} justify='space-between' wrap='nowrap' gap='xs'>
            <PartLink context={context} part={p.part} name={p.name} />
            <Text size='xs' style={{ whiteSpace: 'nowrap' }}>
              {qty(p.quantity, p.units)} · {money(p.value, cur, 0)}
            </Text>
          </Group>
        ))}
      </Stack>
    </Frame>
  );
}

/* ------------------------------------------------------------------ 7. fees */

type Fees = {
  currency: string;
  months: ({ month: string; goods: string } & Record<string, string>)[];
  totals: Record<string, string>;
  fees_counted: string;
  goods_total: string;
  uplift_percent: string | null;
  suppliers: { supplier: number; name: string; uplift_percent: string }[];
};

const FEE_COLORS: Record<string, string> = { shipping: 'blue', duty: 'orange', other: 'grape', import_vat: 'teal', excluded: 'gray', not_counted: 'gray' };
const FEE_LABELS: Record<string, string> = { shipping: 'Shipping', duty: 'Duty', other: 'Other', import_vat: 'Import VAT', not_counted: 'Not counted' };

function FeesWidget({ context }: { context: InvenTreePluginContext }) {
  const { data, setData, error, loading, reload } = useWidget<Fees>(context, 'fees');
  if (!data) return <Loading loading={loading} error={error} />;
  const cur = data.currency;
  const cats = ['shipping', 'duty', 'other', 'import_vat'];
  const monthFee = (m: Record<string, string>) => cats.reduce((s, c) => s + (num(m[c]) ?? 0), 0);
  const months = data.months.filter((m) => monthFee(m) > 0 || (num(m.goods) ?? 0) > 0);
  const maxMonth = Math.max(...months.map(monthFee), 1);
  const maxUp = Math.max(...data.suppliers.map((s) => num(s.uplift_percent) ?? 0), 1);

  return (
    <Frame onRefresh={refreshable(reload, context, `${BASE}/dashboard/fees/`, setData)} loading={loading}>
      <Stack gap={6}>
        <Group gap='lg'>
          <Text fw={700} size='xl'>
            {money(data.fees_counted, cur, 0)}
          </Text>
          <Text size='xs' c='dimmed'>
            fees on {money(data.goods_total, cur, 0)} of goods ({pct(data.uplift_percent)}) · last 12 months
          </Text>
        </Group>
        <Group gap='xs'>
          {Object.entries(data.totals).map(([k, v]) => (
            <Badge key={k} size='sm' variant='light' color={FEE_COLORS[k] ?? 'gray'}>
              {FEE_LABELS[k] ?? k}: {money(v, cur, 0)}
            </Badge>
          ))}
        </Group>
        {months.length === 0 ? (
          <Empty>No received orders in the last 12 months.</Empty>
        ) : (
          months.map((m) => (
            <Group key={m.month} gap='xs' wrap='nowrap'>
              <Text size='xs' w={60}>
                {m.month}
              </Text>
              <Progress.Root style={{ flex: 1 }} size='lg'>
                {cats.map((c) =>
                  num(m[c]) ? (
                    <Tooltip key={c} label={`${FEE_LABELS[c]}: ${money(m[c], cur)}`}>
                      <Progress.Section value={((num(m[c]) ?? 0) / maxMonth) * 100} color={FEE_COLORS[c]} />
                    </Tooltip>
                  ) : null,
                )}
              </Progress.Root>
              <Text size='xs' w={70} ta='right'>
                {money(monthFee(m), cur, 0)}
              </Text>
            </Group>
          ))
        )}
        <Text size='xs' fw={600} mt={4}>
          Typical fee uplift per supplier
        </Text>
        {data.suppliers.map((s) => {
          const v = num(s.uplift_percent) ?? 0;
          return (
            <Group key={s.supplier} gap='xs' wrap='nowrap'>
              <Text size='xs' w={90} lineClamp={1}>
                {s.name}
              </Text>
              <Progress value={(v / maxUp) * 100} style={{ flex: 1 }} color={v < 5 ? 'green' : v < 20 ? 'yellow' : v < 50 ? 'orange' : 'red'} />
              <Text size='xs' w={55} ta='right'>
                {pct(v)}
              </Text>
            </Group>
          );
        })}
      </Stack>
    </Frame>
  );
}

/* ------------------------------------------------------------------ exports */

export function RenderOverview(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <OverviewWidget context={context} />;
}

export function RenderCapacity(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <CapacityWidget context={context} />;
}

// Backwards compatible name (v0.1.0)
export const RenderDashboardItem = RenderCapacity;

export function RenderProduction(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <ProductionWidget context={context} />;
}

export function RenderPurchasing(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <PurchasingWidget context={context} />;
}

export function RenderIncoming(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <IncomingWidget context={context} />;
}

export function RenderInventory(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <InventoryWidget context={context} />;
}

export function RenderFees(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <FeesWidget context={context} />;
}
