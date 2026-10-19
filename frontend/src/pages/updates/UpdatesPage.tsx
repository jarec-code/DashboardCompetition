import {
  Alert, Anchor, Badge, Group, List, Paper, Select, SimpleGrid, Stack, Tabs, Text, Title,
} from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { useJobs, useMonitoring, useRuns, type JobItem, type Monitoring, type RunItem } from "../../api/monitoring";
import { DataTable, type Column } from "../../components/DataTable";
import { formatDateTime } from "../../format";
import { RUN_STATUS_LABELS } from "../competitors/labels";

const PAGE_SIZE = 25;
const STALE_WORKER_MINUTES = 5;

const JOB_STATUS: Record<JobItem["status"], { label: string; color: string }> = {
  pending: { label: "Čaká", color: "gray" }, running: { label: "Beží", color: "blue" },
  succeeded: { label: "Úspešná", color: "green" }, failed: { label: "Chyba", color: "red" },
  cancelled: { label: "Preskočená", color: "yellow" },
};
const KIND: Record<JobItem["kind"], string> = { scheduled: "automatická", manual: "ručná", test: "test" };
const TRIGGER: Record<RunItem["trigger"], string> = { scheduled: "automaticky", manual: "ručne" };

function ago(value: string | null, now: string): number | null {
  return value ? (new Date(now).getTime() - new Date(value).getTime()) / 60000 : null;
}

function Stat({ label, value, hint, color }: { label: string; value: ReactNode; hint?: ReactNode; color?: string }) {
  return (
    <Paper withBorder p="md" radius="md">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{label}</Text>
      <Text fz={26} fw={700} c={color}>{value}</Text>
      {hint && <Text size="xs" c="dimmed">{hint}</Text>}
    </Paper>
  );
}

function StatusCards({ m }: { m: Monitoring }) {
  const workerAge = ago(m.worker_last_seen_at, m.now);
  const workerOk = workerAge !== null && workerAge < STALE_WORKER_MINUTES;
  return (
    <SimpleGrid cols={{ base: 1, xs: 2, md: 3, lg: 5 }}>
      <Stat label="Scheduler" color={m.scheduler_enabled ? undefined : "yellow"}
            value={!m.scheduler_enabled ? "vypnutý" : m.in_window ? "plánuje" : "mimo okna"}
            hint={`posledný beh ${formatDateTime(m.scheduler_last_tick_at)}`} />
      <Stat label="Worker" color={workerOk ? "green" : "red"} value={workerOk ? "beží" : "nereaguje"}
            hint={`naposledy aktívny ${formatDateTime(m.worker_last_seen_at)}`} />
      <Stat label="Fronta" value={m.pending_jobs + m.running_jobs}
            hint={`čaká ${m.pending_jobs}, beží ${m.running_jobs}, na rade ${m.due_offers} ponúk`} />
      <Stat label="Kontroly za 24 h" value={m.checks_24h} color={m.failures_24h ? "orange" : undefined}
            hint={`chyby ${m.failures_24h}, zmeny cien ${m.price_changes_24h}`} />
      <Stat label="Neaktuálne ceny" value={m.stale_prices} color={m.stale_prices ? "orange" : undefined}
            hint="posledná úspešná kontrola je staršia ako 2× interval" />
    </SimpleGrid>
  );
}

