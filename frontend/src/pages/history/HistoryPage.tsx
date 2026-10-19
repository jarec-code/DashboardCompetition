import {
  Alert, Anchor, Badge, Group, MultiSelect, Paper, SegmentedControl, Select, Stack, Table, Text, Title,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useHistoryChanges, useHistoryChart, type HistoryChange, type HistoryChart } from "../../api/history";
import { useProducts } from "../../api/products";
import { DataTable, type Column } from "../../components/DataTable";
import { formatDateTime, formatMoney, formatSignedPercent } from "../../format";
import { AVAILABILITY_LABELS } from "../products/labels";
import { PriceChart } from "./PriceChart";

const PERIODS = [
  { value: "30", label: "30 dní" }, { value: "90", label: "90 dní" }, { value: "365", label: "1 rok" },
  { value: "all", label: "Všetko" },
];
const CHANGE = {
  initial: "prvý záznam", price_up: "cena ↑", price_down: "cena ↓", currency: "mena", availability: "dostupnosť",
  original_price: "pôvodná cena",
} as Record<string, string>;

function periodStart(period: string): string {
  if (period === "all") return "2000-01-01T00:00:00Z";
  return new Date(Date.now() - Number(period) * 86_400_000).toISOString();
}

/** Table view of the chart (also the accessible alternative to the lines). */
function ChartTable({ chart, visible }: { chart: HistoryChart; visible: Set<number> }) {
  const rows = chart.series.filter((s) => visible.has(s.competitor_id))
    .flatMap((s) => s.points.filter((p) => !p.synthetic).map((p) => ({ s, p })))
    .sort((a, b) => b.p.t.localeCompare(a.p.t));
  if (!rows.length) return <Text size="sm" c="dimmed">V zvolenom období bez zmien.</Text>;
  return (
    <Table.ScrollContainer minWidth={560}>
      <Table striped verticalSpacing="xs">
        <Table.Thead><Table.Tr><Table.Th>Čas</Table.Th><Table.Th>Konkurent</Table.Th><Table.Th ta="right">Cena</Table.Th>
          <Table.Th>Dostupnosť</Table.Th><Table.Th>Zmena</Table.Th></Table.Tr></Table.Thead>
        <Table.Tbody>
          {rows.map(({ s, p }) => (
            <Table.Tr key={`${s.offer_id}-${p.t}`}>
              <Table.Td>{formatDateTime(p.t)}</Table.Td><Table.Td>{s.competitor_name}</Table.Td>
              <Table.Td ta="right">{formatMoney(p.price, s.currency)}</Table.Td>
              <Table.Td>{(AVAILABILITY_LABELS[p.availability] ?? AVAILABILITY_LABELS.unknown).label}</Table.Td>
              <Table.Td>{p.change_types.map((c) => CHANGE[c] ?? c).join(", ")}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function ChangesTable({ productId, from }: { productId: number | null; from: string }) {
  const [changeType, setChangeType] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const changes = useHistoryChanges({ product_id: productId ?? undefined, from, change_type: changeType ?? undefined, page });
  const columns: Column<HistoryChange>[] = [
    { key: "when", label: "Čas", nowrap: true, render: (c) => formatDateTime(c.observed_at) },
    { key: "product", label: "Produkt", render: (c) => c.product_id
      ? <Anchor component={Link} to={`/historia?product=${c.product_id}`} size="sm">{c.product_name}</Anchor>
      : <Text size="sm" c="dimmed">{c.offer_title ?? "nepriradená ponuka"}</Text> },
    { key: "competitor", label: "Konkurent", nowrap: true, render: (c) => (
      <Anchor component={Link} to={`/konkurencia/${c.competitor_id}`} size="sm">{c.competitor_name}</Anchor>) },
    { key: "price", label: "Cena", align: "right", nowrap: true, render: (c) => (
      <Stack gap={0}>
        <Text size="sm" fw={600}>{formatMoney(c.price, c.currency)}</Text>
        {c.previous_price && c.previous_price !== c.price && (
          <Text size="xs" c="dimmed">z {formatMoney(c.previous_price, c.currency)} ({formatSignedPercent(
            (Number(c.price) - Number(c.previous_price)) / Number(c.previous_price) * 100)})</Text>)}
      </Stack>) },
    { key: "change", label: "Zmena", render: (c) => (
      <Group gap={4}>{c.change_types.map((t) => <Badge key={t} size="xs" variant="light"
        color={t === "price_down" ? "red" : t === "price_up" ? "green" : "gray"}>{CHANGE[t] ?? t}</Badge>)}</Group>) },
  ];
  return (
    <Paper withBorder p="md" radius="md">
      <Group justify="space-between" mb="sm">
        <Title order={2} size="h4">Zaznamenané zmeny{productId ? "" : " všetkých produktov"}</Title>
        <Select w={220} placeholder="Všetky zmeny" clearable value={changeType} aria-label="Druh zmeny"
                data={Object.entries(CHANGE).map(([value, label]) => ({ value, label }))}
                onChange={(v) => { setChangeType(v); setPage(1); }} />
      </Group>
      <DataTable columns={columns} rows={changes.data?.items} rowKey={(c) => c.id} total={changes.data?.total} page={page}
                 pageSize={25} onPageChange={setPage} loading={changes.isLoading} error={changes.error} minWidth={760}
                 emptyText="V zvolenom období nie sú zaznamenané žiadne zmeny." />
    </Paper>
  );
}

export function HistoryPage() {
  const [params, setParams] = useSearchParams();
  const productId = params.get("product") ? Number(params.get("product")) : null;
  const period = params.get("period") ?? "90";
  const [view, setView] = useState("chart");
  const [search, setSearch] = useState("");
  const [debounced] = useDebouncedValue(search, 300);
  const [hidden, setHidden] = useState<string[] | null>(null);

  const from = useMemo(() => periodStart(period), [period]);
  const products = useProducts({ q: debounced || undefined, page_size: 50 });
  const chart = useHistoryChart({ product_id: productId, market: params.get("market") ?? undefined, from });

  const set = (changes: Record<string, string | null>) => setParams((current) => {
    const next = new URLSearchParams(current);
    for (const [k, v] of Object.entries(changes)) { if (v) next.set(k, v); else next.delete(k); }
    return next;
  }, { replace: true });

  const productOptions = (products.data?.items ?? []).map((p) => ({ value: String(p.id), label: p.name }));
  if (chart.data && productId && !productOptions.some((o) => o.value === String(productId))) {
    productOptions.unshift({ value: String(productId), label: chart.data.product_name });
  }
  const competitors = chart.data?.series.map((s) => ({ value: String(s.competitor_id), label: s.competitor_name })) ?? [];
  const selected = hidden ?? competitors.map((c) => c.value);
  const visible = new Set(selected.map(Number));
  const shownChart = chart.data && { ...chart.data, series: chart.data.series.filter((s) => visible.has(s.competitor_id)) };
  const excluded = chart.data?.series.filter((s) => !s.plottable) ?? [];

  return (
    <Stack>
      <Title order={1} size="h2">Cenová história</Title>
      <Paper withBorder p="md" radius="md">
        <Group align="flex-end" mb="md">
          <Select label="Produkt" placeholder="Vyhľadajte produkt" searchable w={360} value={productId ? String(productId) : null}
                  data={productOptions} searchValue={search} onSearchChange={setSearch} nothingFoundMessage="Nič sa nenašlo"
                  onChange={(v) => { setHidden(null); set({ product: v, market: null }); }} />
          <Select label="Trh" w={110} allowDeselect={false} disabled={!chart.data?.markets.length}
                  value={chart.data?.market ?? null} data={chart.data?.markets ?? []}
                  onChange={(v) => { setHidden(null); set({ market: v }); }} />
          <div>
            <Text size="sm" fw={500} mb={4}>Obdobie</Text>
            <SegmentedControl data={PERIODS} value={period} onChange={(v) => set({ period: v === "90" ? null : v })} />
          </div>
          {competitors.length > 1 && (
            <MultiSelect label="Konkurenti" w={300} data={competitors} value={selected} onChange={setHidden}
                         placeholder="Žiadny" />
          )}
        </Group>

        {!productId && <Text size="sm" c="dimmed">Vyberte produkt; graf ukáže vývoj cien jeho konkurenčných ponúk.</Text>}
        {chart.error && <Alert color="red">{chart.error.message}</Alert>}
        {productId && shownChart && (
          <Stack>
            <Group justify="space-between">
              <Text size="sm" c="dimmed">
                {shownChart.product_name} · trh {shownChart.market ?? "—"}
                {shownChart.own_price === null ? " · vlastná cena pre tento trh nie je zadaná" : ""}
              </Text>
              <SegmentedControl size="xs" value={view} onChange={setView}
                                data={[{ value: "chart", label: "Graf" }, { value: "table", label: "Tabuľka" }]} />
            </Group>
            {view === "chart" ? <PriceChart chart={shownChart} /> : <ChartTable chart={shownChart} visible={visible} />}
            {excluded.length > 0 && (
              <Text size="xs" c="dimmed">
                Mimo grafu (iná mena, ceny sa neprepočítavajú): {excluded.map((s) => `${s.competitor_name} (${s.currency})`).join(", ")}
              </Text>
            )}
          </Stack>
        )}
      </Paper>
      <ChangesTable productId={productId} from={from} />
    </Stack>
  );
}
