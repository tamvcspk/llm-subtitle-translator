import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HashRouter } from "react-router";
import { ApiError } from "./api/client";
import { AppRoutes } from "./routes";
import { theme } from "./theme";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // A 401 raises the session overlay; asking again only repeats it. Other failures get one more try.
        retry: (count, error) => !(error instanceof ApiError && error.status === 401) && count < 1,
        refetchOnWindowFocus: true,
      },
    },
  });
}

const queryClient = makeQueryClient();

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <MantineProvider theme={theme} defaultColorScheme="auto">
        <HashRouter>
          <AppRoutes />
        </HashRouter>
      </MantineProvider>
    </QueryClientProvider>
  );
}
