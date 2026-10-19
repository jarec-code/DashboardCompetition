import {
  Alert, Badge, Button, Group, List, NumberInput, Paper, Select, Stack, Switch, Text, TextInput, Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconRefresh } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { useCatalogSync, useRunCatalogSync, useSaveCatalogSync, type CatalogSyncSettings } from "../api/catalog";
import { formatDateTime } from "../format";

const MARKETS = ["CZ", "SK", "PL", "HU", "AT", "DE"];
const EXAMPLE = "https://www.etaktik.cz/export/heureka.php";

/** Regular update of our own products from the feed of our e-shop (runs in the worker). */
export function CatalogSyncSection({ readOnly }: { readOnly: boolean }) {
  const queryClient = useQueryClient();
  const state = useCatalogSync();
  const save = useSaveCatalogSync();
  const run = useRunCatalogSync();
  const [form, setForm] = useState<CatalogSyncSettings | null>(null);
  useEffect(() => { if (state.data && !form) setForm(state.data.settings); }, [state.data, form]);

  // When a requested run has finished, the product lists are out of date.
  const waiting = Boolean(state.data?.status.requested_at);
  const wasWaiting = useRef(false);
  useEffect(() => {
    if (wasWaiting.current && !waiting) {
      ["products", "offers", "comparison"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }));
    }
    wasWaiting.current = waiting;
  }, [waiting, queryClient]);

  if (!form || !state.data) return null;
  const { status, last_run: last } = state.data;
  const stats = last?.stats ?? {};

  function submit(event: FormEvent) {
    event.preventDefault();
    if (form) save.mutate(form, { onSuccess: () => notifications.show({ color: "green", message: "Nastavenie feedu bolo uložené." }) });
  }

  return (
    <Paper withBorder p="md" radius="md">
      <form onSubmit={submit}>
        <Stack>
          <div>
            <Title order={2} size="h4">Vlastné produkty z feedu e-shopu</Title>
            <Text size="xs" c="dimmed">Worker stiahne feed (Heureka alebo Google Merchant), pridá nové produkty a
              aktualizuje ceny, URL, dostupnosť a ďalšie údaje existujúcich. Jedna požiadavka na beh, robots.txt sa
              rešpektuje. Nejednoznačné položky sa neuložia a zobrazia sa nižšie na kontrolu.</Text>
          </div>
          {(save.error || run.error) && <Alert color="red">{(save.error ?? run.error)?.message}</Alert>}
          <TextInput label="Adresa feedu" placeholder={EXAMPLE} value={form.feed_url ?? ""} disabled={readOnly}
                     onChange={(e) => setForm({ ...form, feed_url: e.currentTarget.value || null })} />
          <Group align="flex-end">
            <Select label="Trh (mena cien vo feede)" data={MARKETS} value={form.market} w={200} allowDeselect={false}
                    disabled={readOnly} onChange={(v) => setForm({ ...form, market: v ?? "CZ" })} />
            <NumberInput label="Interval (hodiny)" min={1} max={720} w={160} value={form.interval_hours}
                         disabled={readOnly} onChange={(v) => setForm({ ...form, interval_hours: Number(v) || 24 })} />
            <Switch label="Automaticky podľa intervalu" checked={form.enabled} disabled={readOnly} mb={8}
                    onChange={(e) => setForm({ ...form, enabled: e.currentTarget.checked })} />
          </Group>
          {!readOnly && (
            <Group justify="flex-end">
              <Button variant="default" leftSection={<IconRefresh size={16} />} loading={run.isPending || waiting}
                      disabled={!state.data.settings.feed_url} onClick={() => run.mutate()}>Synchronizovať teraz</Button>
              <Button type="submit" loading={save.isPending}>Uložiť</Button>
            </Group>
          )}

          <Stack gap={4}>
            <Text size="sm">
              Posledný beh: <b>{formatDateTime(status.last_run_at)}</b>
              {waiting && <Badge ml="xs" variant="light">čaká na worker</Badge>}
              {status.last_error ? <Badge ml="xs" color="red" variant="light">zlyhal</Badge>
                : status.last_success_at && <Badge ml="xs" color="green" variant="light">úspešný</Badge>}
            </Text>
            {status.last_error && <Alert color="red" p="xs">{status.last_error}</Alert>}
            {last && (
              <Text size="sm" c="dimmed">
                {formatDateTime(last.committed_at)}: nové <b>{stats.imported ?? 0}</b> · aktualizované{" "}
                <b>{stats.updated ?? 0}</b> · bez zmeny <b>{stats.unchanged ?? 0}</b> · na kontrolu{" "}
                <b>{stats.review ?? 0}</b> · odmietnuté <b>{stats.error ?? 0}</b>
              </Text>
            )}
            {last && last.problems.length > 0 && (
              <List size="xs" spacing={2} mah={220} style={{ overflowY: "auto" }}>
                {last.problems.map((p) => (
                  <List.Item key={p.row}>
                    <b>{p.name ?? `riadok ${p.row}`}</b>{p.internal_id ? ` (ID ${p.internal_id})` : ""}: {p.reason}
                  </List.Item>
                ))}
              </List>
            )}
          </Stack>
        </Stack>
      </form>
    </Paper>
  );
}
