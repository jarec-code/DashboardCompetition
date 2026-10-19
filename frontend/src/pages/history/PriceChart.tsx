import { Box, Group, Paper, Stack, Text, useComputedColorScheme } from "@mantine/core";
import { useMemo } from "react";
import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

import type { HistoryChart, HistorySeries } from "../../api/history";
import { formatMoney } from "../../format";
import { INK, seriesColor } from "./chartTheme";

type Row = { t: number } & Record<string, number | null | string[] | undefined>;

const key = (s: HistorySeries) => `s${s.offer_id}`;
const day = (t: number) => new Date(t).toLocaleDateString("sk-SK", { day: "numeric", month: "numeric", year: "2-digit" });

/** One row per moment any series changed; each series carries its price forward (a price holds until it changes). */
function buildRows(series: HistorySeries[]): Row[] {
  const times = [...new Set(series.flatMap((s) => s.points.map((p) => new Date(p.t).getTime())))].sort((a, b) => a - b);
  return times.map((t) => {
    const row: Row = { t };
    for (const s of series) {
      let value: number | null = null;
      let changed: string[] | undefined;
      for (const p of s.points) {
        const pt = new Date(p.t).getTime();
        if (pt > t) break;
        value = p.price === null ? null : Number(p.price);
        changed = pt === t && !p.synthetic ? p.change_types : undefined;
      }
      row[key(s)] = value;
      row[`${key(s)}_change`] = changed;
    }
    return row;
  });
}

interface TooltipProps {
  active?: boolean;
  label?: number;
  payload?: { dataKey: string; value: number | null }[];
  series: HistorySeries[];
  currency: string | null;
  own: number | null;
  scheme: "light" | "dark";
}

/** One tooltip listing every series at the hovered date (the pointer never has to hit a line). */
function ChartTooltip({ active, label, payload, series, currency, own, scheme }: TooltipProps) {
  if (!active || label === undefined || !payload) return null;
  return (
    <Paper withBorder shadow="sm" p="xs" radius="sm">
      <Text size="xs" fw={600} mb={4}>{new Date(label).toLocaleString("sk-SK", { dateStyle: "medium", timeStyle: "short" })}</Text>
      <Stack gap={2}>
        {series.map((s) => {
          const value = payload.find((p) => p.dataKey === key(s))?.value;
          return (
            <Group key={s.offer_id} gap={6} wrap="nowrap" justify="space-between">
              <Group gap={6} wrap="nowrap">
                <Box w={10} h={3} style={{ background: seriesColor(s.slot, scheme), borderRadius: 2 }} />
                <Text size="xs">{s.competitor_name}</Text>
              </Group>
              <Text size="xs" fw={600}>{value === null || value === undefined ? "—" : formatMoney(value, currency)}</Text>
            </Group>
          );
        })}
        {own !== null && (
          <Group gap={6} wrap="nowrap" justify="space-between">
            <Text size="xs" c="dimmed">Vaša cena</Text>
            <Text size="xs" c="dimmed">{formatMoney(own, currency)}</Text>
          </Group>
        )}
      </Stack>
    </Paper>
  );
}

