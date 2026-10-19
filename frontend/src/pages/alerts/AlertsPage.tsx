import { Anchor, Badge, Button, Group, Paper, Select, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconCircleCheck, IconEye } from "@tabler/icons-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { RULE_LABELS, useAlerts, useChangeAlert, type AlertItem } from "../../api/alerts";
import { useIsAdmin } from "../../auth/auth";
import { DataTable, type Column } from "../../components/DataTable";
import { formatDateTime } from "../../format";

const STATUS = {
  open: { label: "Otvorené", color: "red", icon: <IconAlertTriangle size={12} /> },
  acknowledged: { label: "Potvrdené", color: "yellow", icon: <IconEye size={12} /> },
  resolved: { label: "Vyriešené", color: "green", icon: <IconCircleCheck size={12} /> },
} as const;
const STATUS_FILTER = [
  { value: "active", label: "Aktívne (otvorené a potvrdené)" }, { value: "open", label: "Otvorené" },
  { value: "acknowledged", label: "Potvrdené" }, { value: "resolved", label: "Vyriešené" },
];

export function AlertActions({ alert }: { alert: AlertItem }) {
  const change = useChangeAlert();
  const run = (action: "acknowledge" | "resolve" | "reopen") => change.mutate({ id: alert.id, action }, {
    onError: (e) => notifications.show({ color: "red", message: e.message }),
  });
  return (
    <Group gap={4} wrap="nowrap" justify="flex-end">
      {alert.status === "open" && (
        <Button size="compact-xs" variant="light" leftSection={<IconEye size={12} />} onClick={() => run("acknowledge")}>
          Potvrdiť</Button>
      )}
      {alert.status === "acknowledged" && (
        <Button size="compact-xs" variant="subtle" onClick={() => run("reopen")}>Znovu otvoriť</Button>
      )}
      {alert.status !== "resolved" && (
        <Button size="compact-xs" variant="light" color="green" leftSection={<IconCheck size={12} />}
                onClick={() => run("resolve")}>Vyriešiť</Button>
      )}
    </Group>
  );
}

export function AlertWhat({ alert }: { alert: AlertItem }) {
  return (
    <Stack gap={2}>
      <Text size="sm" fw={500}>{RULE_LABELS[alert.alert_type].label}</Text>
      <Text size="sm">{alert.message}</Text>
      <Text size="xs" c="dimmed">
        <Anchor component={Link} to={`/konkurencia/${alert.competitor_id}`} size="xs">{alert.competitor_name}</Anchor>
        {" · "}
        {alert.product_id
          ? <Anchor component={Link} to={`/produkty/${alert.product_id}`} size="xs">{alert.product_name}</Anchor>
          : <Anchor href={alert.product_url} target="_blank" rel="noopener noreferrer" size="xs">
              {alert.offer_title ?? alert.product_url}</Anchor>}
      </Text>
    </Stack>
  );
}

export function AlertsPage() {
  const isAdmin = useIsAdmin();
  const [status, setStatus] = useState("active");
  const [type, setType] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const alerts = useAlerts({ status, alert_type: type ?? undefined, page });

  const columns: Column<AlertItem>[] = [
    { key: "status", label: "Stav", nowrap: true, render: (a) => (
      <Badge variant="light" color={STATUS[a.status].color} leftSection={STATUS[a.status].icon}>
        {STATUS[a.status].label}</Badge>) },
    { key: "what", label: "Upozornenie", render: (a) => <AlertWhat alert={a} /> },
    { key: "when", label: "Naposledy zistené", nowrap: true, render: (a) => (
      <Stack gap={0}><Text size="sm">{formatDateTime(a.last_seen_at)}</Text>
        <Text size="xs" c="dimmed">vzniklo {formatDateTime(a.created_at)}</Text>
        {a.resolved_at && <Text size="xs" c="dimmed">vyriešené {formatDateTime(a.resolved_at)}</Text>}</Stack>) },
    ...(isAdmin ? [{ key: "actions", label: "", align: "right" as const, render: (a: AlertItem) => <AlertActions alert={a} /> }] : []),
  ];

  return (
    <Stack>
      <Title order={1} size="h2">Upozornenia</Title>
      <Paper withBorder p="md" radius="md">
        <Group mb="md" align="flex-end" justify="space-between">
          <Group align="flex-end">
            <Select label="Stav" w={280} allowDeselect={false} value={status} data={STATUS_FILTER}
                    onChange={(v) => { setStatus(v ?? "active"); setPage(1); }} />
            <Select label="Druh" w={300} placeholder="Všetky" clearable value={type}
                    data={Object.entries(RULE_LABELS).map(([value, { label }]) => ({ value, label }))}
                    onChange={(v) => { setType(v); setPage(1); }} />
          </Group>
          <Anchor component={Link} to="/nastavenia" size="sm">Pravidlá upozornení</Anchor>
        </Group>
        <DataTable columns={columns} rows={alerts.data?.items} rowKey={(a) => a.id} total={alerts.data?.total}
                   page={page} pageSize={25} onPageChange={setPage} loading={alerts.isLoading} error={alerts.error}
                   minWidth={760} emptyText={status === "active" ? "Žiadne aktívne upozornenia." : "Žiadne upozornenia."} />
        <Text size="xs" c="dimmed" mt="sm">
          Upozornenia vyhodnocuje aplikácia po každej kontrole a každých 5 minút podľa pravidiel v Nastaveniach.
          Stavy ako „konkurent lacnejší“ sa vyriešia samy, keď prestanú platiť; pokles ceny vyriešite ručne.
        </Text>
      </Paper>
    </Stack>
  );
}
