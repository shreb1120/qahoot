/** Mirrors the config that used to live inline in base.html. */
module.exports = {
  // Scans templates AND static JS: several Tailwind classes are applied at
  // runtime from script blocks (drop-zone states, the "View report" link the
  // history poller injects), and they only survive if the scanner sees them.
  content: ['./templates/**/*.html', './static/**/*.js'],
  corePlugins: { preflight: false },   // browser defaults are load-bearing here
  theme: {
    extend: {
      fontFamily: { sans: ['Inter', 'system-ui', 'sans-serif'] },
      colors: {
        /* Design system: Direction A — Indigo Enterprise */
        brand: { 50:'#eef2ff', 100:'#e0e7ff', 200:'#c7d2fe', 500:'#6366f1', 600:'#4f46e5', 700:'#4338ca', 800:'#3730a3', 900:'#312e81' },
        surface: { DEFAULT:'#f5f6fa', card:'#ffffff', ink:'#0f172a', muted:'#475569', border:'#e2e8f0' },
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
