export interface Env {
  ARBORISIS_BUCKET: R2Bucket;
  SIGNING_KEY: string;
}

const CORS_ORIGINS = ['https://arborisis.com', 'https://www.arborisis.com'];

function handleCors(request: Request): Response | null {
  const origin = request.headers.get('Origin') ?? '';
  const allowedOrigin = CORS_ORIGINS.includes(origin) ? origin : CORS_ORIGINS[0];

  if (request.method === 'OPTIONS') {
    return new Response(null, {
      status: 204,
      headers: {
        'Access-Control-Allow-Origin': allowedOrigin,
        'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
        'Access-Control-Allow-Headers': '*',
        'Access-Control-Max-Age': '86400',
      },
    });
  }

  return null;
}

function corsHeaders(request: Request): Headers {
  const origin = request.headers.get('Origin') ?? '';
  const allowedOrigin = CORS_ORIGINS.includes(origin) ? origin : CORS_ORIGINS[0];
  const headers = new Headers();
  headers.set('Access-Control-Allow-Origin', allowedOrigin);
  headers.set('Vary', 'Origin');
  return headers;
}

async function verifySignature(
  path: string,
  expires: string,
  signature: string,
  secret: string
): Promise<boolean> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const payload = `${path}:${expires}`;
  const sig = await crypto.subtle.sign('HMAC', key, encoder.encode(payload));
  const expected = btoa(String.fromCharCode(...new Uint8Array(sig)));
  return timingSafeEqual(signature, expected);
}

function timingSafeEqual(a: string, b: string): boolean {
  const encoder = new TextEncoder();
  const left = encoder.encode(a);
  const right = encoder.encode(b);
  const length = Math.max(left.length, right.length);
  let diff = left.length ^ right.length;

  for (let i = 0; i < length; i += 1) {
    diff |= (left[i] ?? 0) ^ (right[i] ?? 0);
  }

  return diff === 0;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // Handle CORS preflight
    const corsResponse = handleCors(request);
    if (corsResponse) {
      return corsResponse;
    }

    const url = new URL(request.url);
    const path = url.pathname.slice(1); // remove leading /
    const expires = url.searchParams.get('expires');
    const signature = url.searchParams.get('signature');

    // Reject missing params
    if (!expires || !signature) {
      return new Response('Forbidden: missing signature', {
        status: 403,
        headers: corsHeaders(request),
      });
    }

    // Reject expired links
    const expiryTs = parseInt(expires, 10);
    if (isNaN(expiryTs) || expiryTs * 1000 < Date.now()) {
      return new Response('Forbidden: link expired', {
        status: 403,
        headers: corsHeaders(request),
      });
    }

    // Verify HMAC
    const valid = await verifySignature(path, expires, signature, env.SIGNING_KEY);
    if (!valid) {
      return new Response('Forbidden: invalid signature', {
        status: 403,
        headers: corsHeaders(request),
      });
    }

    // Serve from R2 binding
    const rangeHeader = request.headers.get('range');
    const object = await env.ARBORISIS_BUCKET.get(path, {
      range: rangeHeader ? { offset: 0, length: undefined } : undefined,
    });

    if (!object) {
      return new Response('Not found', {
        status: 404,
        headers: corsHeaders(request),
      });
    }

    const headers = corsHeaders(request);
    headers.set('Content-Type', object.httpMetadata?.contentType ?? 'application/octet-stream');
    headers.set('Cache-Control', 'private, max-age=300');
    headers.set('Accept-Ranges', 'bytes');
    if (object.httpMetadata?.contentDisposition) {
      headers.set('Content-Disposition', object.httpMetadata.contentDisposition);
    }
    if (object.httpEtag) {
      headers.set('ETag', object.httpEtag);
    }
    if (object.range) {
      headers.set('Content-Range', `bytes ${object.range.offset}-${object.range.offset + object.range.length - 1}/${object.size}`);
    }

    const status = object.range ? 206 : 200;
    return new Response(object.body, { status, headers });
  },
};
