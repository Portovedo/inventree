import {
  Alert,
  Badge,
  Button,
  Divider,
  Group,
  MultiSelect,
  NumberInput,
  Paper,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useEffect, useMemo, useState } from 'react';

import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';

import {
  BASE,
  Loading,
  OrderLink,
  PartLink,
  SourceBadge,
  StatusBadge,
  UpliftBadge,
  downloadCsv,
  errorText,
  money,
  num,
  pct,
  qty,
  usePluginData,
} from './shared';

/* ======================================================================== Order panel */

type FeeRow = {
  id: number;
  reference: string;
  description: string;
  amount: string;
  category: string;
  detected_category: string;
  overridden: boolean;
  targets: number[];
  included: boolean;
  method_used: string | null;
  unallocated_reason: string | null;
};

type LineRow = {
  line: number;
  part: number | null;
  part_name: string;
  sku: string;
  units: string;
  quantity: string;
  received: string;
  pack_quantity: string;
  native_quantity: string;
  net_value: string;
  net_unit_price_native: string | null;
  fees: string;
  fees_by_category: Record<string, string>;
  fee_per_native_unit: string;
  landed_unit_price_native: string | null;
  uplift_percent: string | null;
  weight: string | null;
};

type OrderData = {
  order: number;
  reference: string;
  supplier: string;
  currency: string;
  method: string;
  method_is_default: boolean;
  goods_total: string;
  fees_included: string;
  fees_excluded: string;
  uplift_percent: string | null;
  lines: LineRow[];
  fees: FeeRow[];
  warnings: string[];
  status: string;
  applied_at: string | null;
  applied_by: string | null;
  can_edit: boolean;
  methods: string[];
  categories: string[];
  stock_items_updated?: number;
  restored?: number;
};

const CATEGORY_LABELS: Record<string, string> = {
  shipping: 'Shipping',
  duty: 'Duty / customs',
  import_vat: 'Import VAT',
  other: 'Other fee',
  excluded: 'Not a product cost',
};

const METHOD_LABELS: Record<string, string> = {
  value: 'By value',
  quantity: 'By quantity',
  manual: 'Manual weights',
};

