import { ActionIcon, Box, Container, Group, Stack, Title, UnstyledButton } from "@mantine/core";
import { IconLogout } from "@tabler/icons-react";
import { NavLink, Outlet } from "react-router";
import { useMe, useSignOut } from "../api/queries";
import { Banners } from "./Banners";
import { SessionOverlay } from "./SessionOverlay";
import { StatusStrip } from "./StatusStrip";

// Review joins this list in P13 (with a badge of pending suggestions); until then it does not exist.
export const NAV = [
  { to: "/", label: "Library", end: true },
  { to: "/activity", label: "Activity", end: false },
  { to: "/settings", label: "Settings", end: false },
] as const;

export function Shell() {
  const me = useMe();
  const signOut = useSignOut();
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
            {NAV.map((item) => (
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
              </UnstyledButton>
            ))}
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
