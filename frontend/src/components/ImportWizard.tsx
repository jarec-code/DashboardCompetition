import {
  Alert, Badge, Button, FileInput, Group, List, Modal, SegmentedControl, Select, Stack, Table, Text,
} from "@mantine/core";
import { IconFileImport } from "@tabler/icons-react";
import { useEffect, useState, type ReactNode } from "react";

import { useCommitImport, useDiscardImport, useUploadImport, type ImportRow, type RowStatus } from "../api/imports";

const STATUS: Record<RowStatus, { label: string; color: string }> = {
  ok: { label: "nový", color: "green" },
  warning: { label: "nový s upozornením", color: "yellow" },
  update: { label: "aktualizácia", color: "blue" },
  unchanged: { label: "bez zmeny", color: "gray" },
  review: { label: "na kontrolu", color: "orange" },
  duplicate: { label: "duplicita", color: "gray" },
  error: { label: "chyba", color: "red" },
};
const MARKETS = ["CZ", "SK", "PL", "HU", "AT", "DE"];

interface Props {
  opened: boolean;
  onClose: () => void;
  kind: "products" | "offers";
  title: string;
  help: ReactNode;
  competitorId?: number;
  /** Offer a default market for rows without a currency (product imports). */
  withMarket?: boolean;
  /** Short summary of a validated row for the preview table. */
  describe: (row: ImportRow) => ReactNode;
}

/** Two-step import: upload -> preview with a status and reason per row -> confirm. */
export function ImportWizard({ opened, onClose, kind, title, help, competitorId, withMarket, describe }: Props) {
  const upload = useUploadImport();
  const commit = useCommitImport();
  const discard = useDiscardImport();
  const [file, setFile] = useState<File | null>(null);
  const [filter, setFilter] = useState("all");
  const [market, setMarket] = useState("CZ");

  useEffect(() => {
    if (opened) { setFile(null); setFilter("all"); upload.reset(); commit.reset(); }
  }, [opened]);

  const batch = upload.data;
  const result = commit.data;
  const count = (s: RowStatus) => batch?.stats[s] ?? 0;
  const toSave = count("ok") + count("warning") + count("update");
  const rows = batch?.rows.filter((r) => filter === "all" || !["ok", "unchanged"].includes(r.status)) ?? [];

  function close() {
    if (batch && !result && batch.status === "preview") discard.mutate(batch.id);  // abandoned preview
    onClose();
  }

  return (
    <Modal opened={opened} onClose={close} size="xl" title={title}>
      <Stack>
        {!batch && (
          <>
            <Text size="sm" component="div">{help}</Text>
            <FileInput label="Súbor" accept=".csv,.xlsx,.xml,text/csv,application/xml" clearable value={file}
                       onChange={setFile} placeholder="CSV, Excel (.xlsx) alebo XML feed" leftSection={<IconFileImport size={16} />} />
            {withMarket && (
              <Select label="Trh pre riadky bez meny" description="Napr. Heureka feed alebo export bez stĺpca s menou"
                      data={MARKETS} value={market} allowDeselect={false} w={260}
                      onChange={(v) => setMarket(v ?? "CZ")} />
            )}
            {upload.error && <Alert color="red">{upload.error.message}</Alert>}
            <Group justify="flex-end">
              <Button variant="default" onClick={close}>Zrušiť</Button>
              <Button disabled={!file} loading={upload.isPending}
                      onClick={() => file && upload.mutate({ file, kind, competitorId, market: withMarket ? market : undefined })}>
                Načítať náhľad</Button>
            </Group>
          </>
        )}

        {batch && !result && (
          <>
            <Group gap="xs">
              <Text size="sm">{batch.file_name} ({batch.file_format}):</Text>
              {(Object.keys(STATUS) as RowStatus[]).filter((s) => count(s) > 0).map((s) => (
                <Badge key={s} variant="light" color={STATUS[s].color}>{STATUS[s].label}: {count(s)}</Badge>
              ))}
            </Group>
            <SegmentedControl size="xs" value={filter} onChange={setFilter} w="fit-content"
                              data={[{ value: "all", label: "Všetky riadky" }, { value: "problems", label: "Iba problémy" }]} />
            <Table.ScrollContainer minWidth={640} mah={420} type="native">
              <Table striped verticalSpacing={4} stickyHeader>
                <Table.Thead><Table.Tr><Table.Th w={60}>Riadok</Table.Th><Table.Th w={150}>Stav</Table.Th>
                  <Table.Th>Údaje</Table.Th><Table.Th>Dôvod</Table.Th></Table.Tr></Table.Thead>
                <Table.Tbody>
                  {rows.map((r) => (
                    <Table.Tr key={r.row}>
                      <Table.Td>{r.row}</Table.Td>
                      <Table.Td><Badge variant="light" color={STATUS[r.status].color}>{STATUS[r.status].label}</Badge></Table.Td>
                      <Table.Td>{describe(r)}</Table.Td>
                      <Table.Td><Text size="xs" c={r.status === "error" ? "red" : r.status === "review" ? "orange" : "dimmed"}>{r.reason}</Text></Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
            {commit.error && <Alert color="red">{commit.error.message}</Alert>}
            <Group justify="space-between">
              <Text size="xs" c="dimmed">Uložia sa nové riadky a aktualizácie. Riadky na kontrolu, duplicity a chyby sa
                preskočia; pred uložením sa všetko ešte raz porovná s aktuálnym katalógom.</Text>
              <Group>
                <Button variant="default" onClick={close}>Zrušiť import</Button>
                <Button disabled={toSave === 0} loading={commit.isPending} onClick={() => commit.mutate(batch.id)}>
                  Uložiť {toSave} {toSave === 1 ? "riadok" : toSave < 5 && toSave > 0 ? "riadky" : "riadkov"}
                </Button>
              </Group>
            </Group>
          </>
        )}

        {result && (
          <>
            <Alert color={result.errors || result.skipped_at_commit.length ? "yellow" : "green"} title="Import dokončený">
              <Text size="sm">Spracované riadky: <b>{result.processed}</b> · nové: <b>{result.imported}</b> ·
                aktualizované: <b>{result.updated}</b> · bez zmeny: <b>{result.unchanged}</b> · na kontrolu:{" "}
                <b>{result.review}</b> · duplicity: <b>{result.duplicates}</b> · odmietnuté: <b>{result.errors}</b></Text>
              {result.skipped_at_commit.length > 0 && (
                <List size="xs" mt={4}>{result.skipped_at_commit.map((s) => (
                  <List.Item key={s.row}>Riadok {s.row}: {s.reason}</List.Item>))}</List>
              )}
            </Alert>
            <Group justify="flex-end"><Button onClick={onClose}>Zavrieť</Button></Group>
          </>
        )}
      </Stack>
    </Modal>
  );
}
