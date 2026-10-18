import { Anchor, Badge, Group, Stack, Text, Tooltip } from "@mantine/core";
import { IconAlertTriangle, IconArrowDownRight, IconArrowUpRight, IconEqual } from "@tabler/icons-react";

import type { OfferCell } from "../../api/comparison";
import { formatDateTime, formatMoney, formatSignedMoney, formatSignedPercent } from "../../format";
import { AVAILABILITY_LABELS } from "../products/labels";

const REASONS: Record<NonNullable<OfferCell["reason"]>, string> = {
  no_price: "cena zatiaľ neznáma",
  no_own_price: "chýba vlastná cena pre tento trh",
  currency_mismatch: "iná mena – neporovnáva sa",
  vat_mismatch: "iný režim DPH – neporovnáva sa",
};

/** Direction is carried by the arrow icon and the words "lacnejší / drahší", not by color alone. */
function Difference({ cell }: { cell: OfferCell }) {
  const diff = Number(cell.diff_abs);
  if (diff === 0) {
    return <Badge variant="light" color="gray" leftSection={<IconEqual size={12} />}>rovnaká cena</Badge>;
  }
  const cheaper = diff < 0;
  return (
    <Tooltip label={`${formatSignedMoney(cell.diff_abs, cell.currency)} oproti vašej cene`} withArrow>
      <Badge variant="light" color={cheaper ? "red" : "green"} style={{ minWidth: "max-content" }}
             leftSection={cheaper ? <IconArrowDownRight size={12} /> : <IconArrowUpRight size={12} />}>
        {cheaper ? "lacnejší" : "drahší"} {formatSignedPercent(cell.diff_pct)}
      </Badge>
    </Tooltip>
  );
}

export function PriceCell({ cell }: { cell: OfferCell | undefined }) {
  if (!cell) return <Text size="sm" c="dimmed">—</Text>;
  const availability = AVAILABILITY_LABELS[cell.availability] ?? AVAILABILITY_LABELS.unknown;
  return (
    <Stack gap={3}>
      <Group gap={6} wrap="nowrap">
        <Anchor href={cell.product_url} target="_blank" rel="noopener noreferrer" fw={600} size="sm" c="inherit">
          {formatMoney(cell.price, cell.currency)}
        </Anchor>
        {cell.is_stale && cell.price !== null && (
          <Tooltip label={`Posledné úspešné overenie ${formatDateTime(cell.last_success_at)}`} withArrow>
            <Badge size="xs" variant="light" color="orange" leftSection={<IconAlertTriangle size={10} />}>neaktuálne</Badge>
          </Tooltip>
        )}
      </Group>
      {cell.comparable ? <Difference cell={cell} /> : <Text size="xs" c="dimmed">{REASONS[cell.reason!]}</Text>}
      {cell.price !== null && (
        <Group gap={6} wrap="nowrap">
          <Badge size="xs" variant="dot" color={availability.color}>{availability.label}</Badge>
          {cell.vat_unverified && <Text size="xs" c="dimmed">DPH neoverené</Text>}
        </Group>
      )}
      <Text size="xs" c="dimmed">{cell.last_success_at ? `overené ${formatDateTime(cell.last_success_at)}`
        : cell.last_error ? `chyba: ${cell.last_error}` : "zatiaľ nekontrolované"}</Text>
    </Stack>
  );
}
