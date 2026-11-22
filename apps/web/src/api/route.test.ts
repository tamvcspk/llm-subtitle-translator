import { describe, expect, it } from "vitest";
import { ownerUrl, queryString, routeUrl } from "./route";

describe("queryString", () => {
  it("leaves out what is undefined or null and escapes the rest", () => {
    expect(queryString({ lang: "vi", q: undefined, at: null, offset: 0 })).toBe("?lang=vi&offset=0");
    expect(queryString({ q: "a&b c" })).toBe("?q=a%26b+c");
  });

  it("is empty when there is nothing to say", () => {
    expect(queryString(undefined)).toBe("");
    expect(queryString({ q: undefined })).toBe("");
  });
});

describe("routeUrl", () => {
  it("fills the path parameters and appends the query", () => {
    expect(routeUrl("/api/units/{p1}/context", { path: { p1: 7 }, query: { lang: "vi" } })).toBe("/api/units/7/context?lang=vi");
    expect(routeUrl("/api/units/{p1}/versions/{p2}/diff", { path: { p1: 7, p2: 3 }, query: { lang: "zh-CN" } })).toBe(
      "/api/units/7/versions/3/diff?lang=zh-CN",
    );
  });

  it("needs no query for a route that reads none", () => {
    expect(routeUrl("/api/units/{p1}", { path: { p1: 12 } })).toBe("/api/units/12");
  });

  it("does not compile a route, a path parameter or a query name the server does not have", () => {
    // Never called: the point is that `npm run typecheck` accepts each @ts-expect-error (the line really is a type error).
    const notRun = () => {
      // @ts-expect-error a route that is not in the document
      routeUrl("/api/units/{p1}/nope", { path: { p1: 1 } });
      // @ts-expect-error a path parameter is missing
      routeUrl("/api/units/{p1}/context", { query: { lang: "vi" } });
      // @ts-expect-error `language` is not a parameter of this route
      routeUrl("/api/units/{p1}/context", { path: { p1: 1 }, query: { language: "vi" } });
      // @ts-expect-error a route that reads no query takes none
      routeUrl("/api/units/{p1}", { path: { p1: 1 }, query: { lang: "vi" } });
    };
    expect(notRun).toBeTypeOf("function");
  });

  it("refuses at run time a path parameter it was not given", () => {
    const missing = { query: { lang: "vi" } } as unknown as Parameters<typeof routeUrl<"/api/units/{p1}/context">>[1];
    expect(() => routeUrl("/api/units/{p1}/context", missing)).toThrow("p1");
  });
});

describe("ownerUrl", () => {
  it("is the series route or the movie route of the same part", () => {
    expect(ownerUrl({ kind: "series", id: 4 }, "glossary", { lang: "vi" })).toBe("/api/series/4/glossary?lang=vi");
    expect(ownerUrl({ kind: "movie", id: 9 }, "glossary/history", { lang: "vi" })).toBe("/api/movies/9/glossary/history?lang=vi");
    expect(ownerUrl({ kind: "movie", id: 9 }, "style/preview")).toBe("/api/movies/9/style/preview");
  });

  it("does not compile a part only one prefix has, or a query name the route does not read", () => {
    const notRun = () => {
      // @ts-expect-error a movie has no glossary copy, so the part is not common to both prefixes
      ownerUrl({ kind: "series", id: 1 }, "glossary/copy");
      // @ts-expect-error `language` is not a query parameter of the glossary
      ownerUrl({ kind: "series", id: 1 }, "glossary", { language: "vi" });
    };
    expect(notRun).toBeTypeOf("function");
  });
});