function DiscoveryOverview({ m }: { m: Monitoring }) {
  if (!m.discovery.length) return null;
  return (
    <Paper withBorder p="md" radius="md">
      <Title order={2} size="h4" mb="xs">Objavovanie produktov konkurentov</Title>
      <List size="sm" spacing={4}>
        {m.discovery.map((d) => {
          const paused = d.paused_until && new Date(d.paused_until) > new Date();
          return (
            <List.Item key={d.competitor_id}>
              <Anchor component={Link} to={`/konkurencia/${d.competitor_id}`}>{d.competitor_name}</Anchor>:{" "}
              {d.mode === "auto" ? "automatický režim" : "tieňový režim"}
              {d.running ? " · beh prebieha" : ` · posledný beh ${formatDateTime(d.last_run_at)}`}
              {!paused && d.next_run_at ? ` · ďalší ${formatDateTime(d.next_run_at)}` : ""}
              {` · automaticky sledované ${d.tracked_auto}`}
              {d.review > 0 && <Badge ml="xs" size="sm" variant="light" color="orange">na kontrolu: {d.review}</Badge>}
              {paused && <Badge ml="xs" size="sm" variant="light" color="red">pozastavené do {formatDateTime(d.paused_until)}</Badge>}
              {d.last_error && <Text size="xs" c="orange">{d.last_error}</Text>}
            </List.Item>
          );
        })}
      </List>
    </Paper>
  );
}

function Problems({ m }: { m: Monitoring }) {
  if (!m.problem_competitors.length && !m.problem_offers.length) return null;
  return (
    <Alert color="red" icon={<IconAlertTriangle size={18} />} title="Opakované zlyhania">
      <List size="sm" spacing={4}>
        {m.problem_competitors.map((c) => (
          <List.Item key={`c${c.id}`}>
            <Anchor component={Link} to={`/konkurencia/${c.id}`}>{c.name}</Anchor>: {c.failed_runs_in_row} neúspešné
            behy za sebou{c.last_error ? ` (${c.last_error.split("\n")[0]})` : ""}
          </List.Item>
        ))}
        {m.problem_offers.map((o) => (
          <List.Item key={`o${o.id}`}>
            <Anchor component={Link} to={`/konkurencia/${o.competitor_id}`}>{o.competitor_name}</Anchor>{" "}
            <Anchor href={o.product_url} target="_blank" rel="noopener noreferrer" size="sm">
              {new URL(o.product_url).pathname}</Anchor>: {o.consecutive_failures}× za sebou neúspešná kontrola
            {o.last_error ? ` – ${o.last_error}` : ""}. Posledná platná cena: {formatDateTime(o.last_success_at)}.
          </List.Item>
        ))}
      </List>
    </Alert>
  );
}

function RunsTab() {
  const [status, setStatus] = useState<string | null>(null);
  const [trigger, setTrigger] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const runs = useRuns({ status: status ?? undefined, trigger: trigger ?? undefined, page });
  const columns: Column<RunItem>[] = [
    { key: "started", label: "Začiatok", nowrap: true, render: (r) => formatDateTime(r.started_at) },
    { key: "competitor", label: "Konkurent", render: (r) => (
      <Anchor component={Link} to={`/konkurencia/${r.competitor_id}`} size="sm">{r.competitor_name}</Anchor>) },
    { key: "trigger", label: "Spustenie", render: (r) => TRIGGER[r.trigger] },
    { key: "status", label: "Stav", nowrap: true, render: (r) => (
      <Badge variant="light" color={RUN_STATUS_LABELS[r.status].color}>{RUN_STATUS_LABELS[r.status].label}</Badge>) },
    { key: "count", label: "Úspešné / spolu", align: "right", render: (r) => `${r.success_count} / ${r.total_count}` },
    { key: "fail", label: "Chyby", align: "right", render: (r) => r.failure_count
      ? <Text c="red" fw={600} size="sm">{r.failure_count}</Text> : 0 },
    { key: "changes", label: "Zmeny cien", align: "right", render: (r) => r.price_change_count },
    { key: "error", label: "Chybové hlásenie", render: (r) => (
      <Text size="xs" c="red" lineClamp={2} title={r.error_summary ?? undefined}>{r.error_summary}</Text>) },
  ];
  return (
    <Stack>
      <Group>
        <Select label="Stav" placeholder="Všetky" clearable w={180} value={status} onChange={(v) => { setStatus(v); setPage(1); }}
                data={Object.entries(RUN_STATUS_LABELS).map(([value, { label }]) => ({ value, label }))} />
        <Select label="Spustenie" placeholder="Všetky" clearable w={180} value={trigger}
                onChange={(v) => { setTrigger(v); setPage(1); }}
                data={[{ value: "scheduled", label: "automaticky" }, { value: "manual", label: "ručne" }]} />
      </Group>
      <DataTable columns={columns} rows={runs.data?.items} rowKey={(r) => r.id} total={runs.data?.total} page={page}
                 pageSize={PAGE_SIZE} onPageChange={setPage} loading={runs.isLoading} error={runs.error} minWidth={900}
                 emptyText="Zatiaľ neprebehol žiadny beh kontrol." />
    </Stack>
  );
}

