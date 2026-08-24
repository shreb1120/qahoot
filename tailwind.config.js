/** Mirrors the config that used to live inline in base.html. */
module.exports = {
  // Scans templates AND static JS: several Tailwind classes are applied at
  // runtime from script blocks (drop-zone states, the "View report" link the
  // history poller injects), and they only survive if the scanner sees them.
  content: ['./templates/**/*.html', './static/**/*.js'],
  corePlugins: { preflight: false },   // browser defaults are load-bearing here
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        display: ['Plus Jakarta Sans', 'Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        /* Steel-teal action brand (Phase 2). Speaker indigo stays in CSS vars. */
        brand: { 50:'#ecfeff', 100:'#cffafe', 200:'#a5f3fc', 500:'#06b6d4', 600:'#0e7490', 700:'#155e75', 800:'#155e75', 900:'#164e63' },
        surface: { DEFAULT:'#f5f6f8', card:'#ffffff', ink:'#0f172a', muted:'#475569', border:'#e4e7ec' },
        sidebar: { DEFAULT:'#0f172a', hover:'rgba(255,255,255,0.06)' },
        /* The documented text ramp, resolved from the CSS custom properties so
           there is exactly one source of truth. Templates previously reached for
           raw slate utilities, which meant a token change could not reach them —
           and text-slate-500 sat at 4.40:1 on the page ground. */
        ink:     'var(--ink)',
        'ink-2': 'var(--ink-2)',
        muted:   'var(--muted)',
        faint:   'var(--faint)',
        decor:   'var(--decor)',
        ok: '#047857',
        bad: '#b91c1c',
        warn: '#b45309',
      },
    },
  },
};