export function PriceChart({ chart }: { chart: HistoryChart }) {
  const scheme = useComputedColorScheme("light");
  const ink = INK[scheme];
  const plotted = chart.series.filter((s) => s.plottable && s.points.length > 0);
  const rows = useMemo(() => buildRows(plotted), [chart]);
  const own = chart.own_price === null ? null : Number(chart.own_price);
  const directLabels = plotted.length <= 4;
  const to = new Date(chart.period_to).getTime();
  // Start the axis at the first record when tracking began later than the selected period
  // (no empty strip on the left); very short histories get a little room to stay readable.
  const firstPoint = rows.length ? rows[0].t : new Date(chart.period_from).getTime();
  const padded = firstPoint - Math.max((to - firstPoint) * 0.05, 60_000);
  const from = Math.max(new Date(chart.period_from).getTime(), Math.min(padded, to - 300_000));
  const span = to - from;
  const tickFormat = span < 3_600_000
    ? (t: number) => new Date(t).toLocaleTimeString("sk-SK", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : span < 2 * 86_400_000
      ? (t: number) => new Date(t).toLocaleTimeString("sk-SK", { hour: "2-digit", minute: "2-digit" }) : day;

  if (plotted.length === 0) {
    return <Text size="sm" c="dimmed" p="md">V zvolenom období nie sú pre tento trh žiadne zaznamenané ceny.</Text>;
  }

  return (
    <Stack gap="xs">
      <Group gap="md" aria-label="Legenda">
        {plotted.map((s) => (
          <Group key={s.offer_id} gap={6} wrap="nowrap">
            <Box w={16} h={3} style={{ background: seriesColor(s.slot, scheme), borderRadius: 2 }} />
            <Text size="sm">{s.competitor_name}</Text>
          </Group>
        ))}
        {own !== null && (
          <Group gap={6} wrap="nowrap">
            <Box w={16} style={{ borderTop: `2px dashed ${ink.secondary}` }} />
            <Text size="sm" c="dimmed">Vaša cena</Text>
          </Group>
        )}
      </Group>
      <Box h={340} role="img" aria-label={`Vývoj ceny produktu ${chart.product_name} u konkurentov, ${chart.market}`}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 12, right: directLabels ? 110 : 24, bottom: 4, left: 8 }}>
            <CartesianGrid vertical={false} stroke={ink.grid} />
            <XAxis dataKey="t" type="number" scale="time" domain={[from, to]} tickFormatter={tickFormat}
                   stroke={ink.secondary} tick={{ fill: ink.secondary, fontSize: 12 }} tickLine={false} />
            <YAxis tickFormatter={(v: number) => formatMoney(v, chart.axis_currency)} width={92} domain={["auto", "auto"]}
                   stroke={ink.secondary} tick={{ fill: ink.secondary, fontSize: 12 }} tickLine={false} axisLine={false} />
            <Tooltip cursor={{ stroke: ink.secondary, strokeWidth: 1 }}
                     content={<ChartTooltip series={plotted} currency={chart.axis_currency} own={own} scheme={scheme} />} />
            {own !== null && (
              <ReferenceLine y={own} stroke={ink.secondary} strokeDasharray="6 4" strokeWidth={1.5} ifOverflow="extendDomain"
                             label={{ value: "Vaša cena", position: "insideTopLeft", fill: ink.secondary, fontSize: 12 }} />
            )}
            {plotted.map((s) => {
              const color = seriesColor(s.slot, scheme);
              const lastIndex = rows.length - 1;
              return (
                <Line key={s.offer_id} type="stepAfter" dataKey={key(s)} name={s.competitor_name} stroke={color}
                      strokeWidth={2} isAnimationActive={false} connectNulls={false}
                      activeDot={{ r: 5, stroke: ink.surface, strokeWidth: 2 }}
                      dot={(props: { cx?: number; cy?: number; index?: number; payload?: Row }) => {
                        const changed = props.payload?.[`${key(s)}_change`];
                        if (!changed || props.cx === undefined || props.cy === undefined) {
                          return <g key={`${s.offer_id}-${props.index}`} />;
                        }
                        return <circle key={`${s.offer_id}-${props.index}`} className="change-dot" cx={props.cx} cy={props.cy} r={4}
                                       fill={color} stroke={ink.surface} strokeWidth={2} />;
                      }}
                      label={directLabels ? (props: { x?: number | string; y?: number | string; index?: number }) => (
                        props.index === lastIndex && props.x !== undefined && props.y !== undefined
                          ? <text key="label" x={Number(props.x) + 8} y={Number(props.y)} dy={4} fill={ink.primary}
                                  fontSize={12}>{s.competitor_name}</text>
                          : <g key={`l${props.index}`} />
                      ) : undefined} />
              );
            })}
          </LineChart>
        </ResponsiveContainer>
      </Box>
    </Stack>
  );
}