function OrderPanel({ context }: { context: InvenTreePluginContext }) {
  const url = context.id ? `${BASE}/order/${context.id}/` : null;
  const { data, setData, error, loading } = usePluginData<OrderData>(context, url);
  const [busy, setBusy] = useState<string | null>(null);

  const lineOptions = useMemo(
    () => (data?.lines ?? []).map((l) => ({ value: String(l.line), label: `${l.part_name || l.sku} (${l.sku})` })),
    [data],
  );

  const post = (path: string, body: any, label: string, message: (d: OrderData) => string) => {
    if (!url) return;
    setBusy(label);
    context.api
      .post(`${url}${path}`, body)
      .then((res) => {
        setData(res.data);
        notifications.show({ title: 'Landed cost', message: message(res.data), color: 'green' });
        context.reloadInstance?.();
      })
      .catch((err) => notifications.show({ title: 'Landed cost', message: errorText(err), color: 'red' }))
      .finally(() => setBusy(null));
  };

  const saveConfig = (body: any) => post('', body, 'save', () => 'Settings saved');

  if (!data) return <Loading loading={loading} error={error} />;

  const editable = data.can_edit && !busy;
  const cur = data.currency;

  return (
    <Stack gap='md'>
      <Group justify='space-between' align='flex-start'>
        <Stack gap={4}>
          <Group gap='xs'>
            <StatusBadge status={data.status} />
          </Group>
          <Text size='sm' c='dimmed'>
            Landed cost = invoice price + this order&apos;s fees, spread over its lines. Applying writes it into the
            purchase price of every stock item received on this order (including stock later split or used in builds).
            Revert restores the original prices.
          </Text>
          {data.applied_at && (
            <Text size='xs' c='dimmed'>
              Applied {new Date(data.applied_at).toLocaleString()}
              {data.applied_by ? ` by ${data.applied_by}` : ' automatically'}
            </Text>
          )}
        </Stack>
        <Group gap='xs'>
          <Button
            disabled={!editable || data.status === 'applied'}
            loading={busy === 'apply'}
            onClick={() =>
              post('apply/', {}, 'apply', (d) => `Landed cost written to ${d.stock_items_updated ?? 0} stock item(s)`)
            }
          >
            {data.status === 'stale' ? 'Re-apply to stock' : 'Apply to stock'}
          </Button>
          <Button
            variant='outline'
            color='red'
            disabled={!editable || data.status === 'not_applied'}
            loading={busy === 'revert'}
            onClick={() => post('revert/', {}, 'revert', (d) => `Original price restored on ${d.restored ?? 0} stock item(s)`)}
          >
            Revert
          </Button>
        </Group>
      </Group>

      {data.warnings.length > 0 && (
        <Alert color='orange' title='Warnings'>
          {data.warnings.map((w) => (
            <Text size='sm' key={w}>
              {w}
            </Text>
          ))}
        </Alert>
      )}

      <SimpleGrid cols={{ base: 2, sm: 4 }}>
        <Stat label='Goods (net)' value={money(data.goods_total, cur)} />
        <Stat label='Fees spread over lines' value={money(data.fees_included, cur)} />
        <Stat label='Fees not counted' value={money(data.fees_excluded, cur)} />
        <Stat label='Uplift on goods' value={<UpliftBadge value={data.uplift_percent} />} />
      </SimpleGrid>

      <Group gap='sm'>
        <Text size='sm' fw={500}>
          Spread fees
        </Text>
        <SegmentedControl
          size='xs'
          disabled={!editable}
          value={data.method}
          data={data.methods.map((m) => ({ value: m, label: METHOD_LABELS[m] ?? m }))}
          onChange={(value) => saveConfig({ method: value })}
        />
        {data.method_is_default ? (
          <Badge variant='light' color='gray'>
            plugin default
          </Badge>
        ) : (
          <Button size='compact-xs' variant='subtle' disabled={!editable} onClick={() => saveConfig({ method: '' })}>
            use default
          </Button>
        )}
      </Group>

      <Divider label='Fees (extra lines)' labelPosition='left' />
      {data.fees.length === 0 ? (
        <Text size='sm' c='dimmed'>
          This order has no extra lines. Add shipping, customs or other charges as extra line items on the order.
        </Text>
      ) : (
        <Table striped withTableBorder verticalSpacing='xs'>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Reference</Table.Th>
              <Table.Th>Amount</Table.Th>
              <Table.Th>Type</Table.Th>
              <Table.Th>Applies to</Table.Th>
              <Table.Th>Counted</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.fees.map((fee) => (
              <Table.Tr key={fee.id}>
                <Table.Td>
                  <Text size='sm'>{fee.reference || '—'}</Text>
                  {fee.description && (
                    <Text size='xs' c='dimmed'>
                      {fee.description}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>{money(fee.amount, cur)}</Table.Td>
                <Table.Td w={220}>
                  <Select
                    size='xs'
                    disabled={!editable}
                    value={fee.overridden ? fee.category : ''}
                    data={[
                      { value: '', label: `Auto: ${CATEGORY_LABELS[fee.detected_category] ?? fee.detected_category}` },
                      ...data.categories.map((c) => ({ value: c, label: CATEGORY_LABELS[c] ?? c })),
                    ]}
                    onChange={(value) => saveConfig({ extra_lines: [{ id: fee.id, category: value ?? '' }] })}
                    allowDeselect={false}
                  />
                </Table.Td>
                <Table.Td w={280}>
                  <MultiSelect
                    size='xs'
                    disabled={!editable}
                    placeholder='All lines'
                    value={fee.targets.map(String)}
                    data={lineOptions}
                    onChange={(values) =>
                      saveConfig({ extra_lines: [{ id: fee.id, targets: values.map((v) => Number(v)) }] })
                    }
                    clearable
                  />
                </Table.Td>
                <Table.Td>
                  {fee.included ? (
                    <Tooltip label={`Spread ${METHOD_LABELS[fee.method_used ?? ''] ?? ''}`.trim()}>
                      <Badge color='green' variant='light'>
                        Yes
                      </Badge>
                    </Tooltip>
                  ) : (
                    <Tooltip
                      label={
                        fee.unallocated_reason ??
                        (fee.category === 'import_vat'
                          ? 'Import VAT is excluded (enable in plugin settings if not recoverable)'
                          : 'Not a product cost')
                      }
                    >
                      <Badge color='gray' variant='light'>
                        No
                      </Badge>
                    </Tooltip>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Divider label='Order lines' labelPosition='left' />
      <Table striped withTableBorder verticalSpacing='xs'>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Part</Table.Th>
            <Table.Th>Quantity</Table.Th>
            {data.method === 'manual' && <Table.Th>Weight</Table.Th>}
            <Table.Th>Invoice / unit</Table.Th>
            <Table.Th>Fees / unit</Table.Th>
            <Table.Th>Landed / unit</Table.Th>
            <Table.Th>Uplift</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {data.lines.map((line) => (
            <Table.Tr key={line.line}>
              <Table.Td>
                {line.part ? (
                  <PartLink context={context} part={line.part} name={line.part_name} />
                ) : (
                  <Text size='sm'>{line.part_name}</Text>
                )}
                <Text size='xs' c='dimmed'>
                  {line.sku}
                  {num(line.pack_quantity) !== 1 ? ` · pack of ${qty(line.pack_quantity, line.units)}` : ''}
                </Text>
              </Table.Td>
              <Table.Td>{qty(line.native_quantity, line.units)}</Table.Td>
              {data.method === 'manual' && (
                <Table.Td w={110}>
                  <NumberInput
                    size='xs'
                    min={0}
                    disabled={!editable}
                    defaultValue={line.weight ?? ''}
                    onBlur={(e) => {
                      const weights: Record<string, string> = {};
                      data.lines.forEach((l) => {
                        if (l.weight !== null && l.weight !== undefined) weights[String(l.line)] = String(l.weight);
                      });
                      weights[String(line.line)] = e.currentTarget.value;
                      saveConfig({ weights });
                    }}
                  />
                </Table.Td>
              )}
              <Table.Td>{money(line.net_unit_price_native, cur, 4)}</Table.Td>
              <Table.Td>
                <Tooltip
                  disabled={Object.keys(line.fees_by_category).length === 0}
                  label={Object.entries(line.fees_by_category)
                    .map(([k, v]) => `${CATEGORY_LABELS[k] ?? k}: ${money(v, cur)}`)
                    .join(' · ')}
                >
                  <Text size='sm'>{money(line.fee_per_native_unit, cur, 4)}</Text>
                </Tooltip>
              </Table.Td>
              <Table.Td>
                <Text size='sm' fw={600}>
                  {money(line.landed_unit_price_native, cur, 4)}
                </Text>
              </Table.Td>
              <Table.Td>
                <UpliftBadge value={line.uplift_percent} />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}

function Stat({ label, value }: { label: string; value: any }) {
  return (
    <Paper withBorder p='xs' radius='md'>
      <Text size='xs' c='dimmed'>
        {label}
      </Text>
      {typeof value === 'string' ? (
        <Text fw={600} size='lg'>
          {value}
        </Text>
      ) : (
        value
      )}
    </Paper>
  );
}

/* ======================================================================== Capacity panel */

type Brief = { part: number; name: string; ipn: string; units: string; assembly: boolean; available: string | null };

type CapacityData = {
  has_bom: boolean;
  native_can_build: number | null;
  single_level: number;
  multi_level: number;
  limiting: (Brief & { missing: string })[];
  builds_needed: (Brief & { quantity: string })[];
  headroom: (Brief & { per_unit: string; can_build: number; reference: string; alternatives: string[] })[];
  settings: Record<string, boolean>;
};

type PlanData = {
  quantity: string;
  feasible: boolean;
  builds: (Brief & { quantity: string })[];
  requirements: (Brief & {
    required: string;
    from_stock: string;
    from_alternatives: Record<string, string>;
    to_build: string | null;
    shortage: string | null;
  })[];
  purchasing: (Brief & {
    shortage: string;
    on_order: string;
    orders: { order: number; reference: string; quantity: string; target_date: string | null }[];
    to_buy: string;
    no_supplier: boolean;
    estimate: null | {
      supplier: string;
      sku: string;
      packs: string;
      pack_quantity: string;
      price_total?: string;
      uplift_percent?: string;
      landed_total?: string;
    };
  })[];
  purchase_total_by_supplier: Record<string, string>;
  purchase_total: string;
  unit_cost: string | null;
  unit_cost_complete: boolean;
};

function CapacityPanel({ context }: { context: InvenTreePluginContext }) {
  const cur = String(context.globalSettings?.getSetting?.('INVENTREE_DEFAULT_CURRENCY') || 'EUR');
  const { data, error, loading, reload } = usePluginData<CapacityData>(
    context,
    context.id ? `${BASE}/part/${context.id}/capacity/` : null,
  );
  const [target, setTarget] = useState<number | string>(10);
  const [plan, setPlan] = useState<PlanData | null>(null);
  const [planBusy, setPlanBusy] = useState(false);

  useEffect(() => {
    if (data?.has_bom) setTarget(Math.max(1, (data.multi_level ?? 0) + 1));
  }, [data?.has_bom, data?.multi_level]);

  const runPlan = () => {
    setPlanBusy(true);
    context.api
      .get(`${BASE}/part/${context.id}/plan/`, { params: { quantity: target } })
      .then((res) => setPlan(res.data))
      .catch((err) => notifications.show({ title: 'Plan', message: errorText(err), color: 'red' }))
      .finally(() => setPlanBusy(false));
  };

  if (!data) return <Loading loading={loading} error={error} />;
  if (!data.has_bom) return <Text c='dimmed'>This assembly has no bill of materials.</Text>;

  const exportPlan = () => {
    if (!plan) return;
    const rows: (string | number | null)[][] = [
      ['Part', 'IPN', 'Units', 'Shortage', 'On order', 'To buy', 'Supplier', 'SKU', 'Packs', 'Pack qty', 'Price', 'Fee uplift %', `Est. landed (${cur})`],
      ...plan.purchasing.map((p) => [
        p.name,
        p.ipn,
        p.units,
        p.shortage,
        p.on_order,
        p.to_buy,
        p.estimate?.supplier ?? '',
        p.estimate?.sku ?? '',
        p.estimate?.packs ?? '',
        p.estimate?.pack_quantity ?? '',
        p.estimate?.price_total ?? '',
        p.estimate?.uplift_percent ?? '',
        p.estimate?.landed_total ?? '',
      ]),
    ];
    downloadCsv(`plan-${context.instance?.IPN || context.id}-x${plan.quantity}.csv`, rows);
  };

  return (
    <Stack gap='md'>
      <Group justify='flex-end'>
        <Button size='compact-sm' variant='subtle' onClick={reload} loading={loading}>
          Refresh
        </Button>
      </Group>

      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <Paper withBorder p='sm' radius='md'>
          <Text size='xs' c='dimmed'>
            Can build now (incl. building sub-assemblies)
          </Text>
          <Text fw={700} size='xl'>
            {data.multi_level}
          </Text>
        </Paper>
        <Paper withBorder p='sm' radius='md'>
          <Text size='xs' c='dimmed'>
            From stock on hand only (one level)
          </Text>
          <Text fw={700} size='xl'>
            {data.single_level}
          </Text>
          {data.native_can_build !== null && data.native_can_build !== data.single_level && (
            <Text size='xs' c='dimmed'>
              InvenTree built-in figure: {data.native_can_build}
            </Text>
          )}
        </Paper>
        <Paper withBorder p='sm' radius='md'>
          <Text size='xs' c='dimmed'>
            Blocking unit #{data.multi_level + 1}
          </Text>
          {data.limiting.length === 0 ? (
            <Text size='sm'>—</Text>
          ) : (
            data.limiting.map((l) => (
              <Group key={l.part} gap={6} wrap='nowrap' justify='space-between'>
                <PartLink context={context} part={l.part} name={l.name} />
                <Badge size='sm' color='red' variant='light' style={{ flexShrink: 0 }}>
                  short {qty(l.missing, l.units)}
                </Badge>
              </Group>
            ))
          )}
        </Paper>
      </SimpleGrid>

      {data.builds_needed.length > 0 && (
        <Text size='sm'>
          To build {data.multi_level}:{' '}
          {data.builds_needed.map((b, i) => (
            <span key={b.part}>
              {i > 0 && ', '}
              build {qty(b.quantity)} × {b.name}
            </span>
          ))}
        </Text>
      )}

      <Text size='xs' c='dimmed'>
        Counts usable stock{data.settings.exclude_external ? ', excluding external locations' : ''}
        {data.settings.include_allocated ? '' : ', excluding stock allocated to builds/sales'}
        {data.settings.include_optional ? '' : ', ignoring optional lines'}
        {data.settings.include_consumable ? '' : ', ignoring consumable lines'}. Change in plugin settings.
      </Text>

      <Divider label='Direct components' labelPosition='left' />
      <Table striped withTableBorder verticalSpacing='xs'>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Component</Table.Th>
            <Table.Th>Per unit</Table.Th>
            <Table.Th>Usable stock</Table.Th>
            <Table.Th>Enough for</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {data.headroom.map((h, i) => (
            <Table.Tr key={`${h.part}-${i}`}>
              <Table.Td>
                <Group gap={6}>
                  <PartLink context={context} part={h.part} name={h.name} />
                  {h.assembly && (
                    <Badge size='xs' variant='light' color='grape'>
                      sub-assembly
                    </Badge>
                  )}
                </Group>
                {h.alternatives.length > 0 && (
                  <Text size='xs' c='dimmed'>
                    + alternatives: {h.alternatives.join(', ')}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>{qty(h.per_unit, h.units)}</Table.Td>
              <Table.Td>{qty(h.available, h.units)}</Table.Td>
              <Table.Td>
                <Badge color={h.can_build === 0 ? 'red' : i < 3 ? 'orange' : 'gray'} variant='light'>
                  {h.can_build >= 10000000 ? '∞' : h.can_build}
                </Badge>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <Divider label='Plan a production run' labelPosition='left' />
      <Group align='flex-end'>
        <NumberInput label='Units to build' min={1} max={1000000} value={target} onChange={setTarget} w={160} />
        <Button onClick={runPlan} loading={planBusy}>
          Calculate
        </Button>
        {plan && (
          <Button variant='outline' onClick={exportPlan}>
            Export purchasing CSV
          </Button>
        )}
      </Group>

      {plan && (
        <Stack gap='sm'>
          <Group gap='sm'>
            {plan.feasible ? (
              <Badge color='green'>Can build {qty(plan.quantity)} from stock</Badge>
            ) : (
              <Badge color='orange'>Purchasing needed for {qty(plan.quantity)}</Badge>
            )}
            {plan.unit_cost && (
              <Text size='sm'>
                Est. landed cost: {money(plan.unit_cost, cur)} / unit · {money(num(plan.unit_cost)! * num(plan.quantity)!, cur)}{' '}
                total{plan.unit_cost_complete ? '' : ' (some component prices missing)'}
              </Text>
            )}
          </Group>

          {plan.builds.length > 0 && (
            <Text size='sm'>
              Builds:{' '}
              {plan.builds.map((b, i) => (
                <span key={b.part}>
                  {i > 0 && ' · '}
                  {qty(b.quantity)} × {b.name}
                </span>
              ))}
            </Text>
          )}

          {plan.purchasing.length > 0 && (
            <>
              <Table striped withTableBorder verticalSpacing='xs'>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>To purchase</Table.Th>
                    <Table.Th>Short</Table.Th>
                    <Table.Th>On order</Table.Th>
                    <Table.Th>To buy</Table.Th>
                    <Table.Th>Supplier</Table.Th>
                    <Table.Th>Est. landed cost</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {plan.purchasing.map((p) => (
                    <Table.Tr key={p.part}>
                      <Table.Td>
                        <PartLink context={context} part={p.part} name={p.name} />
                        {p.assembly && (
                          <Text size='xs' c='orange'>
                            Assembly not expanded
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>{qty(p.shortage, p.units)}</Table.Td>
                      <Table.Td>
                        {qty(p.on_order, p.units)}
                        {p.orders.map((o) => (
                          <Group key={o.order} gap={4}>
                            <OrderLink context={context} order={o.order} reference={o.reference} />
                            {o.target_date && (
                              <Text size='xs' c='dimmed'>
                                {o.target_date}
                              </Text>
                            )}
                          </Group>
                        ))}
                      </Table.Td>
                      <Table.Td>
                        <Text fw={600} size='sm'>
                          {qty(p.to_buy, p.units)}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        {p.estimate ? (
                          <>
                            <Text size='sm'>{p.estimate.supplier}</Text>
                            <Text size='xs' c='dimmed'>
                              {p.estimate.sku} · {qty(p.estimate.packs)} × {qty(p.estimate.pack_quantity, p.units)}
                            </Text>
                          </>
                        ) : p.no_supplier ? (
                          <Text size='xs' c='red'>
                            No supplier part
                          </Text>
                        ) : (
                          '—'
                        )}
                      </Table.Td>
                      <Table.Td>
                        {p.estimate?.landed_total ? (
                          <Tooltip
                            label={`${money(p.estimate.price_total, cur)} at price break ${pct(p.estimate.uplift_percent)} typical fees`}
                          >
                            <Text size='sm'>{money(p.estimate.landed_total, cur)}</Text>
                          </Tooltip>
                        ) : p.estimate ? (
                          <Text size='xs' c='dimmed'>
                            no price break
                          </Text>
                        ) : (
                          '—'
                        )}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
              <Group gap='lg'>
                {Object.entries(plan.purchase_total_by_supplier).map(([s, v]) => (
                  <Text size='sm' key={s}>
                    {s}: <b>{money(v, cur)}</b>
                  </Text>
                ))}
                <Text size='sm'>
                  Total: <b>{money(plan.purchase_total, cur)}</b>
                </Text>
              </Group>
            </>
          )}

          <Table withTableBorder verticalSpacing={4} fz='xs'>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Component (all levels)</Table.Th>
                <Table.Th>Required</Table.Th>
                <Table.Th>From stock</Table.Th>
                <Table.Th>To build</Table.Th>
                <Table.Th>Short</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {plan.requirements.map((r) => (
                <Table.Tr key={r.part}>
                  <Table.Td>
                    <PartLink context={context} part={r.part} name={r.name} />
                  </Table.Td>
                  <Table.Td>{qty(r.required, r.units)}</Table.Td>
                  <Table.Td>
                    {qty(r.from_stock, r.units)}
                    {Object.entries(r.from_alternatives).map(([n, q]) => (
                      <Text key={n} size='xs' c='dimmed'>
                        + {qty(q)} {n}
                      </Text>
                    ))}
                  </Table.Td>
                  <Table.Td>{r.to_build ? qty(r.to_build) : ''}</Table.Td>
                  <Table.Td>
                    {r.shortage ? (
                      <Text size='xs' c='red'>
                        {qty(r.shortage, r.units)}
                      </Text>
                    ) : (
                      ''
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Stack>
      )}
    </Stack>
  );
}

/* ======================================================================== Part cost panel */

type CostNode = {
  part: number;
  name: string;
  units: string;
  assembly: boolean;
  unit_cost: string | null;
  fees_unit: string | null;
  source: string;
  complete: boolean;
  bom_quantity?: string;
  extended?: string | null;
  children: CostNode[];
};

type PartCostData = {
  currency: string;
  history: {
    order: number;
    reference: string;
    supplier: string;
    date: string | null;
    received_native: string;
    net_unit: string | null;
    fee_unit: string | null;
    landed_unit: string | null;
    uplift_percent: string | null;
    status: string;
  }[];
  stock: { average: string | null; priced_quantity: string; unpriced_quantity: string; landed_quantity: string };
  rolled?: CostNode;
  primary_supplier?: { name: string; sku: string; uplift_percent: string | null };
};

function flatten(node: CostNode, depth = 0, out: { node: CostNode; depth: number }[] = []) {
  node.children.forEach((child) => {
    out.push({ node: child, depth });
    if (child.children.length) flatten(child, depth + 1, out);
  });
  return out;
}

function PartCostPanel({ context }: { context: InvenTreePluginContext }) {
  const { data, error, loading } = usePluginData<PartCostData>(
    context,
    context.id ? `${BASE}/part/${context.id}/landed/` : null,
  );
  if (!data) return <Loading loading={loading} error={error} />;
  const cur = data.currency;
  const units = context.instance?.units ?? '';
  const last = data.history[0];

  return (
    <Stack gap='md'>
      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        {data.rolled ? (
          <Stat
            label='Unit cost (BOM roll-up, landed)'
            value={
              <Group gap='xs'>
                <Text fw={700} size='lg'>
                  {money(data.rolled.unit_cost, cur)}
                </Text>
                <SourceBadge source={data.rolled.source} />
              </Group>
            }
          />
        ) : (
          <Stat label='Stock on hand, avg landed / unit' value={money(data.stock.average, cur, 4)} />
        )}
        {data.rolled ? (
          <Stat label='of which fees (shipping, duty, …)' value={money(data.rolled.fees_unit, cur, 4)} />
        ) : (
          <Stat label='Last purchase, landed / unit' value={last ? money(last.landed_unit, cur, 4) : '—'} />
        )}
        {data.rolled ? (
          <Stat
            label='Components without a price'
            value={String(flatten(data.rolled).filter(({ node }) => !node.assembly && node.unit_cost === null).length)}
          />
        ) : data.primary_supplier ? (
          <Stat
            label={`Typical fee uplift · ${data.primary_supplier.name}`}
            value={<UpliftBadge value={data.primary_supplier.uplift_percent} />}
          />
        ) : (
          <Stat label='Stock priced' value={`${qty(data.stock.priced_quantity, units)} (${qty(data.stock.unpriced_quantity, units)} unpriced)`} />
        )}
      </SimpleGrid>

      {data.rolled && (
        <>
          {!data.rolled.complete && (
            <Alert color='orange'>Some components have no price; the unit cost is understated.</Alert>
          )}
          <Table striped withTableBorder verticalSpacing={4} fz='sm'>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Component</Table.Th>
                <Table.Th>Qty / unit</Table.Th>
                <Table.Th>Unit cost</Table.Th>
                <Table.Th>Basis</Table.Th>
                <Table.Th>Extended</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {flatten(data.rolled).map(({ node, depth }, i) => (
                <Table.Tr key={`${node.part}-${i}`}>
                  <Table.Td style={{ paddingLeft: 8 + depth * 18 }}>
                    <PartLink context={context} part={node.part} name={node.name} />
                  </Table.Td>
                  <Table.Td>{qty(node.bom_quantity, node.units)}</Table.Td>
                  <Table.Td>{money(node.unit_cost, cur, 4)}</Table.Td>
                  <Table.Td>
                    <SourceBadge source={node.source} />
                  </Table.Td>
                  <Table.Td>
                    <Text size='sm' fw={depth === 0 ? 600 : 400}>
                      {money(node.extended, cur, 4)}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </>
      )}

      {data.history.length > 0 && (
        <>
          <Divider label='Purchase history (landed)' labelPosition='left' />
          <Table striped withTableBorder verticalSpacing='xs'>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Order</Table.Th>
                <Table.Th>Date</Table.Th>
                <Table.Th>Received</Table.Th>
                <Table.Th>Invoice / unit</Table.Th>
                <Table.Th>Fees / unit</Table.Th>
                <Table.Th>Landed / unit</Table.Th>
                <Table.Th>Uplift</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.history.map((h) => (
                <Table.Tr key={`${h.order}-${h.received_native}-${h.net_unit}`}>
                  <Table.Td>
                    <Group gap={6}>
                      <OrderLink context={context} order={h.order} reference={h.reference} />
                      <StatusBadge status={h.status} />
                    </Group>
                    <Text size='xs' c='dimmed'>
                      {h.supplier}
                    </Text>
                  </Table.Td>
                  <Table.Td>{h.date ?? '—'}</Table.Td>
                  <Table.Td>{qty(h.received_native, units)}</Table.Td>
                  <Table.Td>{money(h.net_unit, cur, 4)}</Table.Td>
                  <Table.Td>{money(h.fee_unit, cur, 4)}</Table.Td>
                  <Table.Td>
                    <Text size='sm' fw={600}>
                      {money(h.landed_unit, cur, 4)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <UpliftBadge value={h.uplift_percent} />
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </>
      )}

      {!data.rolled && data.history.length === 0 && (
        <Text size='sm' c='dimmed'>
          No purchase order receipts for this part yet.
        </Text>
      )}
    </Stack>
  );
}

/* ======================================================================== exports */

export function RenderOrderPanel(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <OrderPanel context={context} />;
}

export function RenderCapacityPanel(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <CapacityPanel context={context} />;
}

export function RenderPartCostPanel(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <PartCostPanel context={context} />;
}
