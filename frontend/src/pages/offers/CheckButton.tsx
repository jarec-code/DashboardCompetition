import { ActionIcon, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconScan } from "@tabler/icons-react";

import { checkOffer, useJobRunner, type Job } from "../../api/jobs";
import type { Offer } from "../../api/offers";
import { formatMoney } from "../../format";

const CHANGE_LABELS: Record<string, string> = {
  initial: "prvá zaznamenaná cena", price_up: "cena stúpla", price_down: "cena klesla", currency: "zmena meny",
  availability: "zmena dostupnosti", original_price: "zmena pôvodnej ceny",
};

function report(job: Job) {
  if (job.status === "succeeded") {
    const e = job.result?.extracted;
    const changes = job.result?.change_types ?? [];
    notifications.show({
      color: "green", title: "Kontrola dokončená",
      message: job.result?.not_modified ? "Stránka sa od poslednej kontroly nezmenila."
        : `${formatMoney(e?.price, e?.currency)} – ${changes.length ? changes.map((c) => CHANGE_LABELS[c] ?? c).join(", ")
          : "bez zmeny"}`,
    });
  } else {
    notifications.show({ color: job.status === "cancelled" ? "yellow" : "red", title: "Kontrola neprebehla",
                         message: job.last_error ?? "Neznáma chyba." });
  }
}

/** Queues a manual check of one offer and reports the outcome when the worker finishes it. */
export function CheckButton({ offer }: { offer: Offer }) {
  const runner = useJobRunner(() => checkOffer(offer.id), report);
  return (
    <Tooltip label={runner.running ? "Kontrola prebieha…" : "Skontrolovať teraz"} withArrow>
      <ActionIcon variant="light" color="teal" aria-label="Skontrolovať teraz" loading={runner.running}
                  disabled={offer.status === "archived"} onClick={runner.run}>
        <IconScan size={16} />
      </ActionIcon>
    </Tooltip>
  );
}
