// runs for any request that isn't a file in site/. its one job is /steal?url=…: fetching a font file from
// another site for the custom font field, when that site doesn't allow cross-origin loading (no CORS header)
// or only serves it to its own pages (referer check). only responses that really are font files are passed on
const MAX_BYTES = 10 * 1024 * 1024;

// what each kind of font file starts with: woff2, woff, truetype, opentype (cff), old apple truetype
const TYPES = { wOF2: 'font/woff2', wOFF: 'font/woff', '\0\x01\0\0': 'font/ttf', OTTO: 'font/otf', true: 'font/ttf' };

const fail = (status, text) => new Response(text, { status });

export default {
  async fetch(request) {
    const { pathname, searchParams } = new URL(request.url);
    if (pathname !== '/steal') return fail(404, 'not found');

    let target;
    try { target = new URL(searchParams.get('url')); } catch { return fail(400, 'not a url'); }
    if (!['http:', 'https:'].includes(target.protocol)) return fail(400, 'not a web url');

    // look like a page on that site asking for its own font
    const res = await fetch(target, { headers: { referer: target.origin + '/', accept: 'font/*,*/*' } })
      .catch(() => null);
    if (!res?.ok) return fail(502, `couldn't fetch it (${res?.status ?? 'network error'})`);
    const body = await res.arrayBuffer();
    if (body.byteLength > MAX_BYTES) return fail(413, 'too big for a font');
    const type = TYPES[String.fromCharCode(...new Uint8Array(body.slice(0, 4)))];
    if (!type) return fail(415, 'not a font file');

    return new Response(body, {
      headers: { 'content-type': type, 'cache-control': 'public, max-age=86400' },
    });
  },
};
