import {
  Alert, Badge, Button, Divider, Group, NumberInput, Select, SimpleGrid, Stack, Switch, Text, TextInput,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconFlask } from "@tabler/icons-react";
import { useEffect, useState, type FormEvent } from "react";

import {
  useUpdateScrapeConfig, type ScrapeConfig, type ScrapeConfigInput, type SourceType, type VatMode,
} from "../../api/competitors";
import { useParsers } from "../../api/jobs";
import { useOffers } from "../../api/offers";
import { formatDateTime } from "../../format";
import { CSS_FIELDS, SOURCE_LABELS, VAT_LABELS } from "./labels";
import { TestCheckModal } from "./TestCheckModal";

const XPATH_PLACEHOLDERS: Record<string, string> = {
  price: "//span[@class='price']", name: "//h1", original_price: "//del", currency: "//meta[@itemprop='priceCurrency']/@content",
  availability: "//*[contains(@class,'stock')]", identifier: "//*[@itemprop='gtin13']/@content",
};

function toInput(config: ScrapeConfig): ScrapeConfigInput {
  const { source_type, parser_type, parser_config, timeout_seconds, max_concurrency, max_retries,
          min_interval_seconds, prices_vat_mode, enabled } = config;
  return { source_type, parser_type, parser_config, timeout_seconds, max_concurrency, max_retries,
           min_interval_seconds, prices_vat_mode, enabled };
}

const entries = <T extends string>(labels: Record<T, string>) =>
  (Object.entries(labels) as [T, string][]).map(([value, label]) => ({ value, label }));

export function ScrapeConfigForm({ competitorId, baseUrl, config, readOnly }: {
  competitorId: number; baseUrl: string; config: ScrapeConfig; readOnly: boolean;
}) {
  const save = useUpdateScrapeConfig(competitorId);
  const parsers = useParsers();
  const firstOffer = useOffers({ page_size: 1 }, competitorId);
  const [testOpened, test] = useDisclosure();
  const [form, setForm] = useState<ScrapeConfigInput>(() => toInput(config));
  useEffect(() => setForm(toInput(config)), [config]);

  const parser = parsers.data?.find((p) => p.name === form.parser_type);
  const kind = parser?.config_kind ?? "none";
  const dirty = JSON.stringify(form) !== JSON.stringify(toInput(config));

  const set = <K extends keyof ScrapeConfigInput>(key: K, value: ScrapeConfigInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }));
  const setExpression = (key: string, value: string) =>
    setForm((f) => ({ ...f, parser_config: { ...f.parser_config, [key]: value } }));

  function submit(event: FormEvent) {
    event.preventDefault();
    const parser_config = kind === "none" ? {}
      : Object.fromEntries(Object.entries(form.parser_config).filter(([, v]) => v && v.trim()));
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
          <Badge variant="light">Verzia nastavenia parsera {config.parser_version}</Badge>
          <Badge variant="light" color={config.last_test_status === "succeeded" ? "green"
            : config.last_test_status ? "red" : "gray"}>
            Posledný test: {config.last_test_status
              ? `${config.last_test_status === "succeeded" ? "úspešný" : "neúspešný"}, ${formatDateTime(config.last_test_at)}`
              : "zatiaľ nebol"}
          </Badge>
        </Group>
        {save.error && <Alert color="red">{save.error.message}</Alert>}
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <Select label="Zdroj dát" data={entries(SOURCE_LABELS)} value={form.source_type} allowDeselect={false}
                  disabled={readOnly} onChange={(v) => v && set("source_type", v as SourceType)} />
          <Select label="Parser" allowDeselect={false} disabled={readOnly || !parsers.data} value={form.parser_type}
                  description={parser ? `${parser.description} (verzia ${parser.version})` : undefined}
                  data={(parsers.data ?? []).map((p) => ({ value: p.name, label: p.label }))}
                  onChange={(v) => v && set("parser_type", v)} />
        </SimpleGrid>

        {kind !== "none" && (
          <>
            <Divider label={kind === "css" ? "CSS selektory na produktovej stránke" : "XPath výrazy na produktovej stránke"}
                     labelPosition="left" />
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              {CSS_FIELDS.map((f) => (
                <TextInput key={f.key} label={f.label} required={f.key === "price"} disabled={readOnly} maxLength={500}
                           placeholder={kind === "css" ? f.placeholder : XPATH_PLACEHOLDERS[f.key]} ff="monospace"
                           value={form.parser_config[f.key] ?? ""}
                           onChange={(e) => setExpression(f.key, e.currentTarget.value)} />
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
          {!readOnly ? (
            <Button variant="default" leftSection={<IconFlask size={16} />} onClick={test.open} disabled={dirty}
                    title={dirty ? "Najprv uložte zmeny, test použije uložené nastavenia." : undefined}>
              Testovacia kontrola
            </Button>
          ) : <span />}
          {!readOnly && <Button type="submit" loading={save.isPending}>Uložiť nastavenia</Button>}
        </Group>
        {dirty && !readOnly && <Text size="xs" c="dimmed">Testovacia kontrola používa uložené nastavenia, najprv ich uložte.</Text>}
        {readOnly && <Text size="sm" c="dimmed">Nastavenia môže meniť iba administrátor.</Text>}
      </Stack>
      <TestCheckModal opened={testOpened} onClose={test.close} competitorId={competitorId}
                      defaultUrl={firstOffer.data?.items[0]?.product_url ?? baseUrl} />
    </form>
  );
}
