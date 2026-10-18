import {
  Alert, Badge, Button, Divider, Group, NumberInput, Select, SimpleGrid, Stack, Switch, Text, TextInput, Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconFlask } from "@tabler/icons-react";
import { useEffect, useState, type FormEvent } from "react";

import {
  useUpdateScrapeConfig, type ParserType, type ScrapeConfig, type ScrapeConfigInput, type SourceType, type VatMode,
} from "../../api/competitors";
import { formatDateTime } from "../../format";
import { CSS_FIELDS, PARSER_LABELS, SOURCE_LABELS, VAT_LABELS } from "./labels";

function toInput(config: ScrapeConfig): ScrapeConfigInput {
  const { source_type, parser_type, parser_config, timeout_seconds, max_concurrency, max_retries,
          min_interval_seconds, prices_vat_mode, enabled } = config;
  return { source_type, parser_type, parser_config, timeout_seconds, max_concurrency, max_retries,
           min_interval_seconds, prices_vat_mode, enabled };
}

const entries = <T extends string>(labels: Record<T, string>) =>
  (Object.entries(labels) as [T, string][]).map(([value, label]) => ({ value, label }));

export function ScrapeConfigForm({ competitorId, config, readOnly }: {
  competitorId: number; config: ScrapeConfig; readOnly: boolean;
}) {
  const save = useUpdateScrapeConfig(competitorId);
  const [form, setForm] = useState<ScrapeConfigInput>(() => toInput(config));
  useEffect(() => setForm(toInput(config)), [config]);

  const set = <K extends keyof ScrapeConfigInput>(key: K, value: ScrapeConfigInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }));
  const setSelector = (key: string, value: string) =>
    setForm((f) => ({ ...f, parser_config: { ...f.parser_config, [key]: value } }));

  function submit(event: FormEvent) {
    event.preventDefault();
    const parser_config = form.parser_type === "css"
      ? Object.fromEntries(Object.entries(form.parser_config).filter(([, v]) => v && v.trim()))
      : {};
    save.mutate({ ...form, parser_config }, {
      onSuccess: () => notifications.show({ color: "green", message: "Nastavenia scrapingu boli uložené." }),
    });
  }

  const num = (key: "timeout_seconds" | "max_concurrency" | "max_retries" | "min_interval_seconds") =>
    (v: string | number) => set(key, Number(v === "" ? 0 : v));

  return (
    <form onSubmit={submit}>
      <Stack>
        <Group gap="xs">
          <Badge variant="light">Verzia parsera {config.parser_version}</Badge>
          <Badge variant="light" color={config.last_test_status === "succeeded" ? "green"
            : config.last_test_status ? "red" : "gray"}>
            Posledný test: {config.last_test_status ? `${config.last_test_status}, ${formatDateTime(config.last_test_at)}`
              : "zatiaľ nebol"}
          </Badge>
        </Group>
        {save.error && <Alert color="red">{save.error.message}</Alert>}
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <Select label="Zdroj dát" data={entries(SOURCE_LABELS)} value={form.source_type} allowDeselect={false}
                  disabled={readOnly} onChange={(v) => v && set("source_type", v as SourceType)} />
          <Select label="Parser" allowDeselect={false} disabled={readOnly} value={form.parser_type}
                  description={PARSER_LABELS[form.parser_type].description}
                  data={(Object.keys(PARSER_LABELS) as ParserType[]).map((p) => ({ value: p, label: PARSER_LABELS[p].label }))}
                  onChange={(v) => v && set("parser_type", v as ParserType)} />
        </SimpleGrid>

        {form.parser_type === "css" && (
          <>
            <Divider label="CSS selektory na produktovej stránke" labelPosition="left" />
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              {CSS_FIELDS.map((f) => (
                <TextInput key={f.key} label={f.label} placeholder={f.placeholder} required={f.key === "price"}
                           disabled={readOnly} value={form.parser_config[f.key] ?? ""} maxLength={500}
                           ff="monospace" onChange={(e) => setSelector(f.key, e.currentTarget.value)} />
              ))}
            </SimpleGrid>
          </>
        )}

        <Divider label="Limity" labelPosition="left" />
        <SimpleGrid cols={{ base: 2, sm: 4 }}>
          <NumberInput label="Časový limit (s)" min={1} max={60} disabled={readOnly} value={form.timeout_seconds}
                       onChange={num("timeout_seconds")} />
          <NumberInput label="Súbežnosť" min={1} max={4} disabled={readOnly} value={form.max_concurrency}
                       onChange={num("max_concurrency")} />
          <NumberInput label="Opakovania" min={0} max={5} disabled={readOnly} value={form.max_retries}
                       onChange={num("max_retries")} />
          <NumberInput label="Rozostup požiadaviek (s)" min={1} max={3600} disabled={readOnly}
                       value={form.min_interval_seconds} onChange={num("min_interval_seconds")} />
        </SimpleGrid>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <Select label="Ceny na webe sú" data={entries(VAT_LABELS)} value={form.prices_vat_mode} allowDeselect={false}
                  disabled={readOnly} onChange={(v) => v && set("prices_vat_mode", v as VatMode)} />
          <Switch label="Scraping povolený" mt={{ sm: 30 }} disabled={readOnly} checked={form.enabled}
                  onChange={(e) => set("enabled", e.currentTarget.checked)} />
        </SimpleGrid>

        <Group justify="space-between">
          <Tooltip label="Testovacia kontrola bude dostupná v etape 4 (scraping)">
            <Button variant="default" leftSection={<IconFlask size={16} />} disabled>Testovacia kontrola</Button>
          </Tooltip>
          {!readOnly && <Button type="submit" loading={save.isPending}>Uložiť nastavenia</Button>}
        </Group>
        {readOnly && <Text size="sm" c="dimmed">Nastavenia môže meniť iba administrátor.</Text>}
      </Stack>
    </form>
  );
}
