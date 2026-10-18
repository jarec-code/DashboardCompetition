/** Display formatting. Timestamps come from the API in UTC and are shown in the viewer's local time zone. */

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("sk-SK", { dateStyle: "short", timeStyle: "short" });
}

export function formatFrequency(hours: number | null | undefined): string {
  if (!hours) return "predvolená";
  if (hours % 24 === 0) return hours === 24 ? "denne" : `každé ${hours / 24} dni`;
  return `každých ${hours} h`;
}
