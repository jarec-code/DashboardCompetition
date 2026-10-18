import {
  Alert, Anchor, Badge, Breadcrumbs, Button, Center, Group, Loader, Paper, SimpleGrid, Stack, Table, Tabs, Text, Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconEdit, IconExternalLink } from "@tabler/icons-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { useCompetitor, useCompetitorRuns, type Competitor } from "../../api/competitors";
import { useIsAdmin } from "../../auth/auth";
import { formatDateTime, formatFrequency } from "../../format";
import { CompetitorFormModal } from "./CompetitorFormModal";
import { RUN_STATUS_LABELS, SOURCE_LABELS, STATUS_LABELS, TYPE_LABELS } from "./labels";
import { OffersPanel } from "../offers/OffersPanel";
import { ScrapeConfigForm } from "./ScrapeConfigForm";
import { StatusActions } from "./StatusActions";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{label}</Text>
      <Text size="sm" component="div">{children}</Text>
    </div>
  );
}

function BasicInfo({ competitor: c }: { competitor: Competitor }) {
  return (
    <Stack>
      <SimpleGrid cols={{ base: 1, xs: 2, md: 3 }} spacing="lg">
        <Field label="URL">
          <Anchor href={c.base_url} target="_blank" rel="noopener noreferrer">
            {c.base_url} <IconExternalLink size={12} />
          </Anchor>
        </Field>
        <Field label="Doména">{c.domain}</Field>
        <Field label="Krajina / trh">{c.country ?? "—"} / {c.market ?? "—"}</Field>
        <Field label="Typ">{TYPE_LABELS[c.competitor_type]}</Field>
        <Field label="Preferovaný zdroj">{SOURCE_LABELS[c.preferred_source]}</Field>
        <Field label="Frekvencia aktualizácie">{formatFrequency(c.default_frequency_hours)}</Field>
        <Field label="Vytvorený">{formatDateTime(c.created_at)}</Field>
        <Field label="Naposledy upravený">{formatDateTime(c.updated_at)}</Field>
        {c.duplicate_domain_reason && <Field label="Dôvod samostatného záznamu">{c.duplicate_domain_reason}</Field>}
      </SimpleGrid>
      <Field label="Interná poznámka">
        <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{c.notes ?? "—"}</Text>
      </Field>
    </Stack>
  );
}

function Monitoring({ competitorId }: { competitorId: number }) {
  const runs = useCompetitorRuns(competitorId);
  if (runs.isLoading) return <Center p="md"><Loader size="sm" /></Center>;
  if (runs.error) return <Alert color="red">{runs.error.message}</Alert>;
  if (!runs.data?.length) {
    return (
      <Text c="dimmed" size="sm">
        Zatiaľ neprebehla žiadna kontrola. Spustite ju tlačidlom „Skontrolovať teraz“ pri ponuke; automatické kontroly pribudnú v etape 5.
      </Text>
    );
  }
  return (
    <Table.ScrollContainer minWidth={640}>
      <Table striped verticalSpacing="xs">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Začiatok</Table.Th><Table.Th>Stav</Table.Th><Table.Th ta="right">Úspešné</Table.Th>
            <Table.Th ta="right">Chyby</Table.Th><Table.Th ta="right">Zmeny cien</Table.Th><Table.Th>Chybové hlásenie</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {runs.data.map((r) => (
            <Table.Tr key={r.id}>
              <Table.Td>{formatDateTime(r.started_at)}</Table.Td>
              <Table.Td><Badge variant="light" color={RUN_STATUS_LABELS[r.status].color}>
                {RUN_STATUS_LABELS[r.status].label}</Badge></Table.Td>
              <Table.Td ta="right">{r.success_count} / {r.total_count}</Table.Td>
              <Table.Td ta="right">{r.failure_count}</Table.Td>
              <Table.Td ta="right">{r.price_change_count}</Table.Td>
              <Table.Td><Text size="sm" c="red">{r.error_summary ?? ""}</Text></Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

export function CompetitorDetailPage() {
  const id = Number(useParams().id);
  const isAdmin = useIsAdmin();
  const [editOpened, edit] = useDisclosure();
  const { data: competitor, isLoading, error } = useCompetitor(id);

  if (isLoading) return <Center p="xl"><Loader /></Center>;
  if (error || !competitor) {
    return <Alert color="red" title="Konkurenta sa nepodarilo načítať">{error?.message}</Alert>;
  }
  const status = STATUS_LABELS[competitor.status];

  return (
    <Stack>
      <Breadcrumbs>
        <Anchor component={Link} to="/konkurencia" size="sm">Konkurencia</Anchor>
        <Text size="sm">{competitor.name}</Text>
      </Breadcrumbs>
      <Group justify="space-between" align="flex-start">
        <Group gap="sm">
          <Title order={1} size="h2">{competitor.name}</Title>
          <Badge color={status.color} variant="light" size="lg">{status.label}</Badge>
        </Group>
        {isAdmin && (
          <Group gap="xs">
            <StatusActions id={competitor.id} name={competitor.name} status={competitor.status} />
            <Button size="sm" leftSection={<IconEdit size={16} />} onClick={edit.open}>Upraviť</Button>
          </Group>
        )}
      </Group>

      <Paper withBorder radius="md">
        <Tabs defaultValue="basic" keepMounted={false}>
          <Tabs.List>
            <Tabs.Tab value="basic">Základné údaje</Tabs.Tab>
            <Tabs.Tab value="offers">Sledované produkty</Tabs.Tab>
            <Tabs.Tab value="scraping">Nastavenia scrapingu</Tabs.Tab>
            <Tabs.Tab value="monitoring">Monitoring</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="basic" p="md"><BasicInfo competitor={competitor} /></Tabs.Panel>
          <Tabs.Panel value="offers" p="md">
            <OffersPanel competitorId={competitor.id} domain={competitor.domain}
                         archived={competitor.status === "archived"} />
          </Tabs.Panel>
          <Tabs.Panel value="scraping" p="md">
            <ScrapeConfigForm competitorId={competitor.id} baseUrl={competitor.base_url} config={competitor.scrape_config}
                              readOnly={!isAdmin} />
          </Tabs.Panel>
          <Tabs.Panel value="monitoring" p="md"><Monitoring competitorId={competitor.id} /></Tabs.Panel>
        </Tabs>
      </Paper>
      <CompetitorFormModal opened={editOpened} onClose={edit.close} competitor={competitor} />
    </Stack>
  );
}
