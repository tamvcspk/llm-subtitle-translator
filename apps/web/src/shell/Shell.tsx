import { ActionIcon, Badge, Box, Container, Group, Stack, Title, UnstyledButton } from "@mantine/core";
import { IconLogout } from "@tabler/icons-react";
import { useEffect } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";
import { useMe, useSignOut, useSuggestionSummary } from "../api/queries";
import { isLibraryPath, lastLibraryPath, rememberLibraryPath } from "../lib/navigation";
import { Banners } from "./Banners";
import { SessionOverlay } from "./SessionOverlay";
import { StatusStrip } from "./StatusStrip";

export const NAV = [
  { to: "/library", label: "Library", end: false },
  { to: "/review", label: "Review", end: false },
  { to: "/activity", label: "Activity", end: false },
  { to: "/insights", label: "Insights", end: false },
  { to: "/settings", label: "Settings", end: false },
] as const;

export function Shell() {
  const me = useMe();
  const signOut = useSignOut();
  const location = useLocation();
  // Review stays out of the nav until the machine has proposed something (UX §4); its badge counts what is waiting.
  const suggestions = useSuggestionSummary().data;
  const total = suggestions ? suggestions.counts.pending + suggestions.counts.accepted + suggestions.counts.rejected : 0;
  const items = NAV.filter((item) => item.to !== "/review" || total > 0);
  const inLibrary = isLibraryPath(location.pathname);

  useEffect(() => {
    if (inLibrary) rememberLibraryPath(location.pathname, location.search);
  }, [inLibrary, location.pathname, location.search]);

  // P18e: tapping Library from outside restores the last visited Library/Series/Unit route; tapping Library again
  // while already inside the Library area returns to the Library list.
  const libraryTo = inLibrary ? "/library" : lastLibraryPath();

  return (
    <Container size={1100} py="md" px="md">
      <Stack gap="md">
        <Group justify="space-between" wrap="nowrap">
          <Title order={3}>GST Bazarr</Title>
          {me.data?.via === "session" && (
            <ActionIcon variant="subtle" aria-label="Sign out" loading={signOut.isPending} onClick={() => signOut.mutate()}>
              <IconLogout size={20} />
            </ActionIcon>
          )}
        </Group>
        <Box component="nav" aria-label="Main">
          <Group gap="xs" wrap="wrap">
            {items.map((item) =>
              item.to === "/library" ? (
                <UnstyledButton
                  key={item.to}
                  component={Link}
                  to={libraryTo}
                  px="md"
                  style={{ minHeight: 44, display: "inline-flex", alignItems: "center", borderRadius: 8 }}
                  className={inLibrary ? "nav-link active" : "nav-link"}
                  aria-current={inLibrary ? "page" : undefined}
                >
                  {item.label}
                </UnstyledButton>
              ) : (
                <UnstyledButton
                  key={item.to}
                  component={NavLink}
                  to={item.to}
                  end={item.end}
                  px="md"
                  style={{ minHeight: 44, display: "inline-flex", alignItems: "center", borderRadius: 8 }}
                  className="nav-link"
                >
                  {item.label}
                  {item.to === "/review" && suggestions && suggestions.pending > 0 && (
                    <Badge ml={8} size="sm" circle={suggestions.pending < 10} aria-label={`${suggestions.pending} pending`}>
                      {suggestions.pending}
                    </Badge>
                  )}
                </UnstyledButton>
              ),
            )}
          </Group>
        </Box>
        <Banners />
        <StatusStrip />
        <main>
          <Outlet />
        </main>
      </Stack>
      <SessionOverlay />
    </Container>
  );
}
