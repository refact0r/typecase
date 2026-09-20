# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools", "brotli"]
# ///
"""Build the font assets for site/index.html from fonts.toml.

    uv run build.py

Writes, all under site/:
  fonts/<family>/*.woff2   self-hosted fonts, converted from the .ttf/.otf installed on this machine
  fonts/LICENSES.md        license of every self-hosted family
  fonts.css                @imports for Google Fonts / Fontshare + @font-face for everything in fonts/
  fonts.js                 the list the page renders

fonts.css is generated from the .woff2 files themselves, so the build also works on a machine where
the fonts aren't installed; conversion is just skipped for families that can't be found locally.
"""
import json
import re
import sys
import tomllib
import urllib.request
from collections import defaultdict
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).parent
SITE = ROOT / 'site'  # everything in here gets deployed
OUT = SITE / 'fonts'
FONT_DIRS = [Path.home() / 'Library/Fonts', Path('/Library/Fonts'),          # macOS
             Path.home() / '.local/share/fonts', Path('/usr/share/fonts')]   # Linux
GENERIC = {'system-ui', 'serif', 'sans-serif', 'monospace', 'cursive', 'fantasy',
           'ui-serif', 'ui-sans-serif', 'ui-monospace', 'ui-rounded'}
GOOGLE_META = 'https://fonts.google.com/metadata/fonts'
FONTSHARE_META = 'https://api.fontshare.com/v2/fonts?limit=1000'
GOOGLE_CHUNK = 12  # families per css2 request, keeps URLs short


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def num(x):
    return f'{x:g}'


# ---------------------------------------------------------------- font metadata

def describe(path):
    """Family, weight, style etc. read from a .ttf/.otf/.woff2 file."""
    font = TTFont(path, lazy=True)
    name = lambda i: (font['name'].getDebugName(i) or '').strip()
    os2 = font['OS/2']
    axes = {a.axisTag: (a.minValue, a.maxValue) for a in font['fvar'].axes} if 'fvar' in font else {}
    subfamily = f'{name(17)} {name(2)}'.lower()
    info = {
        'path': path,
        'family': name(16) or name(1),
        'weight': axes.get('wght') or (os2.usWeightClass,) * 2,
        'width': os2.usWidthClass,
        'stretch': axes.get('wdth'),
        'slant': axes.get('slnt'),
        # some fonts forget the italic bit, so also trust the style name and the slant angle
        'italic': bool(os2.fsSelection & 1) or 'italic' in subfamily or 'oblique' in subfamily
                  or font['post'].italicAngle != 0,
        'license': ' - '.join(filter(None, [license_line(name(13)), name(14)])),
        'copyright': name(0).split('\n')[0][:200],
    }
    font.close()
    return info


