import {
  Alert, Badge, Button, Code, Collapse, Group, List, Paper, Select, Stack, Tabs, Text, Textarea, TextInput,
} from "@mantine/core";
import { useDebouncedValue, useDisclosure } from "@mantine/hooks";
import { IconFileImport, IconPlus, IconSearch } from "@tabler/icons-react";
import { useState } from "react";

import { useAddOffers, useOffers, type Offer } from "../../api/offers";
import { useIsAdmin } from "../../auth/auth";
import { DataTable, type Column } from "../../components/DataTable";
import { ImportWizard } from "../../components/ImportWizard";
import { MATCH_LABELS } from "../products/labels";
import { HistoryModal } from "./HistoryModal";
import { AssignModal } from "./AssignModal";
import { DiscoverLinks } from "./DiscoverLinks";
import { OfferActions, OfferMatchCell, OfferPriceCell, OfferUrlCell } from "./OfferCells";

const PAGE_SIZE = 25;
const MATCH_FILTERS = [{ value: "", label: "Všetky" },
  ...Object.entries(MATCH_LABELS).map(([value, { label }]) => ({ value, label }))];

function AddOffersForm({ competitorId, domain, onDone }: { competitorId: number; domain: string; onDone: () => void }) {
  const add = useAddOffers(competitorId);
  const [text, setText] = useState("");
  const [variant, setVariant] = useState("");
  const urls = text.split(/\s+/).map((u) => u.trim()).filter(Boolean);

  return (
    <Stack gap="xs">
      <Textarea label="URL produktových stránok" description={`Jedna na riadok, iba z domény ${domain}. Najviac 500 naraz.`}
                autosize minRows={3} maxRows={10} placeholder={`https://www.${domain}/produkt/1111`} value={text}
                onChange={(e) => setText(e.currentTarget.value)} />
      <Group align="flex-end">
        <TextInput label="Variant (voliteľné)" description="napr. tištěná; prázdne = predvolený" w={260} maxLength={100}
                   value={variant} onChange={(e) => setVariant(e.currentTarget.value)} />
        <Button disabled={urls.length === 0} loading={add.isPending}
                onClick={() => add.mutate({ urls, variant }, { onSuccess: (r) => { if (!r.invalid.length) setText(""); } })}>
          Pridať {urls.length > 0 ? `(${urls.length})` : ""}
        </Button>
        <Button variant="default" onClick={onDone}>Zavrieť</Button>
      </Group>
      {add.error && <Alert color="red">{add.error.message}</Alert>}
      {add.data && (
        <Alert color={add.data.invalid.length ? "yellow" : "green"}>
          <Text size="sm">Pridané: <b>{add.data.created.length}</b>, duplicity: <b>{add.data.duplicates.length}</b>,
            neplatné: <b>{add.data.invalid.length}</b></Text>
          {add.data.invalid.length > 0 && (
            <List size="xs" mt={4}>
              {add.data.invalid.map((i) => <List.Item key={i.url}>{i.url}: {i.reason}</List.Item>)}
            </List>
          )}
        </Alert>
      )}
    </Stack>
  );
}

