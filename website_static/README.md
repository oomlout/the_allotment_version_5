# Static website

Build the current garden from **Website → Save & build website** in Studio, then choose **Download website**. Or run from the tools repository:

```powershell
python -m allotment build the_allotment oomlout_main
```

Every build writes:

- `website_static/dist/` — the ready-to-upload website.
- `website_static/the_allotment.zip` — the same site, with `index.html` at the archive root.
- `allotment_static/<namespace>/<user>/` — the garden-specific Studio preview, retained for compatibility.

The shared export folder contains the most recently built/downloaded garden. Downloading in Studio selects the current garden's build. Generated files are ignored by Git; the source templates and styles are tracked.

## Upload

Extract **the contents** of `the_allotment.zip` into `/allotment/` on your web host. Open `https://your-domain.example/allotment/`. It also works in another subfolder or at a domain root. Explicit relative `index.html` links work without rewrite rules, Flask, JavaScript, a CDN or an external font service. You can also open `dist/index.html` directly on your computer.

The site includes one feed, a full page per published post, tag pages, and a Follow page. Images in the feed open their post. Full-size attachments open from the post page. Video uses native controls. The feed uses generated WebP previews where possible; animated images retain their animation. Original post files and saved crop choices are not changed.

Only published garden content is exported. Local drafts, account files, automation evidence and browser targets are not copied. Follow links come from the public profile, with explicit public-link account preferences taking precedence. A destination with `publish_profile_link: false` is excluded. Set that preference in Sowing configuration to control public visibility. No follow links are inferred from private chat destinations.

## Restyle

Use Website's colour, width, layout and spacing controls. Save additional rules in **Custom CSS** so they survive rebuilds. Editing a generated file directly works for a one-off upload, but the next build replaces it.

The stylesheet order is:

1. `assets/style.css`: base layout from `allotment/static/public.css`.
2. `assets/theme.css`: profile settings, generated at build time.
3. `assets/custom.css`: your saved overrides, loaded last.

Example:

```css
:root {
  --accent: #00b1b7;
  --link: #00676b;
  --yellow: #fec601;
  --ink: #151918;
  --background: #fcfdfc;
  --surface: #ffffff;
  --line: #dce3e1;
  --feed-width: 1120px;
  --radius: 8px;
  --font: "Segoe UI", Helvetica, Arial, sans-serif;
}
```

Stable selectors include `.public-top`, `.profile-intro`, `.feed`, `.post`, `.post-body`, `.post-text`, `.media-grid`, `.post-tags`, `.link-card`, `.follow-grid` and `.follow-card`. `.feed-single` identifies a detail page. The body exposes `layout-cards` / `layout-timeline` and `density-compact` / `density-comfortable`.

Templates are `allotment/templates/public_base.html`, `public.html` and `public_follow.html`. The builder is `allotment/website.py`. Public templates are separate from Studio templates and platform automation.

## Design references

OOBB colours follow the local `C:/gh/oomlout_oobb_version_5/RENDER_STYLE.md`: turquoise/teal `#00B1B7`, black, and sunflower yellow `#FEC601` in small accents. The presentation guide calls for plain sans-serif type, quiet backgrounds and object-led imagery. Web text uses a darker teal for legibility; the bright swatch is reserved for visual accents.

The implementation follows [W3C image guidance](https://www.w3.org/WAI/tutorials/images/) for saved alternative text and decorative logos, and [W3C reflow guidance](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html) for narrow screens. It includes a skip link and visible keyboard focus. Brand icon sources and their existing licenses travel with the export in `assets/logos/`.
