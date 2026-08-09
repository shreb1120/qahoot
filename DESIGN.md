---
name: Qaboom
description: Call compliance QA — every call graded against your own checklist, with the evidence on a timeline.
colors:
  ink: "#0f172a"
  ink-strong: "#334155"
  muted: "#475569"
  faint: "#5c6a80"
  decor: "#94a3b8"
  panel: "#ffffff"
  panel-recessed: "#fafbfc"
  ground: "#f5f6f8"
  line: "#e4e7ec"
  line-strong: "#d0d5dd"
  line-hover: "#b8c0cc"
  track: "#eef1f4"
  nav-idle: "#a5b0c7"
  nav-hover: "#e6eaf3"
  nav-mid: "#172554"
  showcase-field-a: "#1e2a52"
  showcase-field-b: "#241d52"
  action: "#4f46e5"
  action-deep: "#4338ca"
  speaker-a: "#4338ca"
  speaker-a-soft: "#e0e7ff"
  speaker-b: "#0f766e"
  speaker-b-soft: "#ccfbf1"
  pass: "#047857"
  pass-soft: "#ecfdf5"
  fail: "#b91c1c"
  fail-soft: "#fef2f2"
  critical: "#7f1d1d"
  warn: "#b45309"
  warn-soft: "#fffbeb"
  busy: "#6d28d9"
  busy-soft: "#f5f3ff"
  busy-line: "#ddd6fe"
typography:
  display:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 800
    lineHeight: 1.1
    letterSpacing: "-0.02em"
  metric:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 700
    lineHeight: 1.25
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    letterSpacing: "0.03em"
  numeric:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    fontFeature: "tnum 1"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
  print:
    fontFamily: "Helvetica, Arial, sans-serif"
    fontSize: "10pt"
    fontWeight: 400
rounded:
  micro: "2px"
  sm: "4px"
  md: "6px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "14px"
  lg: "16px"
components:
  button-primary:
    backgroundColor: "{colors.action}"
    textColor: "{colors.panel}"
    rounded: "{rounded.sm}"
    padding: "10px 16px"
    typography: "{typography.body}"
  button-primary-hover:
    backgroundColor: "{colors.action-deep}"
  button-secondary:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink-strong}"
    rounded: "{rounded.sm}"
    padding: "7px 12px"
    height: "32px"
    typography: "{typography.label}"
  button-secondary-hover:
    backgroundColor: "{colors.panel-recessed}"
  input:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink-strong}"
    rounded: "{rounded.sm}"
    height: "32px"
    padding: "0 8px"
    typography: "{typography.label}"
  panel:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.md}"
  panel-header:
    backgroundColor: "{colors.panel-recessed}"
    textColor: "{colors.muted}"
    padding: "10px 14px"
    typography: "{typography.label}"
  verdict-pass:
    backgroundColor: "{colors.pass-soft}"
    textColor: "{colors.pass}"
    rounded: "{rounded.sm}"
    padding: "3px 8px"
    typography: "{typography.label}"
  verdict-fail:
    backgroundColor: "{colors.fail-soft}"
    textColor: "{colors.fail}"
    rounded: "{rounded.sm}"
    padding: "3px 8px"
    typography: "{typography.label}"
  verdict-critical:
    backgroundColor: "{colors.critical}"
    textColor: "{colors.panel}"
    rounded: "{rounded.sm}"
    padding: "3px 8px"
    typography: "{typography.label}"
---

# Design System: Qaboom

## Overview

**Creative North Star: "The Instrument Panel"**

Qaboom is read by two people with different clocks. A frontline reviewer lives
in it for hours, scanning tables of IDs, timestamps and verdicts. A compliance
manager opens it to answer one question — is this defensible? — and to decide
who needs coaching. The system serves the first without failing the second: it
behaves like instrumentation rather than a brochure. Surfaces are flat, dense
and hairline-ruled; nothing is decorated that could instead be measured.

The register is the call-intelligence category executed at full fidelity. That
was a deliberate commitment, not a default: the product needs to read as a peer
of the tools a buyer is already evaluating, so convention is the point and
invention would cost trust. Distinctiveness lives in one place — the graded
call itself, where a speaker timeline, evidence markers and a synced transcript
turn a compliance verdict into something you can hear.

Colour is rationed hard. The interface is near-neutral so that green, red and
dark red mean exactly one thing each: passed, failed, failed catastrophically.
The brand indigo is an action and wayfinding colour, never a mood. Two further
hues — indigo and teal — are reserved as *data*: they identify speakers on the
timeline, in the talk-share readout and in the transcript, and they are never
used decoratively anywhere else.

