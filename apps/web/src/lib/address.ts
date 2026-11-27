/**
 * Generic forms of address are not names (FR-5.9, D-02): "Mom" is Lois to Malcolm and Ida to Lois, so as a character or as
 * an alias it would make different people one. The worker keeps the same list for English in
 * `subcore.memory.address.GENERIC_ADDRESS`; the form only says so before the worker has to. Other source languages are
 * not listed yet: English is used for them, as the worker does.
 */
export const GENERIC_ADDRESS: ReadonlySet<string> = new Set([
  "mom", "mommy", "mama", "mum", "mummy", "ma", "mother", "dad", "daddy", "papa", "pa", "pop", "pops", "father",
  "grandma", "grandpa", "granny", "grandmother", "grandfather", "nana", "grandpop",
  "sir", "ma'am", "madam", "miss",
  "honey", "sweetie", "sweetheart", "darling", "dear", "baby",
  "son", "kid", "boss", "doc", "coach", "buddy", "pal", "bro", "sis", "brother", "sister",
  "uncle", "aunt", "auntie",
]);

/** Is `name` only a form of address ("Mom", "the Sir", "my son")? A proper name that merely contains one is not. */
export function isGenericAddress(name: string): boolean {
  const text = name
    .split(/\s+/)
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
    .replace(/^(?:the|my|your|our)\s+/, "")
    .replace(/’/g, "'")
    .replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
  return GENERIC_ADDRESS.has(text);
}

/** The red line under a box that holds a generic form of address; `null` when it holds a name. */
export function genericProblem(name: string): string | null {
  return isGenericAddress(name) ? `"${name.trim()}" is a relative form of address, not a character's name.` : null;
}
