// A URL built from a route of the server's OpenAPI document (`schema.gen.ts`). The path parameters and the query names of a
// route are checked when the code compiles: a parameter the server does not read, a missing path parameter or a route it
// does not have is a type error, not a request that quietly ignores a name. Nothing here knows how a call is made; `api()`
// (`client.ts`) does that.
import { api } from "./client";
import type { paths } from "./schema.gen";
import type { AnswerOf } from "./types";

export type HttpMethod = "get" | "post" | "put" | "delete";

type ParametersOf<P extends keyof paths, M extends keyof paths[P]> = paths[P][M] extends { parameters: infer X } ? X : never;

/** The path parameters of a route (`{p1}`), or `never` when it has none. */
export type PathParams<P extends keyof paths, M extends keyof paths[P]> =
  ParametersOf<P, M> extends { path: infer T } ? (T extends object ? T : never) : never;

/** The query parameters of a route as the document names them, or `never` when it reads none. */
export type QueryParams<P extends keyof paths, M extends keyof paths[P]> =
  ParametersOf<P, M> extends { query?: infer Q } ? NonNullable<Q> : never;

/** What a call must give: the path parameters when the route has any, the query names it reads, nothing else. */
export type RouteArgs<P extends keyof paths, M extends keyof paths[P]> = ([PathParams<P, M>] extends [never]
  ? { path?: undefined }
  : { path: PathParams<P, M> }) &
  ([QueryParams<P, M>] extends [never] ? { query?: undefined } : { query?: QueryParams<P, M> });

type QueryValue = string | number | boolean | null | undefined;

/** `?a=1&b=x` for the given values, in the order they are given; an undefined or null value (and so an empty form field turned into `undefined`) is left out. */
export function queryString(query: Record<string, QueryValue> | undefined): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    params.set(name, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

/** The URL of a route: `routeUrl("/api/units/{p1}/context", { path: { p1: 7 }, query: { lang: "vi" } })`. */
export function routeUrl<P extends keyof paths, M extends HttpMethod & keyof paths[P] = "get" & keyof paths[P]>(
  route: P,
  args: RouteArgs<P, M>,
): string {
  const { path, query } = args as { path?: Record<string, string | number>; query?: Record<string, QueryValue> };
  const url = String(route).replace(/\{(\w+)\}/g, (_, name: string) => {
    const value = path?.[name];
    if (value === undefined) throw new Error(`Route ${String(route)} needs the path parameter ${name}.`);
    return encodeURIComponent(String(value));
  });
  return url + queryString(query);
}

/** A GET of a route of the document, typed on both sides: its parameters in, its answer out. */
export function apiGet<P extends keyof paths>(
  route: P,
  args: RouteArgs<P, "get" & keyof paths[P]>,
  signal?: AbortSignal,
): Promise<AnswerOf<P, "get">> {
  return api<AnswerOf<P, "get">>(routeUrl(route, args), { signal });
}

// -- an owner: a series or a movie, whose glossary, style and cards have the same routes under two prefixes ---------------

type SeriesPart = Extract<keyof paths, `/api/series/{p1}/${string}`> extends infer R
  ? R extends `/api/series/{p1}/${infer Rest}`
    ? Rest
    : never
  : never;
type MoviePart = Extract<keyof paths, `/api/movies/{p1}/${string}`> extends infer R
  ? R extends `/api/movies/{p1}/${infer Rest}`
    ? Rest
    : never
  : never;

/** The parts both prefixes have (`glossary`, `glossary/history`, `style`, `style/preview`, `cards`, ...). */
export type OwnerPart = SeriesPart & MoviePart;

/**
 * The URL of a part of an owner. The query names are checked against the series route (the movie route has the same ones;
 * `tests/test_openapi.py` keeps the two together).
 */
export function ownerUrl<S extends OwnerPart, M extends HttpMethod & keyof paths[`/api/series/{p1}/${S}`] = "get">(
  owner: { kind: "series" | "movie"; id: number },
  part: S,
  query?: QueryParams<`/api/series/{p1}/${S}`, M>,
): string {
  const prefix = owner.kind === "series" ? "/api/series" : "/api/movies";
  return `${prefix}/${encodeURIComponent(String(owner.id))}/${part}${queryString(query as Record<string, QueryValue> | undefined)}`;
}
