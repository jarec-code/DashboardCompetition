import { ActionIcon, Alert, Badge, Button, Group, NumberInput, Paper, Select, Switch, Table, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { useState } from "react";

import { RULE_LABELS, useAlertRules, useDeleteRule, useSaveRule, type AlertRule, type RuleType } from "../api/alerts";
import { useCompetitors } from "../api/competitors";

const DEFAULTS: Record<RuleType, Record<string, number>> = {
  price_drop: { percent: 10 }, cheaper_than_us: { percent: 0 }, unavailable: {}, stale: {}, scraper_failing: { count: 3 },
};

function RuleRow({ rule, readOnly, competitors }: {
  rule: AlertRule; readOnly: boolean; competitors: { value: string; label: string }[];
}) {
  const save = useSaveRule();
  const remove = useDeleteRule();
  const meta = RULE_LABELS[rule.rule_type];
  const update = (change: Partial<AlertRule>) => {
    const next = { ...rule, ...change };
    save.mutate({ id: rule.id, rule_type: next.rule_type, params: next.params, competitor_id: next.competitor_id,
                  product_id: next.product_id, enabled: next.enabled },
                { onError: (e) => notifications.show({ color: "red", message: e.message }) });
  };
  return (
    <Table.Tr>
      <Table.Td>
        <Switch checked={rule.enabled} disabled={readOnly} aria-label={`Zapnuté: ${meta.label}`}
                onChange={(e) => update({ enabled: e.currentTarget.checked })} />
      </Table.Td>
      <Table.Td><Text size="sm" fw={500}>{meta.label}</Text></Table.Td>
      <Table.Td>
        {meta.param ? (
          <Group gap={6} wrap="nowrap">
            <Text size="sm" c="dimmed">{meta.param.label}</Text>
            <NumberInput size="xs" w={80} min={meta.param.key === "count" ? 1 : 0} max={meta.param.key === "count" ? 50 : 100}
                         defaultValue={rule.params[meta.param.key]} disabled={readOnly} aria-label={meta.param.label}
                         onBlur={(e) => {
                           const value = Number(e.currentTarget.value);
                           if (!Number.isNaN(value) && value !== rule.params[meta.param!.key]) {
                             update({ params: { ...rule.params, [meta.param!.key]: value } });
                           }
                         }} />
            <Text size="sm" c="dimmed">{meta.param.suffix}</Text>
          </Group>
        ) : <Text size="sm" c="dimmed">—</Text>}
      </Table.Td>
      <Table.Td>
        <Select size="xs" w={180} placeholder="Všetci konkurenti" clearable disabled={readOnly} data={competitors}
                value={rule.competitor_id ? String(rule.competitor_id) : null} aria-label="Konkurent"
                onChange={(v) => update({ competitor_id: v ? Number(v) : null })} />
      </Table.Td>
      <Table.Td ta="right">{rule.open_alerts > 0 ? <Badge color="red" variant="light">{rule.open_alerts}</Badge> : 0}</Table.Td>
      <Table.Td ta="right">
        {!readOnly && (
          <ActionIcon variant="subtle" color="red" aria-label={`Zmazať pravidlo ${meta.label}`}
                      onClick={() => remove.mutate(rule.id)}><IconTrash size={16} /></ActionIcon>
        )}
      </Table.Td>
    </Table.Tr>
  );
}

export function AlertRulesSection({ readOnly }: { readOnly: boolean }) {
  const rules = useAlertRules();
  const competitors = useCompetitors({ status: "all", page_size: 100 });
  const save = useSaveRule();
  const [newType, setNewType] = useState<RuleType | null>(null);
  const competitorOptions = (competitors.data?.items ?? []).map((c) => ({ value: String(c.id), label: c.name }));

  return (
    <Paper withBorder p="md" radius="md">
      <Title order={2} size="h4" mb={4}>Pravidlá upozornení</Title>
      <Text size="xs" c="dimmed" mb="sm">Vyhodnocujú sa bežným kódom po každej kontrole a každých 5 minút, bez AI.</Text>
      {rules.error && <Alert color="red">{rules.error.message}</Alert>}
      <Table.ScrollContainer minWidth={760}>
        <Table verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr><Table.Th>Zapnuté</Table.Th><Table.Th>Pravidlo</Table.Th><Table.Th>Prah</Table.Th>
              <Table.Th>Konkurent</Table.Th><Table.Th ta="right">Otvorené</Table.Th><Table.Th /></Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rules.data?.map((r) => <RuleRow key={r.id} rule={r} readOnly={readOnly} competitors={competitorOptions} />)}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {!readOnly && (
        <Group mt="sm" align="flex-end">
          <Select label="Nové pravidlo" placeholder="Vyberte druh" w={320} value={newType}
                  data={Object.entries(RULE_LABELS).map(([value, { label }]) => ({ value, label }))}
                  onChange={(v) => setNewType(v as RuleType | null)} />
          <Button leftSection={<IconPlus size={16} />} disabled={!newType} loading={save.isPending}
                  onClick={() => newType && save.mutate({ rule_type: newType, params: DEFAULTS[newType], competitor_id: null,
                                                          product_id: null, enabled: true },
                                                        { onSuccess: () => setNewType(null) })}>
            Pridať
          </Button>
        </Group>
      )}
    </Paper>
  );
}
