import { Alert, Button, Group, Stack } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import { useNavigate } from "react-router";
import { useCatalogStatus, useStatus, useSyncCatalog } from "../api/queries";
import { deriveBanners, type Banner } from "../lib/banners";

/** Under the nav, on every page: whatever stops every job (UX §4.2). */
export function Banners() {
  const status = useStatus();
  const catalog = useCatalogStatus();
  const sync = useSyncCatalog();
  const navigate = useNavigate();
  const banners = deriveBanners(status.data, catalog.data);
  if (banners.length === 0) return null;

  const run = (banner: Banner) => {
    if (banner.action.kind === "sync") sync.mutate();
    else void navigate(banner.action.to);
  };

  return (
    <Stack gap="xs" role="region" aria-label="Alerts">
      {banners.map((banner) => (
        <Alert
          key={banner.id}
          color={banner.tone}
          variant="light"
          icon={<IconAlertTriangle size={20} />}
          role="alert"
        >
          <Group justify="space-between" wrap="wrap" gap="sm">
            <span>{banner.message}</span>
            <Button
              size="sm"
              variant="light"
              color={banner.tone}
              loading={banner.action.kind === "sync" && sync.isPending}
              onClick={() => run(banner)}
            >
              {banner.action.label}
            </Button>
          </Group>
        </Alert>
      ))}
    </Stack>
  );
}