**Key Characteristics:**
- Flat by default; hairlines and tonal steps carry structure, not shadows
- Dense over airy — more rows visible beats more whitespace
- Tabular figures everywhere a number can be compared
- Semantic colour reserved for verdicts; brand colour reserved for action
- Speaker colour is information, not ornament
- Every text tier clears WCAG AA; one tier is explicitly non-text

## Colors

A near-neutral cool-grey field with a single indigo action colour, two reserved
speaker hues, and a three-step verdict scale that nothing else may borrow.

### Primary
- **Signal Indigo** (`#4f46e5`): the only action colour. Primary buttons and the
  one thing on a screen the user is meant to press. Never used for status.
- **Deep Indigo** (`#4338ca`): links, timestamp buttons, primary hover, and
  Speaker A's identity on the timeline.

### Secondary
- **Evidence Teal** (`#0f766e`): Speaker B. Exists only to be distinguishable
  from Speaker A at a glance in a horizontal band. Not a general accent.

### Tertiary
- **Working Violet** (`#6d28d9` on `#f5f3ff`): a call currently being analysed.
  Deliberately outside the verdict scale — work in progress is not a result.
- **Verdict Green** (`#047857` on `#ecfdf5`): a passed call or a covered item.
- **Verdict Red** (`#b91c1c` on `#fef2f2`): a failed call or a missing item.
- **Critical Oxblood** (`#7f1d1d`): reserved exclusively for auto-fail — a call
  that failed outright. It is the only verdict rendered as a solid fill, because
  it is the only one that ends the conversation.
- **Caution Amber** (`#b45309` on `#fffbeb`): configuration is missing or a
  process is stalling. Never a verdict.

### Neutral
- **Ink** (`#0f172a`): page titles and primary numerics.
- **Strong Ink** (`#334155`): body text, table cell content.
- **Muted** (`#475569`): secondary text, panel header labels.
- **Faint** (`#5c6a80`): tertiary text and field labels — the AA floor. 5.07:1
  on the ground, 5.49:1 on a panel. The earlier value cleared 4.5:1 on white but
  reached only 4.40:1 on the page ground, which is where much of this text sits.
- **Decor** (`#94a3b8`): **non-text only.** Empty-state glyphs and rules.
- **Ground** (`#f5f6f8`), **Panel** (`#ffffff`), **Recessed** (`#fafbfc`):
  the three surface levels. Recessed marks panel headers and inline editors.
- **Line** (`#e4e7ec`) / **Strong Line** (`#d0d5dd`) / **Hover Line**
  (`#b8c0cc`): dividers, control borders, and the border a control takes on hover.
- **Track** (`#eef1f4`): the groove behind every meter, progress bar and the
  call timeline. One value, so a filled bar reads the same everywhere.
- **Nav Idle** (`#a5b0c7`) / **Nav Hover** (`#e6eaf3`): sidebar link text on the
  dark chrome, tinted from the surface hue rather than neutral grey.

### Named Rules

**The Verdict Monopoly Rule.** Green, red and oxblood mean pass, fail and
auto-fail. Nothing else on any screen may use them — not a chart series, not a
hover state, not an icon. If a new element needs to signal something, it takes
a neutral or the action colour.

**The Non-Text Floor Rule.** `decor` (`#94a3b8`) is 2.6:1 and may never carry
text. Every other tier clears 4.5:1 against **the ground**, not merely against
`panel` — the ground is the darker of the two surfaces and the one that decides.
Verify new text colours against `ground`. When a value looks too light for its
job, the answer is the next tier up, never a new lighter value.

**The One Ramp Rule.** Text colour comes from `ink` / `ink-2` / `muted` /
`faint` — bound in `tailwind.config.js` to the CSS custom properties, so a token
change reaches every template. Reaching for a raw `text-slate-*` utility puts
that element outside the system, which is exactly how `faint` sat at 4.40:1
across eight surfaces while the token itself read as fixed.

**The Speaker Colour Rule.** Indigo and teal identify speakers. A surface that
is not about who was talking does not get to use them as accents.

## Typography

**Display / Body Font:** Inter (with `system-ui`, `sans-serif`), self-hosted as
a variable font, latin and latin-ext subsets.
**Mono:** `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` — used only
for auto-fail phrase tokens, where exact characters are the point.
**Print:** Helvetica / Arial in the exported PDF. The PDF renderer (xhtml2pdf)
cannot embed the web font, so print is a declared second medium rather than
drift — it inherits the palette and the verdict vocabulary, not the typeface.

