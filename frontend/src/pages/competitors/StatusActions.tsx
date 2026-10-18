import { ActionIcon, Button, Group, Modal, Text, Tooltip } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconArchive, IconPlayerPause, IconPlayerPlay } from "@tabler/icons-react";
import type { ReactNode } from "react";

import { useUpdateCompetitor, type CompetitorStatus } from "../../api/competitors";

const MESSAGES: Record<CompetitorStatus, string> = {
  active: "Konkurent je aktívny.",
  paused: "Konkurent je pozastavený. Automatické kontroly sa nebudú spúšťať.",
  archived: "Konkurent je archivovaný. Jeho história zostáva v reportoch.",
};

/** Pause / resume / archive. Status changes never delete offers or price history.
 *  `compact` renders icon buttons (for table rows); their accessible names stay the same. */
export function StatusActions({ id, name, status, compact = false }: {
  id: number; name: string; status: CompetitorStatus; compact?: boolean;
}) {
  const update = useUpdateCompetitor(id);
  const [confirmOpened, confirm] = useDisclosure();

  function setStatus(next: CompetitorStatus) {
    update.mutate({ status: next }, {
      onSuccess: () => notifications.show({ color: "green", message: MESSAGES[next] }),
      onError: (e) => notifications.show({ color: "red", message: e.message }),
    });
  }

  function action(label: string, color: string, icon: ReactNode, onClick: () => void, variant = "light") {
    if (compact) {
      return (
        <Tooltip label={label} withArrow>
          <ActionIcon variant={variant} color={color} aria-label={label} loading={update.isPending} onClick={onClick}>
            {icon}
          </ActionIcon>
        </Tooltip>
      );
    }
    return (
      <Button size="sm" variant={variant} color={color} leftSection={icon} loading={update.isPending} onClick={onClick}>
        {label}
      </Button>
    );
  }

  return (
    <Group gap={6} wrap="nowrap">
      {status === "active" && action("Pozastaviť", "yellow", <IconPlayerPause size={16} />, () => setStatus("paused"))}
      {status !== "active" && action("Aktivovať", "green", <IconPlayerPlay size={16} />, () => setStatus("active"))}
      {status !== "archived" && action("Archivovať", "gray", <IconArchive size={16} />, confirm.open, "subtle")}
      <Modal opened={confirmOpened} onClose={confirm.close} title={`Archivovať ${name}?`}>
        <Text size="sm">
          Archivovaný konkurent sa nebude kontrolovať a v zozname sa zobrazí iba pri filtri „Archivovaný“.
          Ponuky ani cenová história sa nevymažú a kedykoľvek ho môžete znova aktivovať.
        </Text>
        <Group justify="flex-end" mt="md">
          <Button variant="default" onClick={confirm.close}>Zrušiť</Button>
          <Button color="gray" onClick={() => { confirm.close(); setStatus("archived"); }}>Archivovať</Button>
        </Group>
      </Modal>
    </Group>
  );
}
