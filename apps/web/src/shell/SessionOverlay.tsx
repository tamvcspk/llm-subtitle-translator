import { Button, Modal, Stack, Text } from "@mantine/core";
import { goToLogin, useSessionExpired } from "../api/session";

/** A blocking overlay, not a banner: once a call answers 401 nothing on the page can be trusted to save. */
export function SessionOverlay() {
  const expired = useSessionExpired();
  return (
    <Modal
      opened={expired}
      onClose={() => undefined}
      withCloseButton={false}
      closeOnClickOutside={false}
      closeOnEscape={false}
      centered
      title="Your session expired."
    >
      <Stack>
        <Text size="sm">Sign in again to carry on. You come back to this page.</Text>
        <Button onClick={goToLogin}>Sign in</Button>
      </Stack>
    </Modal>
  );
}
