import {
  Accordion,
  ActionIcon,
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  NumberInput,
  Select,
  Stack,
  Switch,
  Table,
  Tabs,
  Text,
  TextInput,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';

import type { InvenTreePluginContext } from '@inventreedb/ui';

import { BASE, Loading, StatusBadge, UpliftBadge, errorText, money, qty, usePluginData } from './shared';

const CATEGORY_LABELS: Record<string, string> = {
  shipping: 'Shipping',
  duty: 'Duty / customs',
  import_vat: 'Import VAT',
  other: 'Other fee',
  excluded: 'Not a product cost',
};

type BackfillOrder = {
  order: number;
  reference: string;
  supplier: string;
  currency: string;
  goods_total: string;
  fees_included: string;
  fees_excluded: string;
  uplift_percent: string | null;
  status: string;
  warnings: string[];
  lines: {
    part_name: string;
    sku: string;
    units: string;
    net_unit_price_native: string | null;
    fee_per_native_unit: string;
    landed_unit_price_native: string | null;
    uplift_percent: string | null;
  }[];
  fees: { reference: string; amount: string; category: string; included: boolean }[];
};

function Backfill({ context }: { context: InvenTreePluginContext }) {
  const { data, setData, error, loading, reload } = usePluginData<{ orders: BackfillOrder[] }>(context, `${BASE}/backfill/`);
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);

  if (!data) return <Loading loading={loading} error={error} />;
  const pending = data.orders.filter((o) => o.status !== 'applied');

  const apply = (ids: number[]) => {
    setBusy(true);
    context.api
      .post(`${BASE}/backfill/`, { orders: ids })
      .then((res) => {
        const updated: Record<number, BackfillOrder> = {};
        res.data.orders.forEach((o: BackfillOrder) => (updated[o.order] = o));
        setData({ orders: data.orders.map((o) => updated[o.order] ?? o) });
        setSelected([]);
        notifications.show({ title: 'Landed cost', message: `Applied to ${ids.length} order(s)`, color: 'green' });
      })
      .catch((err) => notifications.show({ title: 'Landed cost', message: errorText(err), color: 'red' }))
      .finally(() => setBusy(false));
  };

  return (
    <Stack gap='sm'>
      <Text size='sm'>
        Preview of the landed cost for every purchase order with received goods. Nothing is changed until you apply.
        Each order can be reverted later from its <b>Landed Cost</b> panel.
      </Text>
      <Group>
        <Button disabled={!selected.length} loading={busy} onClick={() => apply(selected)}>
          Apply selected ({selected.length})
        </Button>
        <Button variant='outline' disabled={!pending.length} loading={busy} onClick={() => apply(pending.map((o) => o.order))}>
          Apply all not yet applied ({pending.length})
        </Button>
        <Button variant='subtle' onClick={reload} loading={loading}>
          Refresh
        </Button>
      </Group>
      <Accordion multiple variant='contained'>
        {data.orders.map((o) => (
          <Accordion.Item key={o.order} value={String(o.order)}>
            <Group wrap='nowrap' pl='sm'>
              <Checkbox
                checked={selected.includes(o.order)}
                disabled={o.status === 'applied'}
                onChange={(e) =>
                  setSelected(e.currentTarget.checked ? [...selected, o.order] : selected.filter((x) => x !== o.order))
                }
              />
              <Accordion.Control>
                <Group justify='space-between' wrap='nowrap'>
                  <Group gap='xs'>
                    <Text fw={600}>{o.reference}</Text>
                    <Text size='sm' c='dimmed'>
                      {o.supplier}
                    </Text>
                    <StatusBadge status={o.status} />
                  </Group>
                  <Group gap='md'>
                    <Text size='sm'>
                      {money(o.goods_total, o.currency)} + {money(o.fees_included, o.currency)} fees
                    </Text>
                    <UpliftBadge value={o.uplift_percent} />
                  </Group>
                </Group>
              </Accordion.Control>
            </Group>
            <Accordion.Panel>
              {o.warnings.map((w) => (
                <Alert key={w} color='orange' mb='xs'>
                  {w}
                </Alert>
              ))}
              <Group gap='xs' mb='xs'>
                {o.fees.map((f, i) => (
                  <Badge key={i} variant='light' color={f.included ? 'blue' : 'gray'}>
                    {f.reference || 'Fee'}: {money(f.amount, o.currency)} · {CATEGORY_LABELS[f.category] ?? f.category}
                    {f.included ? '' : ' (not counted)'}
                  </Badge>
                ))}
              </Group>
              <Table fz='sm' verticalSpacing={4} withTableBorder>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Part</Table.Th>
                    <Table.Th>Invoice / unit</Table.Th>
                    <Table.Th>Fees / unit</Table.Th>
                    <Table.Th>Landed / unit</Table.Th>
                    <Table.Th>Uplift</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {o.lines.map((l, i) => (
                    <Table.Tr key={i}>
                      <Table.Td>
                        {l.part_name}
                        <Text size='xs' c='dimmed'>
                          {l.sku}
                        </Text>
                      </Table.Td>
                      <Table.Td>{money(l.net_unit_price_native, o.currency, 4)}</Table.Td>
                      <Table.Td>{money(l.fee_per_native_unit, o.currency, 4)}</Table.Td>
                      <Table.Td>
                        <b>{money(l.landed_unit_price_native, o.currency, 4)}</b>
                        {l.units && l.units !== 'pcs' ? ` / ${l.units}` : ''}
                      </Table.Td>
                      <Table.Td>
                        <UpliftBadge value={l.uplift_percent} />
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Accordion.Panel>
          </Accordion.Item>
        ))}
      </Accordion>
    </Stack>
  );
}

type Rule = { pk?: number; priority: number; pattern: string; category: string; active: boolean };

function Rules({ context }: { context: InvenTreePluginContext }) {
  const { data, error, loading, reload } = usePluginData<{ categories: string[]; rules: Rule[] }>(context, `${BASE}/rules/`);
  const [draft, setDraft] = useState<Rule>({ priority: 100, pattern: '', category: 'shipping', active: true });

  if (!data) return <Loading loading={loading} error={error} />;
  const categories = data.categories.map((c) => ({ value: c, label: CATEGORY_LABELS[c] ?? c }));

  const call = (p: Promise<any>, msg: string) =>
    p
      .then(() => {
        notifications.show({ title: 'Fee rules', message: msg, color: 'green' });
        reload();
      })
      .catch((err) => notifications.show({ title: 'Fee rules', message: errorText(err), color: 'red' }));

  const update = (rule: Rule, patch: Partial<Rule>) => call(context.api.patch(`${BASE}/rules/${rule.pk}/`, patch), 'Rule updated');

  return (
    <Stack gap='sm'>
      <Text size='sm'>
        Extra lines are classified by matching these regular expressions (case-insensitive) against the line&apos;s
        reference and description, lowest priority number first. Unmatched lines count as <b>Other fee</b>. You can
        always override the type on an individual order.
      </Text>
      <Table withTableBorder verticalSpacing={4}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th w={90}>Priority</Table.Th>
            <Table.Th>Pattern</Table.Th>
            <Table.Th w={200}>Type</Table.Th>
            <Table.Th w={70}>Active</Table.Th>
            <Table.Th w={40} />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {data.rules.map((r) => (
            <Table.Tr key={r.pk}>
              <Table.Td>
                <NumberInput size='xs' min={0} defaultValue={r.priority} onBlur={(e) => update(r, { priority: Number(e.currentTarget.value) })} />
              </Table.Td>
              <Table.Td>
                <TextInput size='xs' defaultValue={r.pattern} onBlur={(e) => e.currentTarget.value !== r.pattern && update(r, { pattern: e.currentTarget.value })} />
              </Table.Td>
              <Table.Td>
                <Select size='xs' data={categories} value={r.category} allowDeselect={false} onChange={(v) => v && update(r, { category: v })} />
              </Table.Td>
              <Table.Td>
                <Switch checked={r.active} onChange={(e) => update(r, { active: e.currentTarget.checked })} />
              </Table.Td>
              <Table.Td>
                <ActionIcon color='red' variant='subtle' onClick={() => call(context.api.delete(`${BASE}/rules/${r.pk}/`), 'Rule deleted')}>
                  ✕
                </ActionIcon>
              </Table.Td>
            </Table.Tr>
          ))}
          <Table.Tr>
            <Table.Td>
              <NumberInput size='xs' min={0} value={draft.priority} onChange={(v) => setDraft({ ...draft, priority: Number(v) })} />
            </Table.Td>
            <Table.Td>
              <TextInput size='xs' placeholder='e.g. dhl|ups|fedex' value={draft.pattern} onChange={(e) => setDraft({ ...draft, pattern: e.currentTarget.value })} />
            </Table.Td>
            <Table.Td>
              <Select size='xs' data={categories} value={draft.category} allowDeselect={false} onChange={(v) => v && setDraft({ ...draft, category: v })} />
            </Table.Td>
            <Table.Td colSpan={2}>
              <Button
                size='xs'
                disabled={!draft.pattern}
                onClick={() =>
                  call(context.api.post(`${BASE}/rules/`, draft), 'Rule added').then(() => setDraft({ ...draft, pattern: '' }))
                }
              >
                Add
              </Button>
            </Table.Td>
          </Table.Tr>
        </Table.Tbody>
      </Table>
    </Stack>
  );
}

function Suppliers({ context }: { context: InvenTreePluginContext }) {
  const { data, error, loading } = usePluginData<{ currency: string; suppliers: { supplier: number; name: string; uplift_percent: string }[] }>(
    context,
    `${BASE}/suppliers/`,
  );
  if (!data) return <Loading loading={loading} error={error} />;
  return (
    <Stack gap='sm'>
      <Text size='sm'>
        Average share of fees on top of the goods value across each supplier&apos;s received orders. Used to estimate the
        landed cost of future purchases in the production plan.
      </Text>
      <Table withTableBorder striped verticalSpacing={4}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Supplier</Table.Th>
            <Table.Th>Typical fee uplift</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {data.suppliers.map((s) => (
            <Table.Tr key={s.supplier}>
              <Table.Td>{s.name}</Table.Td>
              <Table.Td>
                <UpliftBadge value={s.uplift_percent} />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {data.suppliers.length === 0 && <Text c='dimmed'>{qty(0)} suppliers with received orders.</Text>}
    </Stack>
  );
}

function PluginSettingsDisplay({ context }: { context: InvenTreePluginContext }) {
  return (
    <Tabs defaultValue='backfill' keepMounted={false}>
      <Tabs.List>
        <Tabs.Tab value='backfill'>Existing orders</Tabs.Tab>
        <Tabs.Tab value='rules'>Fee rules</Tabs.Tab>
        <Tabs.Tab value='suppliers'>Supplier fee uplift</Tabs.Tab>
      </Tabs.List>
      <Tabs.Panel value='backfill' pt='sm'>
        <Backfill context={context} />
      </Tabs.Panel>
      <Tabs.Panel value='rules' pt='sm'>
        <Rules context={context} />
      </Tabs.Panel>
      <Tabs.Panel value='suppliers' pt='sm'>
        <Suppliers context={context} />
      </Tabs.Panel>
    </Tabs>
  );
}

export function RenderPluginSettings(context: InvenTreePluginContext) {
  return <PluginSettingsDisplay context={context} />;
}
