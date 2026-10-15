/**
 * Two builds come out of this one app.
 *
 *   npm run build           the hosted site (Vercel), served by Next itself
 *   npm run build:desktop   a static export for the desktop app, served by the
 *                           AURA backend from the same address as the API
 *
 * Every page is client-rendered, so the export needs nothing the hosted build
 * doesn't already avoid. NEXT_PUBLIC_AURA_DESKTOP is inlined at build time: it
 * swaps the marketing home page for a redirect into the app, because someone
 * who has already installed AURA doesn't need to be offered the download.
 */
const desktop = process.env.AURA_DESKTOP_BUILD === "1";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  ...(desktop
    ? {
        output: "export",
        images: { unoptimized: true },
        env: { NEXT_PUBLIC_AURA_DESKTOP: "1" },
      }
    : {}),
};

export default nextConfig;
