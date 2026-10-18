import { Anchor, Badge, Button, Group, Paper, Select, SimpleGrid, Stack, Text, TextInput, Title } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconDownload, IconSearch } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { comparisonExportUrl, useComparison, type ComparisonRow } from "../../api/comparison";
import { DataTable, type Column } from "../../components/DataTable";
import { StatTile } from "../../components/StatTile";
import { formatMoney } from "../../format";
import { PRIORITY_LABELS } from "../products/labels";
import { PriceCell } from "./PriceCell";

const PAGE_SIZE = 25;
const POSITION_OPTIONS = [
  { value: "not_cheapest", label: "Konkurent je lacnejší" },
  { value: "cheapest", label: "Sme najlacnejší" },
  { value: "unknown", label: "Bez porovnania" },
];
const SORT_OPTIONS = [
  { value: "name", label: "Podľa názvu" },
  { value: "undercut", label: "Najväčší rozdiel v náš neprospech" },
];
const VAT = { with_vat: "s DPH", without_vat: "bez DPH", unknown: "DPH neznáme" } as Record<string, string>;

export function ComparisonPage() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [debounced] = useDebouncedValue(search.trim(), 300);
  const market = params.get("market") ?? "";
  const competitor = params.get("competitor") ?? "";
  const position = params.get("position") ?? "";
  const sort = params.get("sort") ?? "name";
  const page = Number(params.get("page") ?? 1) || 1;

  function update(changes: Record<string, string | null>, resetPage = true) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [k, v] of Object.entries(changes)) {
        if (!v || (k === "sort" && v === "name")) next.delete(k); else next.set(k, v);
      }
      if (resetPage) next.delete("page");
      return next;
    }, { replace: true });
  }
  // Push the search text into the URL once the user stops typing.
  useEffect(() => { if (debounced !== (params.get("q") ?? "")) update({ q: debounced || null }); }, [debounced]);

  const request = { market: market || undefined, competitor_id: competitor ? Number(competitor) : undefined,
                    q: params.get("q") || undefined, position: position || undefined, sort, page,
                    page_size: PAGE_SIZE };
  const data = useComparison(request);
  const competitors = (data.data?.competitors ?? []).filter((c) => !competitor || String(c.id) === competitor);

  const columns: Column<ComparisonRow>[] = [
    { key: "product", label: "Produkt", render: (r) => (
      <Stack gap={2}>
        <Anchor component={Link} to={`/produkty/${r.product_id}`} fw={500} size="sm">{r.product_name}</Anchor>
        <Group gap={4}>
          <Badge size="xs" variant="outline" color="gray">{r.market}</Badge>
          <Badge size="xs" variant="light" color={PRIORITY_LABELS[r.priority as keyof typeof PRIORITY_LABELS]?.color}>
            {PRIORITY_LABELS[r.priority as keyof typeof PRIORITY_LABELS]?.label}</Badge>
        </Group>
      </Stack>
    ) },
    { key: "own", label: "Vlastná cena", nowrap: true, render: (r) => r.own_price === null
      ? <Text size="sm" c="dimmed">chýba pre {r.market}</Text>
      : (<Stack gap={0}><Text size="sm" fw={600}>{formatMoney(r.own_price, r.own_currency)}</Text>
          <Text size="xs" c="dimmed">{VAT[r.own_vat_mode ?? "unknown"]}</Text></Stack>) },
    { key: "range", label: "Najnižšia – najvyššia", nowrap: true, render: (r) => r.min_price === null
      ? <Text size="sm" c="dimmed">—</Text>
      : <Text size="sm">{formatMoney(r.min_price, r.own_currency)} – {formatMoney(r.max_price, r.own_currency)}</Text> },
    ...competitors.map((c) => ({
      key: `c${c.id}`, label: c.name,
      render: (r: ComparisonRow) => <PriceCell cell={r.offers.find((o) => o.competitor_id === c.id)} />,
    })),
  ];

  const summary = data.data?.summary;
  return (
    <Stack>
      <Group justify="space-between">
        <Title order={1} size="h2">Cenové porovnanie</Title>
        <Button component="a" href={comparisonExportUrl(request)} variant="default" leftSection={<IconDownload size={16} />}>
          Export CSV
        </Button>
      </Group>
      {summary && (
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <StatTile label="Sme najlacnejší" value={summary.cheapest} hint="žiadny porovnateľný konkurent nie je lacnejší" />
          <StatTile label="Konkurent je lacnejší" value={summary.not_cheapest}
                    hint="aspoň jeden konkurent má na rovnakom trhu nižšiu cenu" />
          <StatTile label="Bez porovnania" value={summary.unknown}
                    hint="chýba cena, vlastná cena pre trh, alebo sa líši mena či DPH" />
        </SimpleGrid>
      )}
      <Paper withBorder p="md" radius="md">
        <Group mb="md" align="flex-end">
          <TextInput label="Hľadať produkt" leftSection={<IconSearch size={16} />} w={240} value={search}
                     onChange={(e) => setSearch(e.currentTarget.value)} />
          <Select label="Trh" placeholder="Všetky" clearable w={120} value={market || null}
                  data={data.data?.markets ?? []} onChange={(v) => update({ market: v })} />
          <Select label="Konkurent" placeholder="Všetci" clearable w={200} value={competitor || null}
                  data={(data.data?.competitors ?? []).map((c) => ({ value: String(c.id), label: c.name }))}
                  onChange={(v) => update({ competitor: v })} />
          <Select label="Pozícia" placeholder="Všetky" clearable w={220} value={position || null} data={POSITION_OPTIONS}
                  onChange={(v) => update({ position: v })} />
          <Select label="Zoradiť" w={280} allowDeselect={false} value={sort} data={SORT_OPTIONS}
                  onChange={(v) => update({ sort: v })} />
        </Group>
        <DataTable columns={columns} rows={data.data?.items} rowKey={(r) => `${r.product_id}-${r.market}`}
                   total={data.data?.total} page={page} pageSize={PAGE_SIZE} loading={data.isLoading} error={data.error}
                   onPageChange={(p) => update({ page: String(p) }, false)} minWidth={520 + competitors.length * 220}
                   emptyText={params.toString() ? "Žiadny produkt nezodpovedá filtrom."
                     : "Na porovnanie treba priradiť ponuky konkurentov k vlastným produktom (Produkty → Párovanie)."} />
        <Text size="xs" c="dimmed" mt="sm">
          Porovnáva sa vždy cena konkurenta s vašou cenou pre jeho trh. Ceny v inej mene alebo s iným režimom DPH sa
          neprepočítavajú a do rozdielu ani do rozpätia sa nezapočítavajú.
        </Text>
      </Paper>
    </Stack>
  );
}
