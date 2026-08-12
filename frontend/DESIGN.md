# AURA — design system

Dark aurora is the default: a near-black canvas lit by violet and azure gradient
fields, with translucent glass surfaces stacked on top. A warm parchment theme is
still available as an opt-in.

## Themes

The palette lives in CSS variables in `app/globals.css` as **raw RGB channels**,
so Tailwind's `/opacity` modifiers keep working (`bg-panel/75`, `border-accent/30`).

| Theme | How it's applied |
| --- | --- |
| Dark aurora (default) | `:root` — no class needed |
| Warm parchment | `.warm` on `<html>` |

`.dark` is kept as an alias so the existing toggle keeps working, but it carries
no palette of its own. Theme selection happens in two places and both must agree:

- `app/layout.tsx` — the inline `THEME_BOOTSTRAP` script, which runs before first
  paint so there's no flash of the wrong palette
- `app/(app)/settings/page.tsx` — `applyTheme()`

Preference is stored in `localStorage` under `aura_theme` (`"dark"` | `"warm"`).

## Tokens

**Surfaces** — `canvas` (page), `panel` (cards), `raised` (nested/inputs)
**Text** — `ink` (primary), `muted` (secondary), `faint` (metadata)
**Edges** — `line`, a hairline derived from `--hairline` + `--hairline-a`
**Accent** — `accent`, `accent-soft` (use `soft` for small text and icons on
dark — plain `accent` is too dense at small sizes), `accent-dim` (a fixed 14%
tint for chip backgrounds)
**Categories** — `teal`, `amber`, `rose`, `violet`, `sage`, `azure`

These are **flat colours**, not scales. `bg-teal/20` works; `bg-teal-500` does not.

`--wash` scales the ambient aurora intensity per theme — it's dialled down to
0.42 in warm mode so the light palette doesn't get muddy.

### Legacy zinc ramp

Screens written against the original theme use `zinc-100`…`zinc-700`, where 100
was brightest and 700 faintest. That ramp is redefined in `globals.css` so those
screens retint automatically. **New work should use `ink` / `muted` / `faint`.**

## Component classes

Defined in `@layer components` in `globals.css`:

| Class | Use |
| --- | --- |
| `.panel` | Standard card — glass, hairline top highlight |
| `.panel-raised` | Nested surface inside a panel |
| `.glass` | Heavier glass for heroes, pricing, modals. Includes `relative overflow-hidden` |
| `.glow-card` | Gradient-border wrapper. Its single child gets the inner surface |
| `.hairline-top` | Luminous rule across the top of a section |
| `.btn-primary` / `.btn-ghost` / `.btn-quiet` | Buttons |
| `.input` | Form fields |
| `.chip` | Small status pill |
| `.pill` | The larger badge that sits above a hero headline |
| `.label` | Uppercase micro-label |
| `.display` | Tight-tracking headline |
| `.text-shine` | White → violet gradient headline fill |
| `.text-accent-gradient` | Violet → azure fill, for emphasised words |
| `.marquee` | Two-track infinite scroll; children must be identical |
| `.aurora-spot` | Absolutely-positioned blurred bloom. Needs a `relative` parent |

## Shared React primitives (`components/ui.tsx`)

- `PageHeader` — every top-level screen uses this. Gradient title, optional
  blurb, an aurora bloom, and an `action` slot for the primary button.
- `Stat` — the gradient headline number used in stat tiles.
- `Card` — `.panel` with padding. Pass `interactive` for hover lift.
- `Badge`, `Toggle`, `Progress`, `Skeleton`, `EmptyState`, `Modal`, `Spinner`
- `SkillLine`, `SkillBadge` — the terminal-style skill activity log
- `Markdown` — minimal renderer for chat bubbles

## Conventions

- **Aurora blooms need a `relative` ancestor.** `.aurora-spot` is
  `position: absolute`. `.glass` already includes `relative overflow-hidden`, so
  a bloom placed inside one is clipped to the card — usually what you want.
- **Overlays use `bg-canvas/75`, never `bg-ink/*`.** On dark, `ink` is near-white,
  so an ink scrim paints white over the page.
- **`overflow-x` is pinned on the app `<main>`** so header blooms can bleed past
  the content column without producing a horizontal scrollbar.
- **Dynamic class strings must be statically analysable.** Tailwind scans source
  text — `sm:${cond}` produces nothing. Keep variants in a lookup (see `STATS`
  in `app/page.tsx`).
- **Disabled/off states** use `opacity-55` plus, where it reads better,
  `grayscale`. Active states use `border-accent/25 bg-accent/12 shadow-glow-sm`
  and often a 2px left rail.

## Landing page

`app/page.tsx` composes `components/landing/*`:

`LandingNav` (client — condenses on scroll) · `HeroPreview` (a miniature of the
product built from DOM, so it stays crisp and themes with the palette) ·
`LogoMarquee` · `Bento` · `Pricing` (client — yearly toggle) · `Faq` (client —
accordion).

Third-party names in the logo strip are rendered as **type, not artwork** — no
logo files to license, and they retint with the theme.
