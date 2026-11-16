import { createTheme } from "@mantine/core";

// Touch first (UX §3 rule 6): every control is at least 44 px tall (see styles.css for the floor).
export const theme = createTheme({
  primaryColor: "teal",
  defaultRadius: "md",
  fontFamily: '"Inter Variable", system-ui, -apple-system, "Segoe UI", sans-serif',
  headings: { fontFamily: '"Inter Variable", system-ui, -apple-system, "Segoe UI", sans-serif' },
  components: {
    Button: { defaultProps: { size: "md" } },
    TextInput: { defaultProps: { size: "md" } },
    Select: { defaultProps: { size: "md" } },
  },
});
