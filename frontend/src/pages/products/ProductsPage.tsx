import { Anchor, Badge, Button, Group, Paper, Select, Stack, Tabs, Text, TextInput, Title } from "@mantine/core";
import { useDebouncedValue, useDisclosure } from "@mantine/hooks";
import { IconFileImport, IconPlus, IconSearch } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useOffers, type Offer } from "../../api/offers";
import { useProducts, type Product } from "../../api/products";
import { useIsAdmin } from "../../auth/auth";
import { DataTable, type Column } from "../../components/DataTable";
import { formatMoney } from "../../format";
import { HistoryModal } from "../offers/HistoryModal";
import { AssignModal } from "../offers/AssignModal";
import { OfferActions, OfferMatchCell, OfferPriceCell, OfferUrlCell } from "../offers/OfferCells";
import { ImportModal } from "./ImportModal";
import { PRIORITY_LABELS } from "./labels";
import { ProductFormModal } from "./ProductFormModal";

const PAGE_SIZE = 25;

function ProductList() {
  const isAdmin = useIsAdmin();
  const [params, setParams] = useSearchParams();
  const [formOpened, form] = useDisclosure();
  const [importOpened, importer] = useDisclosure();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [debounced] = useDebouncedValue(search.trim(), 300);
  const priority = params.get("priority") ?? "";
  const market = params.get("market") ?? "";
  const sort = params.get("sort") ?? "name";
  const page = Number(params.get("page") ?? 1) || 1;

  function update(changes: Record<string, string | number | null>, resetPage = true) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [k, v] of Object.entries(changes)) {
        if (v === null || v === "" || (k === "sort" && v === "name")) next.delete(k); else next.set(k, String(v));
      }
      if (resetPage) next.delete("page");
      return next;
    }, { replace: true });
  }
  // Push the search text into the URL once the user stops typing.
  useEffect(() => { if (debounced !== (params.get("q") ?? "")) update({ q: debounced || null }); }, [debounced]);

  const list = useProducts({ q: params.get("q") ?? undefined, priority: priority || undefined,
                             market: market || undefined, sort, page, page_size: PAGE_SIZE });

  const columns: Column<Product>[] = [
    { key: "name", label: "Názov", sortKey: "name", render: (p) => (
      <Stack gap={0}>
        <Anchor component={Link} to={`/produkty/${p.id}`} fw={500} size="sm">{p.name}</Anchor>
        {(p.attributes.subject || p.attributes.grade) && (
          <Text size="xs" c="dimmed">{[p.attributes.subject, p.attributes.grade && `${p.attributes.grade}. ročník`,
                                        p.attributes.type].filter(Boolean).join(" · ")}</Text>
        )}
      </Stack>
    ) },
    { key: "ids", label: "ISBN / EAN / SKU", nowrap: true, render: (p) => (
      <Text size="sm" ff="monospace">{p.isbn ?? p.ean ?? p.sku ?? "—"}</Text>
    ) },
    { key: "prices", label: "Vlastné ceny", nowrap: true, render: (p) => p.prices.length === 0 ? "—" : (
      <Stack gap={0}>{p.prices.map((x) => (
        <Text key={x.market} size="sm"><Text span c="dimmed" size="xs">{x.market}</Text> {formatMoney(x.price, x.currency)}</Text>
      ))}</Stack>
    ) },
    { key: "priority", label: "Priorita", sortKey: "priority", nowrap: true, render: (p) => (
      <Badge variant="light" color={PRIORITY_LABELS[p.priority].color} style={{ minWidth: "max-content" }}>
        {PRIORITY_LABELS[p.priority].label}</Badge>
    ) },
    { key: "offers", label: "Ponuky", sortKey: "offers", align: "right", render: (p) => p.offer_count },
    { key: "tracking", label: "Sledovanie", nowrap: true, render: (p) => p.tracking_status === "active" ? "aktívne"
      : <Badge variant="light" color="yellow">pozastavené</Badge> },
  ];

  return (
    <Stack>
      <Group justify="space-between" align="flex-end">
        <Group align="flex-end">
          <TextInput label="Hľadať" placeholder="Názov, ISBN, EAN, SKU" leftSection={<IconSearch size={16} />} w={260}
                     value={search} onChange={(e) => setSearch(e.currentTarget.value)} />
          <Select label="Priorita" placeholder="Všetky" clearable w={170} value={priority || null}
                  data={Object.entries(PRIORITY_LABELS).map(([value, { label }]) => ({ value, label }))}
                  onChange={(v) => update({ priority: v })} />
          <Select label="Trh" placeholder="Všetky" clearable w={120} value={market || null} data={["SK", "CZ"]}
                  onChange={(v) => update({ market: v })} />
        </Group>
        {isAdmin && (
          <Group>
            <Button variant="default" leftSection={<IconFileImport size={16} />} onClick={importer.open}>Import CSV</Button>
            <Button leftSection={<IconPlus size={16} />} onClick={form.open}>Pridať produkt</Button>
          </Group>
        )}
      </Group>
      <DataTable columns={columns} rows={list.data?.items} rowKey={(p) => p.id} total={list.data?.total} page={page}
                 pageSize={PAGE_SIZE} sort={sort} onSortChange={(s) => update({ sort: s })}
                 onPageChange={(p) => update({ page: p }, false)} loading={list.isLoading} error={list.error}
                 emptyText={params.get("q") || priority || market ? "Žiadny produkt nezodpovedá filtrom."
                   : "Zatiaľ nie sú evidované žiadne vlastné produkty."} />
      <ProductFormModal opened={formOpened} onClose={form.close} />
      <ImportModal opened={importOpened} onClose={importer.close} />
    </Stack>
  );
}