def license_line(text):
    """The line of a font's license description that names the license."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    return next((l for l in lines if 'licen' in l.lower()), lines[0] if lines else '')[:200]


def installed_fonts():
    found = defaultdict(list)
    for d in FONT_DIRS:
        for p in sorted(d.rglob('*')) if d.is_dir() else []:
            if p.suffix.lower() in ('.ttf', '.otf'):
                try:
                    info = describe(p)
                except Exception as e:
                    print(f'  ! skipping unreadable font {p.name}: {e}')
                    continue
                found[info['family'].lower()].append(info)
    return found


def pick_faces(faces):
    """One file per (weight, style), normal width only."""
    best_width = min((abs(f['width'] - 5), f['width']) for f in faces)[1]
    picked = {}
    for f in sorted(faces, key=lambda f: f['path'].name):
        if f['width'] != best_width:
            continue
        key = (f['weight'], f['italic'])
        if key in picked:
            print(f"  ! {f['path'].name} duplicates {picked[key]['path'].name} "
                  f"(same weight and style), ignoring it")
            continue
        picked[key] = f
    return list(picked.values())


# ---------------------------------------------------------------- sources

def convert(family, installed):
    """Convert the installed files of a family to fonts/<slug>/*.woff2. Returns False if there's nothing to host."""
    dest = OUT / slug(family)
    faces = installed.get(family.lower())
    if not faces:
        if any(dest.glob('*.woff2')):
            print(f'  {family}: not installed here, keeping the existing woff2 files')
            return True
        return False
    faces = pick_faces(faces)
    dest.mkdir(parents=True, exist_ok=True)
    wanted = set()
    for f in faces:
        src = f['path']
        out = dest / (slug(src.stem) + '.woff2')
        wanted.add(out)
        if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
            continue
        font = TTFont(src)
        font.flavor = 'woff2'
        font.save(out)
        print(f'  {family}: {src.name} -> {out.relative_to(SITE)}')
    for stale in set(dest.glob('*.woff2')) - wanted:
        print(f'  {family}: removing stale {stale.relative_to(SITE)}')
        stale.unlink()
    return True


def font_face(family, f):
    lines = [f'font-family: "{family}";',
             f"src: url(\"{f['path'].relative_to(SITE).as_posix()}\") format(\"woff2\");",
             f"font-weight: {' '.join(num(w) for w in dict.fromkeys(f['weight']))};"]
    if f['stretch']:
        lines.append(f"font-stretch: {num(f['stretch'][0])}% {num(f['stretch'][1])}%;")
    if f['slant'] and not f['italic']:
        # slnt axis: negative slnt leans right, which CSS calls a positive oblique angle
        lo, hi = sorted(-v for v in f['slant'])
        lines.append(f'font-style: oblique {num(lo)}deg {num(hi)}deg;')
    else:
        lines.append(f"font-style: {'italic' if f['italic'] else 'normal'};")
    lines.append('font-display: swap;')
    return '@font-face {\n' + ''.join(f'  {l}\n' for l in lines) + '}\n'


def google_spec(meta):
    """The `family=` value of a css2 URL covering every weight and italic the family has."""
    styles = meta['fonts'].keys()
    italic = any(s.endswith('i') for s in styles)
    wght = next((a for a in meta['axes'] if a['tag'] == 'wght'), None)
    if wght:
        weights = [f"{num(wght['min'])}..{num(wght['max'])}"]
    else:
        weights = sorted({s.rstrip('i') for s in styles}, key=int)
    spec = meta['family'].replace(' ', '+')
    if italic:
        return spec + ':ital,wght@' + ';'.join(f'{i},{w}' for i in (0, 1) for w in weights)
    if weights != ['400']:
        return spec + ':wght@' + ';'.join(weights)
    return spec


def fontshare_spec(meta):
    styles = meta['styles']
    variable = sorted(s['weight']['number'] for s in styles if s['is_variable'])
    numbers = variable or sorted(s['weight']['number'] for s in styles)
    return f"{meta['slug']}@{','.join(map(str, numbers))}"


# ---------------------------------------------------------------- build

def main():
    config = tomllib.loads((ROOT / 'fonts.toml').read_text())
    categories = config['categories']
    installed_only = config.get('installed_only', [])
    license_notes = config.get('licenses', {})
    names = [n for fonts in categories.values() for n in fonts]
    if dupes := {n for n in names if names.count(n) > 1}:
        sys.exit(f'listed more than once in fonts.toml: {", ".join(sorted(dupes))}')

    print('fetching Google Fonts and Fontshare catalogues…')
    google = {f['family']: f for f in json.loads(fetch(GOOGLE_META))['familyMetadataList']}
    fontshare = {f['name']: f for f in json.loads(fetch(FONTSHARE_META))['fonts']}

    from_google, from_fontshare, self_hosted = [], [], []
    for n in names:
        if n.lower() in GENERIC or n in installed_only:
            continue
        elif n in google:
            from_google.append(n)
        elif n in fontshare:
            from_fontshare.append(n)
        else:
            self_hosted.append(n)

    print('converting self-hosted fonts…')
    installed = installed_fonts() if self_hosted else {}
    missing = [n for n in self_hosted if not convert(n, installed)]
    if missing:
        sys.exit('not on Google Fonts or Fontshare, and not installed on this machine (check the spelling, '
                 f'or install the font and rebuild): {", ".join(missing)}')
    keep = {slug(n) for n in self_hosted}
    for d in OUT.iterdir() if OUT.is_dir() else []:
        if d.is_dir() and d.name not in keep:
            print(f'  removing fonts/{d.name}/ (no longer self-hosted)')
            for f in d.iterdir():
                f.unlink()
            d.rmdir()

    # everything below is generated from the woff2 files, not from the installed fonts
    imports, faces, licenses, unlicensed = [], [], [], []  # imports: (url, families it must deliver)
    for i in range(0, len(from_google), GOOGLE_CHUNK):
        chunk = sorted(from_google)[i:i + GOOGLE_CHUNK]
        imports.append(('https://fonts.googleapis.com/css2?'
                        + '&'.join('family=' + google_spec(google[n]) for n in chunk) + '&display=swap', chunk))
    for n in sorted(from_fontshare):  # one request each: Fontshare only answers the first f[] of a URL
        imports.append((f'https://api.fontshare.com/v2/css?f[]={fontshare_spec(fontshare[n])}&display=swap', [n]))
    print('checking the Google Fonts / Fontshare URLs…')
    for url, families in imports:
        css = fetch(url).decode()  # raises on a 400, e.g. when a weight range is off
        if absent := [n for n in families if f"'{n}'" not in css]:
            sys.exit(f'{url}\ndoes not define: {", ".join(absent)}')

    for n in sorted(self_hosted, key=str.lower):
        files = [describe(p) for p in sorted((OUT / slug(n)).glob('*.woff2'))]
        files.sort(key=lambda f: (f['italic'], f['weight']))
        faces += [font_face(n, f) for f in files]
        license = license_notes.get(n) or files[0]['license']
        if not license:
            unlicensed.append(n)
        licenses.append(f"| {n} | {license} | {files[0]['copyright']} |")
    if unlicensed:
        sys.exit('no license info inside these fonts. Check that they may be redistributed, then note the '
                 f'license under [licenses] in fonts.toml: {", ".join(unlicensed)}')

    header = '/* generated by build.py from fonts.toml, do not edit */\n'
    (SITE / 'fonts.css').write_text(
        header + ''.join(f'@import url("{u}");\n' for u, _ in imports) + '\n' + '\n'.join(faces))
    (SITE / 'fonts.js').write_text(
        header.replace('/*', '//').replace(' */', '')
        + f'const FONTS = {json.dumps(categories, indent=2, ensure_ascii=False)};\n'
        + f'const INSTALLED_ONLY = {json.dumps(installed_only, ensure_ascii=False)};\n')
    if licenses:
        (OUT / 'LICENSES.md').write_text(
            '# Licenses of the self-hosted fonts\n\nGenerated by build.py. Each woff2 file is an unmodified, '
            'losslessly compressed copy of the original font and carries its full copyright and license text '
            'in its name table.\n\n| Family | License | Copyright |\n|---|---|---|\n' + '\n'.join(licenses) + '\n')

    size = sum(f.stat().st_size for f in OUT.rglob('*.woff2')) / 1e6
    print(f'done: {len(from_google)} from Google Fonts, {len(from_fontshare)} from Fontshare, '
          f'{len(self_hosted)} self-hosted ({size:.1f} MB), {len(installed_only)} installed-only')


if __name__ == '__main__':
    main()
