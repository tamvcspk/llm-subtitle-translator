import { Accordion, Alert, Button, PasswordInput, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { useChangePassword, useMe } from "../../api/queries";

/** Security (wireframe 6.7): the one console account's password. Nothing typed here is kept anywhere, not even as a draft. */
export function SecurityGroup() {
  const me = useMe();
  const change = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const tooShort = next.length > 0 && next.length < 8;

  const locked = me.data !== undefined && !me.data.can_change_password;
  const submit = () =>
    change.mutate(
      { current, new: next },
      {
        onSuccess: () => {
          setCurrent("");
          setNext("");
        },
      },
    );

  return (
    <Accordion.Item value="security">
      <Accordion.Control style={{ minHeight: 56 }}>
        <Text fw={600}>7. Security</Text>
      </Accordion.Control>
      <Accordion.Panel>
        {locked ? (
          <Alert color="blue">The console is not protected by a password here (it runs with sign-in switched off), so there is none to change.</Alert>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (current !== "" && next.length >= 8) submit();
            }}
          >
            <Stack gap="md" maw={420}>
              <PasswordInput label="Current password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.currentTarget.value)} />
              <PasswordInput
                label="New password (8+)"
                autoComplete="new-password"
                value={next}
                error={tooShort ? "Use at least 8 characters" : null}
                onChange={(event) => setNext(event.currentTarget.value)}
              />
              {change.isError && <Alert color="red">{change.error.message}</Alert>}
              {change.isSuccess && (
                <Text size="sm" c="teal.7" role="status">
                  ✓ Password updated. Other devices are signed out.
                </Text>
              )}
              <Button type="submit" loading={change.isPending} disabled={current === "" || next.length < 8} style={{ alignSelf: "flex-start" }}>
                Update password
              </Button>
            </Stack>
          </form>
        )}
      </Accordion.Panel>
    </Accordion.Item>
  );
}
