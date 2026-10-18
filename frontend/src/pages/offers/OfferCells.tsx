import { ActionIcon, Anchor, Badge, Group, Menu, Stack, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconDots, IconLink, IconPlayerPause, IconPlayerPlay, IconRefresh, IconTrash, IconUnlink,
} from "@tabler/icons-react";
import { Link } from "react-router-dom";

import { useAssignOffer, useRemoveOffer, useUpdateOffer, type Offer } from "../../api/offers";
import { formatDateTime, formatMoney } from "../../format";
import { AVAILABILITY_LABELS, MATCH_LABELS, METHOD_LABELS } from "../products/labels";

export function OfferUrlCell({ offer }: { offer: Offer }) {
  const path = (() => { try { return new URL(offer.product_url).pathname; } catch { return offer.product_url; } })();
  return (
    <Stack gap={0}>
      {offer.current?.title && <Text size="sm" fw={500} lineClamp={1}>{offer.current.title}</Text>}
      <Anchor href={offer.product_url} target="_blank" rel="noopener noreferrer" size="xs">{path}</Anchor>
      {offer.variant && <Text size="xs" c="dimmed">variant: {offer.variant}</Text>}
    </Stack>
  );
}

/** Price, availability and freshness; unknown values are shown as such, never invented. */
export function OfferPriceCell({ offer }: { offer: Offer }) {
  const c = offer.current;
  if (!c || !c.last_checked_at) return <Text size="sm" c="dimmed">zatiaľ nekontrolované</Text>;
  const availability = AVAILABILITY_LABELS[c.availability] ?? AVAILABILITY_LABELS.unknown;
  return (
    <Stack gap={2}>
      <Group gap={6} wrap="nowrap">
        <Text size="sm" fw={600}>{formatMoney(c.price, c.currency)}</Text>
        {c.is_stale && <Badge size="xs" color="orange" variant="light">neaktuálne</Badge>}
      </Group>
      <Group gap={6} wrap="nowrap">
        <Badge size="xs" variant="dot" color={availability.color}>{availability.label}</Badge>
        <Text size="xs" c="dimmed">{formatDateTime(c.last_success_at)}</Text>
      </Group>
      {c.last_error && <Text size="xs" c="red" lineClamp={1} title={c.last_error}>{c.last_error}</Text>}
    </Stack>
  );
}

export function OfferMatchCell({ offer, showCompetitor = false }: { offer: Offer; showCompetitor?: boolean }) {
  const status = MATCH_LABELS[offer.match_status];
  return (
    <Stack gap={2}>
      {showCompetitor && <Text size="xs" c="dimmed">{offer.competitor_name}</Text>}
      {offer.product
        ? <Anchor component={Link} to={`/produkty/${offer.product.id}`} size="sm">{offer.product.name}</Anchor>
        : <Badge variant="light" color={status.color} style={{ minWidth: "max-content" }}>{status.label}</Badge>}
      {offer.product && offer.matching_method && (
        <Text size="xs" c="dimmed">{METHOD_LABELS[offer.matching_method]}
          {offer.matching_method === "name" && offer.match_score ? ` (${Math.round(Number(offer.match_score) * 100)} %)` : ""}
        </Text>
      )}
    </Stack>
  );
}

export function OfferActions({ offer, onAssign }: { offer: Offer; onAssign: (offer: Offer) => void }) {
  const assign = useAssignOffer();
  const update = useUpdateOffer();
  const remove = useRemoveOffer();
  const fail = (e: Error) => notifications.show({ color: "red", message: e.message });

  return (
    <Group gap={4} wrap="nowrap" justify="flex-end">
      <Tooltip label="Priradiť k produktu" withArrow>
        <ActionIcon variant="light" aria-label="Priradiť k produktu" onClick={() => onAssign(offer)}>
          <IconLink size={16} />
        </ActionIcon>
      </Tooltip>
      <Menu position="bottom-end" withinPortal>
        <Menu.Target>
          <ActionIcon variant="subtle" color="gray" aria-label="Ďalšie akcie"><IconDots size={16} /></ActionIcon>
        </Menu.Target>
        <Menu.Dropdown>
          {offer.match_status !== "ignored" && (
            <Menu.Item leftSection={<IconUnlink size={14} />}
                       onClick={() => assign.mutate({ id: offer.id, action: "ignore" }, { onError: fail })}>
              Ignorovať (nepárovať)
            </Menu.Item>
          )}
          <Menu.Item leftSection={<IconRefresh size={14} />}
                     onClick={() => assign.mutate({ id: offer.id, action: "auto" }, { onError: fail })}>
            Automatické párovanie
          </Menu.Item>
          {offer.status === "active" ? (
            <Menu.Item leftSection={<IconPlayerPause size={14} />}
                       onClick={() => update.mutate({ id: offer.id, status: "paused" }, { onError: fail })}>
              Pozastaviť sledovanie
            </Menu.Item>
          ) : (
            <Menu.Item leftSection={<IconPlayerPlay size={14} />}
                       onClick={() => update.mutate({ id: offer.id, status: "active" }, { onError: fail })}>
              Obnoviť sledovanie
            </Menu.Item>
          )}
          <Menu.Divider />
          <Menu.Item color="red" leftSection={<IconTrash size={14} />}
                     onClick={() => remove.mutate(offer.id, {
                       onError: fail,
                       onSuccess: (r) => notifications.show({ message: r.result === "archived"
                         ? "Ponuka má cenovú históriu, preto bola archivovaná." : "Ponuka bola odobratá." }),
                     })}>
            Odobrať ponuku
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </Group>
  );
}
