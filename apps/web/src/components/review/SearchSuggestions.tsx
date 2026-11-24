import { Stack, Text } from "@mantine/core";
import { useSearchSuggestions } from "../../api/queries";

/**
 * Google's "Search Suggestions" for a research suggestion. Its terms for Grounding with Google Search want them shown with
 * what the search gave, and unchanged: the HTML is Google's own, so it goes into a sandboxed frame as it came (no script,
 * no access to this page) and only the document around it is ours. Links open in a new tab.
 */
export function frameDocument(html: string): string {
  return `<!doctype html><meta charset="utf-8"><base target="_blank"><style>html,body{margin:0;background:transparent}</style>${html}`;
}

export function SearchSuggestions({ id }: { id: number | null | undefined }) {
  const found = useSearchSuggestions(id);
  if (typeof id !== "number" || found.isError || !found.data?.html) return null;
  return (
    <Stack gap={2}>
      <Text size="xs" c="dimmed" fw={600}>
        Search Suggestions
      </Text>
      <iframe
        title="Google Search Suggestions"
        srcDoc={frameDocument(found.data.html)}
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="no-referrer"
        loading="lazy"
        style={{ border: 0, width: "100%", height: 96, colorScheme: "normal" }}
      />
    </Stack>
  );
}
