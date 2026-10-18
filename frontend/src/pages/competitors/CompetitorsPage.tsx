import { Anchor, Badge, Button, Group, Paper, Select, Stack, Text, TextInput, Title } from "@mantine/core";
import { useDebouncedValue, useDisclosure } from "@mantine/hooks";
import { IconPlus, IconSearch } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useCompetitorFilters, useCompetitors, type CompetitorListItem } from "../../api/competitors";
import { useIsAdmin } from "../../auth/auth";
import { DataTable, type Column } from "../../components/DataTable";
import { formatDateTime, formatFrequency } from "../../format";
import { CompetitorFormModal } from "./CompetitorFormModal";
import { STATUS_LABELS, TYPE_LABELS } from "./labels";
import { StatusActions } from "./StatusActions";

const PAGE_SIZE = 25;
const STATUS_FILTERS = [
  { value: "", label: "Aktívni a pozastavení" },
  { value: "active", label: "Aktívny" },
  { value: "paused", label: "Pozastavený" },
  { value: "archived", label: "Archivovaný" },
  { value: "all", label: "Všetci" },
];

export function CompetitorsPage() {
  const isAdmin = useIsAdmin();
  const [params, setParams] = useSearchParams();
  const [formOpened, form] = useDisclosure();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [debouncedSearch] = useDebouncedValue(search, 300);

  const status = params.get("status") ?? "";
  const country = params.get("country") ?? "";
  const sort = params.get("sort") ?? "name";
  const page = Number(params.get("page") ?? 1) || 1;

  function update(changes: Record<string, string | number | null>, resetPage = true) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(changes)) {
        if (value === null || value === "" || (key === "sort" && value === "name")) next.delete(key);
        else next.set(key, String(value));
      }
      if (resetPage) next.delete("page");
      return next;
    }, { replace: true });
  }

  // Push the search text into the URL once the user stops typing (only the debounced value matters here).
  useEffect(() => {
    if (debouncedSearch.trim() !== (params.get("q") ?? "")) update({ q: debouncedSearch.trim() || null });
  }, [debouncedSearch]);

  const list = useCompetitors({ q: params.get("q") ?? undefined, status: status || undefined,
                                country: country || undefined, sort, page, page_size: PAGE_SIZE });
  const filters = useCompetitorFilters();

  const columns: Column<CompetitorListItem>[] = [
    { key: "name", label: "Názov", sortKey: "name", nowrap: true, render: (c) => (
      <Anchor component={Link} to={`/konkurencia/${c.id}`} fw={500}>{c.name}</Anchor>
    ) },
    { key: "domain", label: "Doména", sortKey: "domain", render: (c) => (
      <Anchor href={c.base_url} target="_blank" rel="noopener noreferrer" size="sm" c="dimmed">{c.domain}</Anchor>
    ) },
    { key: "country", label: "Krajina / trh", sortKey: "country", render: (c) =>
      c.country ? `${c.country}${c.market && c.market !== c.country ? ` / ${c.market}` : ""}` : "—" },
    { key: "type", label: "Typ", sortKey: "type", render: (c) => TYPE_LABELS[c.competitor_type] },
    { key: "offers", label: "Ponuky", sortKey: "offers", align: "right", render: (c) => c.offer_count },
    { key: "frequency", label: "Frekvencia", sortKey: "frequency", nowrap: true, render: (c) => formatFrequency(c.default_frequency_hours) },
    { key: "last_success", label: "Posledná úspešná kontrola", sortKey: "last_success", nowrap: true,
      render: (c) => formatDateTime(c.last_success_at) },
    { key: "errors", label: "Chyby", sortKey: "errors", align: "right", render: (c) =>
      c.last_run_error_count > 0 ? <Text c="red" fw={600} size="sm">{c.last_run_error_count}</Text> : 0 },
    { key: "status", label: "Stav", sortKey: "status", nowrap: true, render: (c) => (
      <Badge color={STATUS_LABELS[c.status].color} variant="light" style={{ minWidth: "max-content" }}>{STATUS_LABELS[c.status].label}</Badge>
    ) },
    ...(isAdmin ? [{ key: "actions", label: "Akcie", nowrap: true, render: (c: CompetitorListItem) => (
      <StatusActions id={c.id} name={c.name} status={c.status} compact />
    ) }] : []),
  ];

  const filtered = Boolean(params.get("q") || status || country);

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={1} size="h2">Konkurencia</Title>
        {isAdmin && <Button leftSection={<IconPlus size={16} />} onClick={form.open}>Pridať konkurenta</Button>}
      </Group>
      <Paper withBorder p="md" radius="md">
        <Group mb="md" align="flex-end">
          <TextInput label="Hľadať" placeholder="Názov alebo doména" leftSection={<IconSearch size={16} />}
                     value={search} onChange={(e) => setSearch(e.currentTarget.value)} w={260} />
          <Select label="Stav" data={STATUS_FILTERS} value={status} allowDeselect={false} w={210}
                  onChange={(v) => update({ status: v ?? "" })} />
          <Select label="Krajina" placeholder="Všetky" clearable w={140} value={country || null}
                  data={filters.data?.countries ?? []} onChange={(v) => update({ country: v })} />
        </Group>
        <DataTable columns={columns} rows={list.data?.items} rowKey={(c) => c.id} total={list.data?.total}
                   page={page} pageSize={PAGE_SIZE} sort={sort} loading={list.isLoading} error={list.error}
                   onSortChange={(s) => update({ sort: s })} onPageChange={(p) => update({ page: p }, false)}
                   minWidth={960}
                   emptyText={filtered ? "Žiadny konkurent nezodpovedá filtrom."
                     : "Zatiaľ nie sú evidovaní žiadni konkurenti."} />
      </Paper>
      <CompetitorFormModal opened={formOpened} onClose={form.close} />
    </Stack>
  );
}
