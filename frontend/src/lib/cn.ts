/** Tiny classNames helper — avoids pulling in clsx/tailwind-merge for this scale. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
