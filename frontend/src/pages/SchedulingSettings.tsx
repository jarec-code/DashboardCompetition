import { Alert, Button, Group, NumberInput, Paper, Select, SimpleGrid, Stack, Switch, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useEffect, useState, type FormEvent } from "react";

import { useSaveSchedulingSettings, useSchedulingSettings, type SchedulingSettings } from "../api/monitoring";

const HOURS = Array.from({ length: 24 }, (_, h) => ({ value: String(h), label: `${String(h).padStart(2, "0")}:00` }));

export function SchedulingSettingsSection({ readOnly }: { readOnly: boolean }) {
  const settings = useSchedulingSettings();
  const save = useSaveSchedulingSettings();
  const [form, setForm] = useState<SchedulingSettings | null>(null);
  useEffect(() => { if (settings.data) setForm(settings.data); }, [settings.data]);
  if (!form) return null;

  const hours = (key: keyof SchedulingSettings["priority_interval_hours"]) => ({
    value: form.priority_interval_hours[key], min: 1, max: 720, disabled: readOnly,
    onChange: (v: string | number) => setForm({ ...form, priority_interval_hours: {
      ...form.priority_interval_hours, [key]: Number(v) || 1 } }),
  });
  const windowOn = form.window_start_hour !== null;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    save.mutate(form, { onSuccess: () => notifications.show({ color: "green", message: "Plánovanie bolo uložené." }) });
  }

  return (
    <Paper withBorder p="md" radius="md">
      <form onSubmit={submit}>
        <Stack>
          <Title order={2} size="h4">Automatické aktualizácie</Title>
          {save.error && <Alert color="red">{save.error.message}</Alert>}
          <Switch label="Automatické kontroly zapnuté" checked={form.scheduler_enabled} disabled={readOnly}
                  onChange={(e) => setForm({ ...form, scheduler_enabled: e.currentTarget.checked })} />
          <div>
            <Text size="sm" fw={500}>Interval kontroly podľa priority produktu (hodiny)</Text>
            <Text size="xs" c="dimmed">Frekvencia nastavená pri konkurentovi alebo pri ponuke má prednosť.</Text>
          </div>
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <NumberInput label="Strategické" {...hours("strategic")} />
            <NumberInput label="Bežné" {...hours("regular")} />
            <NumberInput label="Stabilné" {...hours("stable")} />
          </SimpleGrid>
          <Switch label="Spúšťať kontroly iba v časovom okne (miestny čas)" checked={windowOn} disabled={readOnly}
                  onChange={(e) => setForm({ ...form, window_start_hour: e.currentTarget.checked ? 1 : null,
                                             window_end_hour: e.currentTarget.checked ? 5 : null })} />
          {windowOn && (
            <Group>
              <Select label="Od" data={HOURS} w={120} allowDeselect={false} disabled={readOnly}
                      value={String(form.window_start_hour)}
                      onChange={(v) => setForm({ ...form, window_start_hour: Number(v) })} />
              <Select label="Do" data={HOURS} w={120} allowDeselect={false} disabled={readOnly}
                      value={String(form.window_end_hour)} onChange={(v) => setForm({ ...form, window_end_hour: Number(v) })} />
              <Text size="xs" c="dimmed" mt={24}>Okno môže prechádzať cez polnoc (napr. 22:00 – 05:00).</Text>
            </Group>
          )}
          <NumberInput label="Najviac ponúk naplánovaných naraz" description="Obmedzuje záťaž na jeden beh plánovača"
                       w={300} min={1} max={1000} value={form.batch_size} disabled={readOnly}
                       onChange={(v) => setForm({ ...form, batch_size: Number(v) || 1 })} />
          {!readOnly && <Group justify="flex-end"><Button type="submit" loading={save.isPending}>Uložiť plánovanie</Button></Group>}
        </Stack>
      </form>
    </Paper>
  );
}
