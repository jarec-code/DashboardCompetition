import { Alert, Badge, Button, Group, Loader, Modal, Stack, Table, Text, TextInput } from "@mantine/core";
import { IconFlask } from "@tabler/icons-react";
import { useEffect, useState, type ReactNode } from "react";

import { testCompetitor, useJobRunner, type Job } from "../../api/jobs";
import { formatMoney } from "../../format";
import { AVAILABILITY_LABELS } from "../products/labels";

const VAT = { with_vat: "s DPH", without_vat: "bez DPH", unknown: "neznáme (podľa nastavenia)" } as const;

function Result({ job }: { job: Job }) {
  if (job.status === "cancelled") return <Alert color="yellow">{job.last_error}</Alert>;
  if (job.status === "failed") {
    return (
      <Alert color="red" title={job.error_kind === "temporary" ? "Dočasná chyba" : "Extrakcia zlyhala"}>
        {job.last_error}
      </Alert>
    );
  }
  const r = job.result ?? {};
  const e = r.extracted;
  if (!e) return null;
  const availability = AVAILABILITY_LABELS[e.availability] ?? AVAILABILITY_LABELS.unknown;
  const rows: [string, ReactNode][] = [
    ["Názov", e.title ?? "—"],
    ["Cena", <b key="p">{formatMoney(e.price, e.currency)}</b>],
    ["Pôvodná cena", formatMoney(e.original_price, e.currency)],
    ["Dostupnosť", <Badge key="a" variant="dot" color={availability.color}>{availability.label}</Badge>],
    ["DPH", VAT[e.vat_mode as keyof typeof VAT] ?? e.vat_mode],
    ["Identifikátory", Object.entries(e.identifiers).map(([k, v]) => `${k.toUpperCase()} ${v}`).join(", ") || "—"],
    ["Variant", e.variant ? `${e.variant}${e.available_variants.length > 1 ? ` (dostupné: ${e.available_variants.join(", ")})` : ""}` : "—"],
    ["Parser", `${r.parser} · nastavenie v${r.parser_config_version} · zdroj ${e.source}`],
    ["Načítanie", `HTTP ${r.http_status}, ${r.elapsed_ms} ms${r.final_url !== r.url ? `, presmerované na ${r.final_url}` : ""}`],
  ];
  return (
    <Stack gap="xs">
      <Alert color="green">Extrakcia funguje. Test nič neuložil do cien ani histórie.</Alert>
      <Table verticalSpacing={4}>
        <Table.Tbody>
          {rows.map(([label, value]) => (
            <Table.Tr key={label}><Table.Td c="dimmed" w={140}>{label}</Table.Td><Table.Td>{value}</Table.Td></Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}

export function TestCheckModal({ opened, onClose, competitorId, defaultUrl }: {
  opened: boolean; onClose: () => void; competitorId: number; defaultUrl: string;
}) {
  const [url, setUrl] = useState(defaultUrl);
  const [variant, setVariant] = useState("");
  const runner = useJobRunner(() => testCompetitor(competitorId, url, variant));
  useEffect(() => { if (opened) { setUrl(defaultUrl); setVariant(""); runner.reset(); } }, [opened]);

  return (
    <Modal opened={opened} onClose={onClose} size="lg" title="Testovacia kontrola">
      <Stack>
        <Text size="sm" c="dimmed">
          Načíta jednu produktovú stránku s aktuálnymi nastaveniami scrapingu a ukáže, čo sa z nej vyčítalo.
          Rešpektuje robots.txt a rozostup požiadaviek. Nič sa neukladá.
        </Text>
        <TextInput label="URL produktovej stránky" required value={url} onChange={(e) => setUrl(e.currentTarget.value)} />
        <TextInput label="Variant (voliteľné)" placeholder="napr. tištěná" value={variant}
                   onChange={(e) => setVariant(e.currentTarget.value)} />
        {runner.error && <Alert color="red">{runner.error.message}</Alert>}
        {runner.running && (
          <Group gap="xs"><Loader size="sm" /><Text size="sm">{runner.job?.status === "running" ? "Načítavam stránku…"
            : "Čaká vo fronte…"}</Text></Group>
        )}
        {runner.job && !runner.running && <Result job={runner.job} />}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Zavrieť</Button>
          <Button leftSection={<IconFlask size={16} />} loading={runner.running} disabled={!url.trim()}
                  onClick={runner.run}>Spustiť test</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