export function OffersPanel({ competitorId, domain, archived }: { competitorId: number; domain: string; archived: boolean }) {
  const isAdmin = useIsAdmin();
  const [adding, addForm] = useDisclosure();
  const [importing, importer] = useDisclosure();
  const [assigning, setAssigning] = useState<Offer | null>(null);
  const [history, setHistory] = useState<Offer | null>(null);
  const [search, setSearch] = useState("");
  const [q] = useDebouncedValue(search.trim(), 300);
  const [matchStatus, setMatchStatus] = useState("");
  const [page, setPage] = useState(1);
  const offers = useOffers({ q: q || undefined, match_status: matchStatus || undefined, page, page_size: PAGE_SIZE },
                           competitorId);

  const columns: Column<Offer>[] = [
    { key: "url", label: "Produktová stránka", render: (o) => <OfferUrlCell offer={o} /> },
    { key: "price", label: "Cena a dostupnosť", render: (o) => <OfferPriceCell offer={o} /> },
    { key: "match", label: "Vlastný produkt", render: (o) => <OfferMatchCell offer={o} /> },
    { key: "status", label: "Stav", nowrap: true, render: (o) => o.status === "active" ? "sleduje sa"
      : <Badge variant="light" color="yellow">{o.status === "paused" ? "pozastavené" : "archivované"}</Badge> },
    ...(isAdmin ? [{ key: "actions", label: "", align: "right" as const,
                     render: (o: Offer) => <OfferActions offer={o} onAssign={setAssigning} onHistory={setHistory} /> }] : []),
  ];

  return (
    <Stack>
      <Group justify="space-between" align="flex-end">
        <Group align="flex-end">
          <TextInput label="Hľadať" placeholder="URL, názov alebo produkt" leftSection={<IconSearch size={16} />} w={260}
                     value={search} onChange={(e) => { setSearch(e.currentTarget.value); setPage(1); }} />
          <Select label="Párovanie" data={MATCH_FILTERS} value={matchStatus} allowDeselect={false} w={190}
                  onChange={(v) => { setMatchStatus(v ?? ""); setPage(1); }} />
        </Group>
        {isAdmin && !archived && (
          <Group gap="xs">
            <Button variant="default" leftSection={<IconFileImport size={16} />} onClick={importer.open}>
              Import zo súboru</Button>
            {!adding && <Button leftSection={<IconPlus size={16} />} onClick={addForm.open}>Pridať URL</Button>}
          </Group>
        )}
      </Group>
      <Collapse expanded={adding}>
        <Paper withBorder p="sm" radius="md">
          <Tabs defaultValue="paste" keepMounted={false}>
            <Tabs.List>
              <Tabs.Tab value="paste">Vložiť URL</Tabs.Tab>
              <Tabs.Tab value="discover">Zo stránky kategórie</Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value="paste" pt="sm">
              <AddOffersForm competitorId={competitorId} domain={domain} onDone={addForm.close} />
            </Tabs.Panel>
            <Tabs.Panel value="discover" pt="sm">
              <DiscoverLinks competitorId={competitorId} domain={domain} />
              <Group justify="flex-end" mt="xs"><Button variant="default" onClick={addForm.close}>Zavrieť</Button></Group>
            </Tabs.Panel>
          </Tabs>
        </Paper>
      </Collapse>
      <ImportWizard opened={importing} onClose={importer.close} kind="offers" competitorId={competitorId}
                    title="Import ponúk zo súboru"
                    help={<>CSV, Excel (.xlsx) alebo XML feed. Povinný stĺpec <Code>url</Code> (iba z domény {domain}),
                      voliteľne <Code>variant</Code> a priradenie k vlastnému produktu cez <Code>product_isbn</Code>,{" "}
                      <Code>product_ean</Code>, <Code>product_sku</Code>, <Code>product_id</Code> alebo{" "}
                      <Code>product_name</Code>. Pred uložením uvidíte náhľad.</>}
                    describe={(r) => {
                      const d = r.data as { url?: string; product_name?: string | null; variant?: string };
                      return <Text size="sm">{d.url}<Text span size="xs" c="dimmed">
                        {d.variant ? ` · ${d.variant}` : ""}{d.product_name ? ` → ${d.product_name}` : ""}</Text></Text>;
                    }} />
      <DataTable columns={columns} rows={offers.data?.items} rowKey={(o) => o.id} total={offers.data?.total}
                 page={page} pageSize={PAGE_SIZE} onPageChange={setPage} loading={offers.isLoading} error={offers.error}
                 minWidth={860}
                 emptyText={q || matchStatus ? "Žiadna ponuka nezodpovedá filtrom." : "Zatiaľ nie sú sledované žiadne produktové URL."} />
      <AssignModal offer={assigning} onClose={() => setAssigning(null)} />
      <HistoryModal offer={history} onClose={() => setHistory(null)} />
    </Stack>
  );
}
