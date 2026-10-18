import {
  IconBuildingStore, IconChartLine, IconHistory, IconLayoutDashboard, IconPackage, IconScale, IconSettings,
} from "@tabler/icons-react";
import type { ComponentType } from "react";

export interface NavItem {
  path: string;
  label: string;
  icon: ComponentType<{ size?: number; stroke?: number }>;
  /** Implementation stage in docs/IMPLEMENTATION_PLAN.md that brings this section. */
  stage: number;
}

export const NAV_ITEMS: NavItem[] = [
  { path: "/", label: "Dashboard", icon: IconLayoutDashboard, stage: 6 },
  { path: "/konkurencia", label: "Konkurencia", icon: IconBuildingStore, stage: 2 },
  { path: "/produkty", label: "Produkty", icon: IconPackage, stage: 3 },
  { path: "/porovnanie", label: "Cenové porovnanie", icon: IconScale, stage: 6 },
  { path: "/historia", label: "Cenová história", icon: IconChartLine, stage: 7 },
  { path: "/aktualizacie", label: "Aktualizácie a logy", icon: IconHistory, stage: 5 },
  { path: "/nastavenia", label: "Nastavenia", icon: IconSettings, stage: 1 },
];
