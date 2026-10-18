import { Alert, Anchor, Badge, Button, Group, Loader, Modal, Stack, Table, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconSearch } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { useAssignOffer, useCandidates, type Offer } from "../../api/offers";
import { METHOD_LABELS } from "../products/labels";

/** Choose which of our products a competitor offer corresponds to. */
export function AssignModal({ offer, onClose }: { offer: Offer | null; onClose: () => void }) {
  const [search, setSearch] = useState("");
  const [debounced] = useDebouncedValue(search, 300);
  const candidates = useCandidates(offer?.id ?? null, debounced.trim());
  const assign = useAssignOffer();
  useEffect(() => { setSearch(""); assign.reset(); }, [offer?.id]);

  function choose(productId: number, name: string) {
    if (!offer) return;
    assign.mutate({ id: offer.id, action: "assign", product_id: productId }, {
      onSuccess: () => { notifications.show({ color: "green", message: `Ponuka priradená k produktu ${name}.` }); onClose(); },
    });
  }

  return (
    <Modal opened={offer !== null} onClose={onClose} size="lg" title="Priradiť k vlastnému produktu">
      {offer && (
        <Stack>
          <div>
            <Text size="sm" fw={500}>{offer.current?.title ?? "Názov zatiaľ nie je známy (stránka ešte nebola skontrolovaná)"}</Text>
            <Anchor href={offer.product_url} target="_blank" rel="noopener noreferrer" size="xs">{offer.product_url}</Anchor>
          </div>
          <TextInput label="Hľadať produkt" placeholder="Názov, napr. Hravá matematika 2" leftSection={<IconSearch size={16} />}
                     value={search} onChange={(e) => setSearch(e.currentTarget.value)} data-autofocus />
          {assign.error && <Alert color="red">{assign.error.message}</Alert>}
          {candidates.isFetching && !candidates.data && <Loader size="sm" />}
          {candidates.data && candidates.data.length === 0 && (
            <Text size="sm" c="dimmed">
              {debounced ? "Žiadny produkt nezodpovedá hľadaniu." : "Bez návrhov. Vyhľadajte produkt podľa názvu."}
            </Text>
          )}
          {candidates.data && candidates.data.length > 0 && (
            <Table verticalSpacing="xs" highlightOnHover>
              <Table.Tbody>
                {candidates.data.map((c) => (
                  <Table.Tr key={c.product.id}>
                    <Table.Td>
                      <Text size="sm">{c.product.name}</Text>
                      {(c.isbn || c.ean) && <Text size="xs" c="dimmed">{c.isbn ?? c.ean}</Text>}
                    </Table.Td>
                    <Table.Td style={{ whiteSpace: "nowrap" }}>
                      <Badge variant="light" color={c.method === "identifier" ? "green" : "blue"}>
                        {METHOD_LABELS[c.method]} {Math.round(c.score * 100)} %
                      </Badge>
                    </Table.Td>
                    <Table.Td ta="right">
                      <Button size="xs" loading={assign.isPending} onClick={() => choose(c.product.id, c.product.name)}>
                        Priradiť
                      </Button>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
          <Group justify="flex-end"><Button variant="default" onClick={onClose}>Zavrieť</Button></Group>
        </Stack>
      )}
    </Modal>
  );
}
