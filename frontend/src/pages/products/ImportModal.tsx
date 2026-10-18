import { Alert, Button, Code, FileInput, Group, List, Modal, Stack, Text } from "@mantine/core";
import { IconFileTypeCsv } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { useImportProducts } from "../../api/products";

export function ImportModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const importer = useImportProducts();
  const [file, setFile] = useState<File | null>(null);
  useEffect(() => {
    if (opened) { setFile(null); importer.reset(); }
  }, [opened]);
  const result = importer.data;

  return (
    <Modal opened={opened} onClose={onClose} size="lg" title="Import produktov z CSV">
      <Stack>
        <Text size="sm">
          Prvý riadok je hlavička. Povinné stĺpce <Code>name</Code> a <Code>price</Code>, voliteľné <Code>market</Code>,{" "}
          <Code>currency</Code>, <Code>isbn</Code>, <Code>ean</Code>, <Code>sku</Code>, <Code>internal_id</Code>,{" "}
          <Code>brand</Code>, <Code>category</Code>, <Code>priority</Code>. Oddeľovač čiarka alebo bodkočiarka,
          kódovanie UTF-8 alebo Windows-1250. Trh sa odvodí z meny (CZK → CZ, EUR → SK). Existujúce produkty sa neprepisujú.
        </Text>
        <FileInput label="Súbor CSV" accept=".csv,text/csv" leftSection={<IconFileTypeCsv size={16} />} clearable
                   value={file} onChange={setFile} placeholder="Vyberte súbor" />
        {importer.error && <Alert color="red">{importer.error.message}</Alert>}
        {result && (
          <Alert color={result.errors.length ? "yellow" : "green"} title="Výsledok importu">
            <Text size="sm">
              Importované: <b>{result.imported}</b>, preskočené duplicity: <b>{result.duplicates.length}</b>,
              chybné riadky: <b>{result.errors.length}</b>
            </Text>
            {[...result.errors, ...result.duplicates].length > 0 && (
              <List size="sm" mt="xs" spacing={2}>
                {result.errors.map((e) => <List.Item key={`e${e.row}`}>Riadok {e.row}: {e.reason}</List.Item>)}
                {result.duplicates.map((d) => <List.Item key={`d${d.row}`}>Riadok {d.row}: {d.reason}</List.Item>)}
              </List>
            )}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>{result ? "Zavrieť" : "Zrušiť"}</Button>
          <Button disabled={!file} loading={importer.isPending} onClick={() => file && importer.mutate(file)}>
            Importovať
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
