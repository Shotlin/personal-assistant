/**
 * Preview mode runs the real windows against fixture data in a plain browser.
 * It exists in `npm run dev` and in the separate showcase build
 * (VITE_SANI_PREVIEW=1). In the real app build both are false, so the bundler
 * removes the fixtures entirely.
 */
export const PREVIEW_BUILD: boolean =
  import.meta.env.DEV || import.meta.env.VITE_SANI_PREVIEW === "1";
