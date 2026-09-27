/**
 * Wordmark strip of the surfaces AURA connects to. Rendered as type rather than
 * artwork — no third-party logo files to license, and it retints with the theme.
 * Two identical tracks scroll in lockstep, so the loop has no seam.
 *
 * Only what actually works: Google (backend/app/services/google.py), the
 * channels in backend/app/services/channels.py, and the upload formats in
 * services/documents.py. It used to list Notion, Linear, Drive, Zoom, GitHub
 * and Outlook, none of which AURA connects to; the plugin hub itself shows
 * GitHub as "not built yet".
 */
const NAMES = [
  "Gmail",
  "Google Calendar",
  "Slack",
  "Telegram",
  "Any inbox, by forwarding",
  "PDF",
  "Word",
  "Markdown",
  "Your terminal",
];

export default function LogoMarquee() {
  return (
    <div className="marquee py-2">
      {[0, 1].map((copy) => (
        <div key={copy} aria-hidden={copy === 1}>
          {NAMES.map((n) => (
            <span
              key={n}
              className="whitespace-nowrap pr-14 text-[17px] font-medium tracking-tight text-faint transition-colors hover:text-muted"
            >
              {n}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}
