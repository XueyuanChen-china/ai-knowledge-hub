import "@mantine/core/styles.css";
import "@/app/globals.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MantineProvider } from "@mantine/core";

import { AppRouter } from "@/src/router";

const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("Root element #root not found");
}

createRoot(rootElement).render(
  <StrictMode>
    <MantineProvider
      defaultColorScheme="light"
      theme={{
        primaryColor: "blue",
        fontFamily:
          'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        defaultRadius: "sm",
      }}
    >
      <AppRouter />
    </MantineProvider>
  </StrictMode>,
);
