# typecase

A one-page tool for comparing typefaces. Static files only, so it runs from `index.html` directly or from
any static host (GitHub Pages: Settings → Pages → deploy from branch, root).

## Adding or removing a font

1. Edit the list in [fonts.toml](fonts.toml).
2. Run `uv run build.py` ([uv](https://docs.astral.sh/uv/) installs the script's dependencies on its own).
3. Commit everything, including `fonts/`, `fonts.css` and `fonts.js`.

The build works out where each font comes from:

- on **Google Fonts** or **Fontshare**: loaded from their CDN, with every weight and italic the family has
- anything else: the `.ttf`/`.otf` files installed on your machine are converted to `fonts/<family>/*.woff2`
  and served from this repo. That is redistribution, so only do it with fonts whose license allows it
  (OFL and similar); the licenses are collected in [fonts/LICENSES.md](fonts/LICENSES.md)
- fonts that can't be redistributed go under `installed_only` in `fonts.toml`: they stay in the list but
  only render on devices that have them installed

`fonts.css`, `fonts.js` and everything in `fonts/` are generated; don't edit them by hand.