function JobsTab() {
  const [status, setStatus] = useState<string | null>(null);
  const [kind, setKind] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const jobs = useJobs({ status: status ?? undefined, kind: kind ?? undefined, page });
  const columns: Column<JobItem>[] = [
    { key: "created", label: "Naplánovaná na", nowrap: true, render: (j) => formatDateTime(j.scheduled_at) },
    { key: "competitor", label: "Konkurent", nowrap: true, render: (j) => (
      <Anchor component={Link} to={`/konkurencia/${j.competitor_id}`} size="sm">{j.competitor_name}</Anchor>) },
    { key: "url", label: "Stránka", render: (j) => j.url ? (
      <Anchor href={j.url} target="_blank" rel="noopener noreferrer" size="xs">{new URL(j.url).pathname}</Anchor>) : "—" },
    { key: "kind", label: "Druh", render: (j) => KIND[j.kind] },
    { key: "status", label: "Stav", nowrap: true, render: (j) => (
      <Badge variant="light" color={JOB_STATUS[j.status].color}>{JOB_STATUS[j.status].label}</Badge>) },
    { key: "attempts", label: "Pokusy", align: "right", render: (j) => j.attempt_count },
    { key: "finished", label: "Dokončená", nowrap: true, render: (j) => formatDateTime(j.finished_at) },
    { key: "error", label: "Chyba", render: (j) => j.last_error ? (
      <Text size="xs" c={j.status === "pending" ? "orange" : "red"} lineClamp={2} title={j.last_error}>
        {j.error_kind === "temporary" ? "dočasná: " : ""}{j.last_error}</Text>) : null },
  ];
  return (
    <Stack>
      <Group>
        <Select label="Stav" placeholder="Všetky" clearable w={180} value={status}
                onChange={(v) => { setStatus(v); setPage(1); }}
                data={Object.entries(JOB_STATUS).map(([value, { label }]) => ({ value, label }))} />
        <Select label="Druh" placeholder="Všetky" clearable w={180} value={kind} onChange={(v) => { setKind(v); setPage(1); }}
                data={Object.entries(KIND).map(([value, label]) => ({ value, label }))} />
      </Group>
      <DataTable columns={columns} rows={jobs.data?.items} rowKey={(j) => j.id} total={jobs.data?.total} page={page}
                 pageSize={PAGE_SIZE} onPageChange={setPage} loading={jobs.isLoading} error={jobs.error} minWidth={980}
                 emptyText="Žiadne úlohy." />
    </Stack>
  );
}

export function UpdatesPage() {
  const monitoring = useMonitoring();
  return (
    <Stack>
      <Title order={1} size="h2">Aktualizácie a logy</Title>
      {monitoring.error && <Alert color="red">{monitoring.error.message}</Alert>}
      {monitoring.data && (
        <>
          <StatusCards m={monitoring.data} />
          <Problems m={monitoring.data} />
          <DiscoveryOverview m={monitoring.data} />
        </>
      )}
      <Paper withBorder radius="md">
        <Tabs defaultValue="runs" keepMounted={false}>
          <Tabs.List>
            <Tabs.Tab value="runs">Behy kontrol</Tabs.Tab>
            <Tabs.Tab value="jobs">Úlohy</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="runs" p="md"><RunsTab /></Tabs.Panel>
          <Tabs.Panel value="jobs" p="md"><JobsTab /></Tabs.Panel>
        </Tabs>
      </Paper>
    </Stack>
  );
}
