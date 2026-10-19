import { Alert, Anchor, Badge, Center, Group, Loader, Paper, SimpleGrid, Stack, Table, Text, ThemeIcon, Title } from "@mantine/core";
import { IconAlertTriangle, IconArrowDownRight, IconArrowUpRight, IconCircleCheck } from "@tabler/icons-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { useAlerts } from "../../api/alerts";
import { useDashboard, type ChangeItem } from "../../api/comparison";
import { StatTile } from "../../components/StatTile";
import { formatDateTime, formatMoney, formatSignedPercent } from "../../format";
import { AlertWhat } from "../alerts/AlertsPage";
import { RUN_STATUS_LABELS } from "../competitors/labels";

const CHANGE_LABELS: Record<string, string> = {
  price_up: "cena ↑", price_down: "cena ↓", availability: "dostupnosť", currency: "mena", original_price: "pôvodná cena",
};

function Section({ title, children, empty }: { title: string; children: ReactNode; empty: boolean }) {
  return (
    <Paper withBorder p="md" radius="md">
      <Title order={2} size="h5" mb="sm">{title}</Title>
      {empty ? <Text size="sm" c="dimmed">Zatiaľ žiadne údaje.</Text> : children}
    </Paper>
  );
}

function ChangeRow({ item }: { item: ChangeItem }) {
  const pct = item.change_pct === null ? null : Number(item.change_pct);
  return (
    <Table.Tr>
      <Table.Td>
        <Anchor component={Link} to={item.product_id ? `/produkty/${item.product_id}` : `/konkurencia/${item.competitor_id}`}
                size="sm" lineClamp={1}>{item.product_name ?? item.title ?? "Nepriradená ponuka"}</Anchor>
        <Text size="xs" c="dimmed">{item.competitor_name} · {formatDateTime(item.observed_at)}</Text>
      </Table.Td>
      <Table.Td ta="right" style={{ whiteSpace: "nowrap" }}>
        <Text size="sm" fw={600}>{formatMoney(item.price, item.currency)}</Text>
        {item.previous_price && item.previous_price !== item.price && (
          <Text size="xs" c="dimmed" td="line-through">{formatMoney(item.previous_price, item.currency)}</Text>
        )}
      </Table.Td>
      <Table.Td style={{ whiteSpace: "nowrap" }}>
        {pct !== null && pct !== 0 ? (
          <Badge variant="light" color={pct < 0 ? "red" : "green"}
                 leftSection={pct < 0 ? <IconArrowDownRight size={12} /> : <IconArrowUpRight size={12} />}>
            {formatSignedPercent(item.change_pct)}
          </Badge>
        ) : (
          <Group gap={4}>{item.change_types.map((c) => <Badge key={c} size="xs" variant="outline" color="gray">
            {CHANGE_LABELS[c] ?? c}</Badge>)}</Group>
        )}
      </Table.Td>
    </Table.Tr>
  );
}

export function DashboardPage() {
  const { data, isLoading, error } = useDashboard();
  const alerts = useAlerts({ status: "open", page: 1, page_size: 5 });
  if (isLoading) return <Center p="xl"><Loader /></Center>;
  if (error || !data) return <Alert color="red">{error?.message}</Alert>;

  const ok = <ThemeIcon variant="light" color="green" size="sm" radius="xl" aria-label="v poriadku"><IconCircleCheck size={14} /></ThemeIcon>;
  const warn = <ThemeIcon variant="light" color="orange" size="sm" radius="xl" aria-label="vyžaduje pozornosť"><IconAlertTriangle size={14} /></ThemeIcon>;

  return (
    <Stack>
      <Title order={1} size="h2">Dashboard</Title>
      <SimpleGrid cols={{ base: 1, xs: 2, md: 3 }}>
        <StatTile label="Konkurenti" value={data.competitors_total} hint={`aktívni ${data.competitors_active}`} />
        <StatTile label="Sledované ponuky" value={data.offers_tracked} hint="aktívne ponuky aktívnych konkurentov" />
        <StatTile label="Úspešné kontroly za 24 h" value={data.checks_24h} icon={data.failed_checks_24h ? warn : undefined}
                  hint={`neúspešné ${data.failed_checks_24h}`} />
        <StatTile label="Chýbajúce alebo neaktuálne ceny" value={data.missing_or_stale}
                  icon={data.missing_or_stale ? warn : ok} hint="bez ceny alebo dlho neoverené" />
        <StatTile label="Zmeny cien za 24 h" value={data.price_changes_24h} hint={`za 7 dní ${data.price_changes_7d}`} />
        <StatTile label="Konkurent je lacnejší" value={data.cheaper_competitor_rows}
                  icon={data.cheaper_competitor_rows ? warn : undefined}
                  hint={<Anchor component={Link} to="/porovnanie?position=not_cheapest" size="xs">
                    z {data.comparable_rows} porovnateľných produktov</Anchor>} />
      </SimpleGrid>

      {!!alerts.data?.total && (
        <Paper withBorder p="md" radius="md">
          <Group justify="space-between" mb="sm">
            <Group gap="xs"><IconAlertTriangle size={18} aria-hidden /><Title order={2} size="h5">
              Otvorené upozornenia ({alerts.data.total})</Title></Group>
            <Anchor component={Link} to="/upozornenia" size="sm">Všetky upozornenia</Anchor>
          </Group>
          <Stack gap="sm">{alerts.data.items.map((a) => <AlertWhat key={a.id} alert={a} />)}</Stack>
        </Paper>
      )}

      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Section title="Najväčšie poklesy cien (30 dní)" empty={data.biggest_drops.length === 0}>
          <Table verticalSpacing="xs"><Table.Tbody>
            {data.biggest_drops.map((i) => <ChangeRow key={`${i.offer_id}-${i.observed_at}`} item={i} />)}
          </Table.Tbody></Table>
        </Section>
        <Section title="Posledné cenové zmeny" empty={data.recent_changes.length === 0}>
          <Table verticalSpacing="xs"><Table.Tbody>
            {data.recent_changes.map((i) => <ChangeRow key={`${i.offer_id}-${i.observed_at}`} item={i} />)}
          </Table.Tbody></Table>
        </Section>
      </SimpleGrid>

      <Section title="Posledné behy kontrol" empty={data.recent_runs.length === 0}>
        <Table verticalSpacing="xs" striped>
          <Table.Tbody>
            {data.recent_runs.map((r) => (
              <Table.Tr key={r.id}>
                <Table.Td>{formatDateTime(r.started_at)}</Table.Td>
                <Table.Td><Anchor component={Link} to={`/konkurencia/${r.competitor_id}`} size="sm">{r.competitor_name}</Anchor></Table.Td>
                <Table.Td>{r.trigger === "scheduled" ? "automaticky" : "ručne"}</Table.Td>
                <Table.Td><Badge variant="light" color={RUN_STATUS_LABELS[r.status].color}>{RUN_STATUS_LABELS[r.status].label}</Badge></Table.Td>
                <Table.Td ta="right">{r.success_count} / {r.total_count}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <Anchor component={Link} to="/aktualizacie" size="sm">Všetky behy a logy</Anchor>
      </Section>
    </Stack>
  );
}
