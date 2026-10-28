import { Alert, Badge, Center, Loader, Text, Tooltip } from '@mantine/core';
import { useCallback, useEffect, useState } from 'react';

import type { InvenTreePluginContext } from '@inventreedb/ui';

export const BASE = '/plugin/landed-cost-capacity';

/** Fetch JSON from a plugin endpoint, with reload support. */
export function usePluginData<T>(context: InvenTreePluginContext, url: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  const reload = useCallback(() => {
    if (!url) return;
    setLoading(true);
    setError(null);
    context.api
      .get(url)
      .then((res) => setData(res.data))
      .catch((err) => setError(errorText(err)))
      .finally(() => setLoading(false));
  }, [context.api, url]);

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, setData, error, loading, reload };
}

export function errorText(err: any): string {
  const data = err?.response?.data;
  if (data && typeof data === 'object') {
    return Object.entries(data)
      .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : typeof v === 'object' ? JSON.stringify(v) : v}`)
      .join('; ');
  }
  return err?.message ?? String(err);
}

export function num(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function money(value: string | number | null | undefined, currency = 'EUR', digits = 2): string {
  const n = num(value);
  if (n === null) return '—';
  const maxDigits = Math.abs(n) > 0 && Math.abs(n) < 1 ? Math.max(digits, 4) : digits;
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency,
    minimumFractionDigits: Math.min(2, maxDigits),
    maximumFractionDigits: maxDigits,
  }).format(n);
}

export function qty(value: string | number | null | undefined, units = ''): string {
  const n = num(value);
  if (n === null) return '—';
  const s = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(n);
  return units && units !== 'pcs' ? `${s} ${units}` : s;
}

export function pct(value: string | number | null | undefined, digits = 1): string {
  const n = num(value);
  if (n === null) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

export function UpliftBadge({ value }: { value: string | number | null | undefined }) {
  const n = num(value);
  if (n === null) return <Text c='dimmed'>—</Text>;
  const color = n < 5 ? 'green' : n < 20 ? 'yellow' : n < 50 ? 'orange' : 'red';
  return (
    <Badge color={color} variant='light'>
      {pct(n)}
    </Badge>
  );
}

const STATUS: Record<string, { color: string; label: string; tip: string }> = {
  applied: { color: 'green', label: 'Applied', tip: 'Landed cost is written into the stock purchase prices' },
  stale: { color: 'orange', label: 'Out of date', tip: 'Order lines or fees changed since landed cost was applied' },
  not_applied: { color: 'gray', label: 'Not applied', tip: 'Stock purchase prices are the invoice prices' },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? STATUS.not_applied;
  return (
    <Tooltip label={s.tip}>
      <Badge color={s.color}>{s.label}</Badge>
    </Tooltip>
  );
}

const SOURCE: Record<string, { label: string; color: string; tip: string }> = {
  stock_landed: { label: 'Stock (landed)', color: 'green', tip: 'Weighted average landed cost of stock on hand' },
  stock: { label: 'Stock', color: 'blue', tip: 'Weighted average purchase price of stock on hand (no PO fees known)' },
  last_purchase: { label: 'Last purchase', color: 'cyan', tip: 'Landed cost of the most recent purchase (no stock on hand)' },
  supplier_estimate: { label: 'Estimate', color: 'yellow', tip: 'Primary supplier price break plus the supplier’s typical fee uplift' },
  missing: { label: 'No price', color: 'red', tip: 'No stock price, purchase history or supplier price' },
  bom: { label: 'BOM', color: 'grape', tip: 'Rolled up from the bill of materials' },
  bom_incomplete: { label: 'BOM (incomplete)', color: 'red', tip: 'Some components have no price' },
};

export function SourceBadge({ source }: { source: string }) {
  const s = SOURCE[source] ?? { label: source, color: 'gray', tip: '' };
  return (
    <Tooltip label={s.tip} disabled={!s.tip}>
      <Badge size='sm' variant='light' color={s.color}>
        {s.label}
      </Badge>
    </Tooltip>
  );
}

export function Loading({ loading, error }: { loading: boolean; error: string | null }) {
  if (error) {
    return (
      <Alert color='red' title='Error'>
        {error}
      </Alert>
    );
  }
  if (loading) {
    return (
      <Center p='md'>
        <Loader size='sm' />
      </Center>
    );
  }
  return null;
}

/** Link to an InvenTree page inside the SPA. */
export function PartLink({ context, part, name }: { context: InvenTreePluginContext; part: number; name: string }) {
  return (
    <Text
      component='a'
      href={`/web/part/${part}/`}
      size='sm'
      c={context.theme.primaryColor}
      style={{ cursor: 'pointer' }}
      onClick={(e: any) => {
        e.preventDefault();
        context.navigate(`/part/${part}/`);
      }}
    >
      {name}
    </Text>
  );
}

export function OrderLink({ context, order, reference }: { context: InvenTreePluginContext; order: number; reference: string }) {
  return (
    <Text
      component='a'
      href={`/web/purchasing/purchase-order/${order}/`}
      size='sm'
      c={context.theme.primaryColor}
      style={{ cursor: 'pointer' }}
      onClick={(e: any) => {
        e.preventDefault();
        context.navigate(`/purchasing/purchase-order/${order}/`);
      }}
    >
      {reference}
    </Text>
  );
}

export function downloadCsv(filename: string, rows: (string | number | null | undefined)[][]) {
  const csv = rows
    .map((r) => r.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(','))
    .join('\n');
  const blob = new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
