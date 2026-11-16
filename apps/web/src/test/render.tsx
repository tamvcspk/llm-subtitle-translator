import { MantineProvider } from "@mantine/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import { makeQueryClient } from "../App";
import { AppRoutes } from "../routes";
import { theme } from "../theme";

export type Reply = { status?: number; body: unknown };
export type Replier = Reply | ((body: Record<string, unknown> | null) => Reply);

/** Answer `fetch` by path. A function sees the JSON body of the request, for endpoints that depend on it. */
export function serve(routes: Record<string, Replier>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const replier = routes[String(input)] ?? { status: 404, body: { error: "not found" } };
      const body = typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
      const reply = typeof replier === "function" ? replier(body) : replier;
      return new Response(JSON.stringify(reply.body), {
        status: reply.status ?? 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

/** The JSON bodies sent to a path, oldest first. */
export function sent(path: string): Array<Record<string, unknown> | null> {
  return vi
    .mocked(fetch)
    .mock.calls.filter(([input]) => String(input) === path)
    .map(([, init]) => (typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null));
}

export function show(path = "/") {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MantineProvider theme={theme}>
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
        </MemoryRouter>
      </MantineProvider>
    </QueryClientProvider>,
  );
}