function MatchingQueue() {
  const isAdmin = useIsAdmin();
  const [status, setStatus] = useState("pending_review");
  const [page, setPage] = useState(1);
  const [assigning, setAssigning] = useState<Offer | null>(null);
  const [history, setHistory] = useState<Offer | null>(null);
  const offers = useOffers({ match_status: status, page, page_size: PAGE_SIZE });

  const columns: Column<Offer>[] = [
    { key: "competitor", label: "Konkurent", nowrap: true, render: (o) => (
      <Anchor component={Link} to={`/konkurencia/${o.competitor_id}`} size="sm">{o.competitor_name}</Anchor>
    ) },
    { key: "url", label: "Ponuka", render: (o) => <OfferUrlCell offer={o} /> },
    { key: "price", label: "Cena", render: (o) => <OfferPriceCell offer={o} /> },
    { key: "match", label: "Stav párovania", render: (o) => <OfferMatchCell offer={o} /> },
    ...(isAdmin ? [{ key: "actions", label: "", align: "right" as const,
                     render: (o: Offer) => <OfferActions offer={o} onAssign={setAssigning} onHistory={setHistory} /> }] : []),
  ];

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        Ponuky, ktoré systém nevedel s istotou priradiť. Presná zhoda ISBN/EAN/SKU sa priraďuje automaticky,
        podobný názov iba navrhne kandidátov.
      </Text>
      <Select label="Zobraziť" w={220} allowDeselect={false} value={status}
              data={[{ value: "pending_review", label: "Čaká na kontrolu" }, { value: "unmatched", label: "Nepriradené" },
                     { value: "ignored", label: "Ignorované" }]}
              onChange={(v) => { setStatus(v ?? "pending_review"); setPage(1); }} />
      <DataTable columns={columns} rows={offers.data?.items} rowKey={(o) => o.id} total={offers.data?.total} page={page}
                 pageSize={PAGE_SIZE} onPageChange={setPage} loading={offers.isLoading} error={offers.error}
                 emptyText="Žiadne ponuky v tomto stave." />
      <AssignModal offer={assigning} onClose={() => setAssigning(null)} />
      <HistoryModal offer={history} onClose={() => setHistory(null)} />
    </Stack>
  );
}

export function ProductsPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "parovanie" ? "parovanie" : "produkty";
  const pending = useOffers({ match_status: "pending_review", page_size: 1 });

  return (
    <Stack>
      <Title order={1} size="h2">Produkty</Title>
      <Paper withBorder radius="md">
        <Tabs value={tab} onChange={(v) => setParams(v === "parovanie" ? { tab: "parovanie" } : {}, { replace: true })}>
          <Tabs.List>
            <Tabs.Tab value="produkty">Vlastné produkty</Tabs.Tab>
            <Tabs.Tab value="parovanie" rightSection={pending.data?.total ? (
              <Badge size="xs" color="orange" circle>{pending.data.total}</Badge>) : undefined}>
              Párovanie
            </Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="produkty" p="md"><ProductList /></Tabs.Panel>
          <Tabs.Panel value="parovanie" p="md"><MatchingQueue /></Tabs.Panel>
        </Tabs>
      </Paper>
    </Stack>
  );
}
