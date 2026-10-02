// runs for any request that isn't a file in site/. its one job is /steal?url=…: fetching a font file from
// another site for the custom font field, when that site doesn't allow cross-origin loading (no CORS header)
// or only serves it to its own pages (referer check). only responses that really are font files are passed on,
// and only to this site's own page
const MAX_BYTES = 10 * 1024 * 1024;
const MAX_URL = 2048;
const TIMEOUT = 10_000; // ms, for the other site to answer and send the whole file

// what each kind of font file starts with: woff2, woff, truetype, opentype (cff), old apple truetype
const TYPES = { wOF2: 'font/woff2', wOFF: 'font/woff', '\0\x01\0\0': 'font/ttf', OTTO: 'font/otf', true: 'font/ttf' };

const fail = (status, text) => new Response(text, { status });

// the page's own fetch() says so itself (sec-fetch-site, which scripts can't set). browsers too old to send
// it are let in by their referer. this keeps other sites and casual scripts from using /steal as a free proxy
function fromThisSite(request, origin) {
  const site = request.headers.get('sec-fetch-site');
  if (site) return site === 'same-origin';
  try { return new URL(request.headers.get('referer')).origin === origin; } catch { return false; }
}

// the response body, or null as soon as it turns out not to be a font or to be too big, without reading the rest
async function readFont(res) {
  if (Number(res.headers.get('content-length')) > MAX_BYTES) return null;
  const reader = res.body.getReader();
  const chunks = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    size += value.byteLength;
    if (size > MAX_BYTES || (chunks.length === 1 && size >= 4 && !TYPES[magic(value)])) {
      reader.cancel().catch(() => { });
      return null;
    }
  }
  const body = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) { body.set(chunk, at); at += chunk.byteLength; }
  return body;
}

const magic = bytes => String.fromCharCode(...bytes.subarray(0, 4));

export default {
  async fetch(request, env, ctx) {
    const { origin, hostname, pathname, searchParams } = new URL(request.url);
    if (pathname !== '/steal') return fail(404, 'not found');
    if (request.method !== 'GET') return fail(405, 'get only');
    if (!fromThisSite(request, origin)) return fail(403, 'only for the typecase page');

    const url = searchParams.get('url') ?? '';
    if (url.length > MAX_URL) return fail(414, 'url too long');
    let target;
    try { target = new URL(url); } catch { return fail(400, 'not a url'); }
    if (!['http:', 'https:'].includes(target.protocol)) return fail(400, 'not a web url');
    if (target.username || target.password) return fail(400, 'no credentials in the url');
    if (target.hostname === hostname) return fail(400, 'that is this site');

    // fonts fetched before are kept at the edge for a day, so the other site is asked once, not on every load
    const cache = caches.default, key = new Request(request.url);
    const cached = await cache.match(key);
    if (cached) return cached;

    // look like a page on that site asking for its own font
    let body;
    try {
      const res = await fetch(target, {
        headers: { referer: target.origin + '/', accept: 'font/*,*/*' },
        signal: AbortSignal.timeout(TIMEOUT),
      });
      if (!res.ok) return fail(502, `couldn't fetch it (${res.status})`);
      body = await readFont(res);
    } catch {
      return fail(502, "couldn't fetch it (network error or too slow)");
    }
    const type = body && TYPES[magic(body)];
    if (!type) return fail(415, 'not a font file, or bigger than a font should be');

    const response = new Response(body, {
      headers: {
        'content-type': type,
        'cache-control': 'public, max-age=86400',
        'x-content-type-options': 'nosniff',
      },
    });
    ctx.waitUntil(cache.put(key, response.clone()));
    return response;
  },
};
