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
    // A segment's text sat at the top of its pill: centre it in a touch-sized box (44 px) for every segmented control.
    SegmentedControl: {
      styles: {
        label: { display: "flex", alignItems: "center", justifyContent: "center", minHeight: 44 },
        control: { display: "flex" },
      },
    },
  },
});
