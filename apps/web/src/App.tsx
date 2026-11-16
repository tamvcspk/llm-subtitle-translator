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
        // A 401 raises the session overlay and any other 4xx (a series that is not there) is the answer: asking again
        // only repeats it. Other failures (network, 5xx) get one more try.
        retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 1,
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
