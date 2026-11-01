# Vendored libraries

The agents' pages load these from here, never from a CDN, so they work behind a proxy that blocks
`cdnjs.cloudflare.com` (as Orchestra's own pages do with theirs, `orchestra/web/static/vendor`).
Each file is the exact pinned release, unchanged.

| File | Library | Version | From | Licence |
|---|---|---|---|---|
| `marked.min.js` | marked (agent prose as markdown) | 12.0.2 | `cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js` | MIT, `marked.LICENSE.md` |
| `purify.min.js` | DOMPurify (sanitizes it) | 3.1.6 | `cdnjs.cloudflare.com/ajax/libs/dompurify/3.1.6/purify.min.js` | Apache-2.0 or MPL-2.0, `dompurify.LICENSE` |
| `highlight.min.js`, `github-dark.min.css` | highlight.js (code blocks) | 11.9.0 | `cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/` | BSD-3-Clause, `highlight.js.LICENSE` |

To upgrade one, replace its file with the new release's, unchanged, and update its row here.