**Character:** A workhorse UI face chosen for the job rather than the mood.
Disambiguated `1/l/I`, a tall x-height and tabular figures are what make a
table of ALV ids, timestamps and percentages scannable at 12–14px. The
personality of this product is in its density and precision, not its lettering.

### Hierarchy
- **Display** (800, 28px, 1.1, `-0.02em`): page titles, one per screen.
- **Metric** (700, 24px, 1.15, `-0.02em`): dashboard readouts and the report
  score. Always tabular.
- **Title** (700, 18px, 1.25): panel and card headings.
- **Body** (400–500, 14px, 1.5): body copy, table cells, controls. Cap evidence
  and prose at ~58ch.
- **Label** (600, 12px, `0.03em`, often uppercase): panel headers, field labels,
  metric labels, secondary metadata.

### Named Rules

**The Five Steps Rule.** 28 / 24 / 18 / 14 / 12px, and no others *inside the
app*. 24px exists solely for metric readouts, which need to outweigh a panel
title without competing with the page title. A value that feels like it needs
16px, 20px or 13px is a weight or colour problem, not a missing size.

**The Two Registers Rule.** The public marketing pages run a second, larger
ramp — 48 / 30 / 18 / 16px, declared as `--mk-fs-*` — and that is deliberate,
not drift. The app scale tops out at 28px because it is read at desk distance,
a hundred rows at a time; a landing page is read once, at arm's length, by
someone deciding whether to care. The two registers share everything else:
family, palette, radii, the hairline-not-shadow rule. A visitor who signs up
has to recognise the product they were shown, and that recognition is worth
more than a louder page.

The boundary is the `.mk-*` prefix. An app surface reaching for `--mk-fs-h1` is
a mistake; so is a marketing page reaching for `--fs-display`.

**The Tight Floor Rule.** The 14→12px step is 1.17× on purpose. 12px is the
floor for readable secondary text in a dense table, and below body the
distinction is carried by weight, case and colour instead of size. Do not add
an 11px tier to widen the ratio.

**The Tabular Rule.** Any figure a user might compare down a column — score,
duration, count, date, phone, ID — gets `font-variant-numeric: tabular-nums`.

## Layout

Content sits in a `max-w-6xl` centred container with a fixed 256px dark sidebar
from 1024px up, and an off-canvas drawer below it. Auth screens opt out of the
container entirely via an overridable `main` block and run full-bleed.

Spacing is a tight 4px-based rhythm: 14px panel padding, 16px between panels,
8px within a control group. Density is the point — a table row is ~8px vertical
padding, not 12.

The report is the system's reference composition: a full-width sticky transport
across the top, then a two-column split of working surface and evidence rail
(`1fr` + `22rem`) that collapses to stacked below 1180px. The rail is sticky and
scrolls independently. Dashboard and history use a single column of panels with
a 4-up metric strip that halves to 2-up below 900px.

Every wide table lives in its own `overflow-x` container so the page body never
scrolls horizontally.

## Elevation & Depth

**This system is flat.** Depth comes from a 1px hairline and a one-step tonal
change (`panel` → `panel-recessed`), not from shadows. Panels at rest have no
shadow at all.

Shadow appears only where an element genuinely floats above the page, and there
are exactly three such cases.

### Shadow Vocabulary
- **Sticky lift** (`box-shadow: 0 1px 3px rgba(16,24,40,.06)`): the report
  transport, which overlaps content as it sticks.
- **Toast** (`box-shadow: 0 8px 24px rgba(15,23,42,.22)`): the completion toast,
  which is genuinely above everything.
- **Showcase** (`box-shadow: 0 24px 48px -12px rgba(0,0,0,.5)`): the sample
  artifact on the dark auth panel only.

Focus is a 3px translucent indigo ring (`rgba(67,56,202,.15–.25)`) plus a border
shift — never a bare `outline: none`.

### Named Rules

**The Earned Shadow Rule.** A shadow requires a real z-relationship: the element
must overlap content. Decorative elevation on a resting panel is a defect.

## Shapes

Three radii and nothing between them: **6px** for panels, tables and inline
editors; **4px** for controls, badges and tags; **2px** for marks under 8px tall
— meter fills, timeline grooves, sparkline bars, evidence markers — where 4px
would round them into pills. Pills (`999px`) survive only on the speaker/verdict
dot and the numeral chips, where the shape is a dot rather than a container.

