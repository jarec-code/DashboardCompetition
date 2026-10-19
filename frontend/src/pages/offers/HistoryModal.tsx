import { Alert, Anchor, Badge, Group, Loader, Modal, Table, Text } from "@mantine/core";

import { useOfferHistory } from "../../api/jobs";
import type { Offer } from "../../api/offers";
import { formatDateTime, formatMoney } from "../../format";
import { AVAILABILITY_LABELS } from "../products/labels";

const CHANGE = {
  // From our point of view: a competitor getting cheaper is the threat (red), as in the comparison.
  initial: { label: "prvý záznam", color: "gray" }, price_up: { label: "cena ↑", color: "green" },
  price_down: { label: "cena ↓", color: "red" }, currency: { label: "mena", color: "blue" },
  availability: { label: "dostupnosť", color: "orange" }, original_price: { label: "pôvodná cena", color: "grape" },
} as Record<string, { label: string; color: string }>;

/** Recorded changes of one offer (a row exists only when something really changed). */
export function HistoryModal({ offer, onClose }: { offer: Offer | null; onClose: () => void }) {
  const history = useOfferHistory(offer?.id ?? null);
  return (
    <Modal opened={offer !== null} onClose={onClose} size="lg" title="História zmien ponuky">
      {offer && (
        <>
          <Text size="sm" fw={500}>{offer.current?.title ?? offer.product_url}</Text>
          <Anchor href={offer.product_url} target="_blank" rel="noopener noreferrer" size="xs">{offer.product_url}</Anchor>
          {history.isLoading && <Loader size="sm" mt="md" />}
          {history.error && <Alert color="red" mt="md">{history.error.message}</Alert>}
          {history.data?.length === 0 && (
            <Text size="sm" c="dimmed" mt="md">Zatiaľ bez záznamu. Prvý vznikne pri prvej úspešnej kontrole.</Text>
          )}
          {!!history.data?.length && (
            <Table mt="md" striped verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr><Table.Th>Čas</Table.Th><Table.Th ta="right">Cena</Table.Th><Table.Th>Dostupnosť</Table.Th>
                  <Table.Th>Zmena</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {history.data.map((row) => (
                  <Table.Tr key={row.observed_at}>
                    <Table.Td>{formatDateTime(row.observed_at)}</Table.Td>
                    <Table.Td ta="right">
                      {formatMoney(row.price, row.currency)}
                      {row.original_price && <Text size="xs" c="dimmed" td="line-through">{formatMoney(row.original_price, row.currency)}</Text>}
                    </Table.Td>
                    <Table.Td>{(AVAILABILITY_LABELS[row.availability] ?? AVAILABILITY_LABELS.unknown).label}</Table.Td>
                    <Table.Td>
                      <Group gap={4}>{row.change_types.map((c) => (
                        <Badge key={c} size="xs" variant="light" color={CHANGE[c]?.color ?? "gray"}>{CHANGE[c]?.label ?? c}</Badge>
                      ))}</Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </>
      )}
    </Modal>
  );
}
