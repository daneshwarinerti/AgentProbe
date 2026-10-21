import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { EnterpriseDashboardPage } from "./pages/EnterpriseDashboardPage";

export function App() {
  return (
    <BrowserRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <Routes>
        <Route element={<EnterpriseDashboardPage />} path="/" />
        <Route element={<Navigate to="/" replace />} path="*" />
      </Routes>
    </BrowserRouter>
  );
}
