import { Alert, Center, Loader } from "@mantine/core";
import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { useSession } from "./auth/auth";
import { AppLayout } from "./layout/AppLayout";
import { NAV_ITEMS } from "./layout/navigation";
import { CompetitorDetailPage } from "./pages/competitors/CompetitorDetailPage";
import { CompetitorsPage } from "./pages/competitors/CompetitorsPage";
import { LoginPage } from "./pages/LoginPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { ProductDetailPage } from "./pages/products/ProductDetailPage";
import { ProductsPage } from "./pages/products/ProductsPage";
import { SettingsPage } from "./pages/SettingsPage";

const IMPLEMENTED: Record<string, ReactNode> = {
  "/konkurencia": <CompetitorsPage />,
  "/produkty": <ProductsPage />,
  "/nastavenia": <SettingsPage />,
};

export function App() {
  const session = useSession();
  const location = useLocation();

  if (session.isLoading) {
    return <Center mih="100vh"><Loader aria-label="Načítavam" /></Center>;
  }
  if (session.error) {
    return <Center mih="100vh" p="md"><Alert color="red">{session.error.message}</Alert></Center>;
  }

  if (!session.data) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace state={{ from: location.pathname }} />} />
      </Routes>
    );
  }

  const from = (location.state as { from?: string } | null)?.from;
  return (
    <Routes>
      <Route path="/login" element={<Navigate to={from && from !== "/login" ? from : "/"} replace />} />
      <Route element={<AppLayout />}>
        {NAV_ITEMS.map((item) => (
          <Route key={item.path} path={item.path}
                 element={IMPLEMENTED[item.path] ?? <PlaceholderPage item={item} />} />
        ))}
        <Route path="/konkurencia/:id" element={<CompetitorDetailPage />} />
        <Route path="/produkty/:id" element={<ProductDetailPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
