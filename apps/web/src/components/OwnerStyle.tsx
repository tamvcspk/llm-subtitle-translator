import { Alert, Loader, Stack } from "@mantine/core";
import { useState } from "react";
import { useOwnerStyle, useStyleSuggestions, type StyleOwner } from "../api/queries";
import type { StyleOverview } from "../api/types";
import { cleanDraft, type StyleDraft } from "../lib/style";
import { StyleForm } from "./StyleForm";
import { CompareVariant, TryStyle, type SampleEpisode } from "./StyleTools";

function Editor({
  owner,
  lang,
  overview,
  episodes,
  genres,
  suggestedPreset,
}: {
  owner: StyleOwner;
  lang: string;
  overview: StyleOverview;
  episodes: SampleEpisode[];
  genres: string[];
  suggestedPreset: { id: string; name: string } | null;
}) {
  const stored: StyleDraft = { preset: overview.preset, style: cleanDraft({ preset: "", style: overview.style }).style };
  const [draft, setDraft] = useState<StyleDraft>(stored);
  return (
    <Stack gap="lg">
      <StyleForm
        target={owner}
        lang={lang}
        stored={stored}
        draft={draft}
        onChange={setDraft}
        effective={overview.effective}
        presets={overview.presets}
        suggestion={suggestedPreset ? { genres, preset: suggestedPreset } : undefined}
        noun={owner.kind === "movie" ? "movie" : "episode"}
      />
      <TryStyle owner={owner} lang={lang} draft={draft} episodes={owner.kind === "series" ? episodes : []} />
      <CompareVariant lang={lang} draft={draft} episodes={episodes} />
    </Stack>
  );
}

/**
 * The Style tab of a series or a movie (wireframe 2b): preset, the axes, the live impact line, Try 20 lines and
 * Compare on a whole episode. The editor starts again from what the worker says whenever a save changes it.
 */
export function OwnerStyle({ owner, lang, episodes }: { owner: StyleOwner; lang: string; episodes: SampleEpisode[] }) {
  const style = useOwnerStyle(owner, lang);
  const suggestions = useStyleSuggestions(owner.kind === "series" ? owner.id : null);

  if (lang === "") return <Alert color="blue">There is no target language to work in: add one in Settings, Translation, then come back.</Alert>;
  if (style.isPending) return <Loader size="sm" aria-label="Loading style" />;
  if (style.isError) return <Alert color="red">{style.error.message}</Alert>;

  const best = suggestions.data?.presets[0] ?? null;
  const key = JSON.stringify([style.data.preset, style.data.style]);
  return (
    <Editor
      key={key}
      owner={owner}
      lang={lang}
      overview={style.data}
      episodes={episodes}
      genres={suggestions.data?.genres ?? []}
      suggestedPreset={best}
    />
  );
}
