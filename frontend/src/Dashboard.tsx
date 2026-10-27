import { Badge, Group, ScrollArea, Table, Text, Tooltip } from '@mantine/core';

import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';

import { BASE, Loading, PartLink, money, qty, usePluginData } from './shared';

type Row = {
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

function DashboardItem({ context }: { context: InvenTreePluginContext }) {
  const cur = String(context.globalSettings?.getSetting?.('INVENTREE_DEFAULT_CURRENCY') || 'EUR');
  const { data, error, loading } = usePluginData<{ assemblies: Row[] }>(context, `${BASE}/dashboard/`);
  if (!data) return <Loading loading={loading} error={error} />;

  // Top-level products first, then sub-assemblies
  const rows = [...data.assemblies].sort(
    (a, b) => Number(a.used_in_other_assemblies) - Number(b.used_in_other_assemblies) || a.name.localeCompare(b.name),
  );

  if (rows.length === 0) return <Text c='dimmed'>No assemblies with a bill of materials.</Text>;

  return (
    <ScrollArea h='100%'>
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
            <Table.Th>Landed unit cost</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => (
            <Table.Tr key={r.part}>
              <Table.Td>
                <Group gap={6} wrap='nowrap'>
                  <PartLink context={context} part={r.part} name={r.name} />
                  {r.used_in_other_assemblies && (
                    <Badge size='xs' variant='light' color='grape'>
                      sub
                    </Badge>
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
                {r.limiting.length ? (
                  <Text size='xs' lineClamp={1}>
                    {r.limiting.map((l) => l.name).join(', ')}
                  </Text>
                ) : (
                  '—'
                )}
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
    </ScrollArea>
  );
}

export function RenderDashboardItem(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <DashboardItem context={context} />;
}