Borders are 1px. The system uses one thicker mark, and it is load-bearing: a
missing checklist item carries `inset 2px 0 0` in verdict red on its first cell,
so a failed row is findable by shape when scanning a long table.

Evidence and guidance are set as citations — indented behind a 1px left rule,
capped at 58ch — everywhere they appear: report evidence, checklist guidance,
auto-fail rationale.

### Named Rules

**The Three Radii Rule.** 6px contains, 4px controls, 2px marks. The 2px tier is
only available to elements under 8px tall; anything larger takes 4px. A fourth
value is drift.

## Components

### Buttons
- **Shape:** slightly softened corners (4px).
- **Primary:** Signal Indigo fill, white text, 10px/16px padding. One per view;
  it marks the single action the screen exists for.
- **Secondary:** white fill, strong-line border, 32px tall, label type. Used for
  everything else, including destructive-adjacent navigation.
- **Hover / Focus:** primary deepens to `action-deep`; secondary fills to
  `panel-recessed` and darkens its border. Focus always adds the 3px ring.
- **Destructive:** text-only in verdict red, with a soft red hover wash. Never
  a filled red button — a filled red reads as a verdict.

### Cards / Containers
- **Corner Style:** 6px.
- **Background:** `panel`, with a `panel-recessed` header strip.
- **Shadow Strategy:** none at rest (see Elevation).
- **Border:** 1px `line`.
- **Internal Padding:** 14–16px.

### Inputs / Fields
- **Style:** 32px tall, 1px `line-strong` border, 4px radius, label-size text.
- **Label:** always visible above the field, in `faint` label type. Placeholder
  is an example, never the label.
- **Focus:** border shifts to Deep Indigo plus the 3px translucent ring.
- **Error:** message renders beneath the field in verdict red with a warning
  glyph, and the field takes `aria-invalid`.

### Navigation
- Fixed 256px sidebar on a dark indigo-to-navy vertical gradient — the only
  gradient in the system, and the only dark chrome. Links are 14px medium in a
  desaturated blue-grey; the active item takes a translucent indigo wash, white
  text, and a 3px left marker. Below 1024px it becomes an off-canvas drawer with
  a backdrop, focus trap, Escape-to-close and focus return.

### Tables
- Header row in `panel-recessed` with uppercase label type; 1px row dividers;
  ~8px vertical cell padding; hover fills to `panel-recessed`.
- A row representing a failed or missing thing takes a near-white red tint plus
  the 2px inset marker.
- Rows that navigate somewhere are clickable in full, but always contain a real
  link so keyboard and screen-reader users have a target.

### Call Transport (signature component)
The system's defining element and the reason the report exists. A sticky bar
holding: a circular dark play control; a horizontal track of speaker bands drawn
from real utterance timings, coloured by speaker; diamond evidence markers above
the track, one per graded item and auto-fail phrase that carries a timestamp;
and a 2px ink playhead. The whole track is a `role="slider"` — clickable,
draggable, and keyboard-operable with arrows, Home/End and Space.

Every timestamp elsewhere on the page — in the items table, the auto-fail table,
the transcript — aims this one transport. Clicking any of them seeks the audio
and highlights the matching transcript line.

**The timeline never invents data.** A call with no usable speaker timing
renders without a timeline rather than with a decorative one, and the legend
lists only marker kinds actually placed.

## Do's and Don'ts

### Do:
- **Do** reach for a hairline and a tonal step before reaching for a shadow.
- **Do** give every number a comparison job `tabular-nums`.
- **Do** put evidence and guidance in the 58ch citation treatment, under the
  thing they qualify, rather than in a column of their own.
- **Do** label every form control visibly; placeholders disappear on first
  keystroke and were the source of a real accessibility failure here.
- **Do** keep the verdict vocabulary to pass / fail / critical, and render
  critical as the only solid fill.
- **Do** let a surface render honestly when data is missing — an empty state
  that says what to do beats a decorative placeholder.

### Don't:
- **Don't** use a colour below 4.5:1 for text. `decor` exists for glyphs only.
- **Don't** add a fifth type size or an 11px tier.
- **Don't** put green, red or oxblood on anything that is not a verdict.
- **Don't** use speaker indigo or teal as a general accent.
- **Don't** put a thick coloured border on the side or top of a card. The one
  2px mark in this system flags a failed row, and adding a second dilutes it.
- **Don't** render a chart, waveform or ratio from data the product does not
  actually have.
- **Don't** introduce a radius between 4px and 6px, or above 6px on a panel.
