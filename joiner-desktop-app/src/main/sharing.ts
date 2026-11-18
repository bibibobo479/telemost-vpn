import { createHash } from 'node:crypto';
import { gunzipSync } from 'node:zlib';

export interface SharedConnection {
  version: number; token: string; folder: string;
  profile: { fps: number; batch: number; dual_track: boolean };
}
export function decodeShare(code: string): SharedConnection {
  if (typeof code !== 'string' || code.length > 32768) throw new Error('Invalid connection code');
  const match = /^TMVPN1\.([A-Za-z0-9_-]+)\.([a-f0-9]{12})$/.exec(code.replace(/\s/g, ''));
  if (!match || createHash('sha256').update(match[1]).digest('hex').slice(0,12) !== match[2])
    throw new Error('Connection code is incomplete or damaged');
  let value: SharedConnection;
  try { value = JSON.parse(gunzipSync(Buffer.from(match[1], 'base64url'), { maxOutputLength: 32768 }).toString()); }
  catch { throw new Error('Invalid connection code'); }
  const p = value?.profile;
  if (value?.version !== 1 || typeof value.token !== 'string' || !/^[\x21-\x7e]{16,4096}$/.test(value.token) ||
      typeof value.folder !== 'string' || !/^disk:\/Телемост VPN(?:\/[a-f0-9]{32})?$/.test(value.folder) ||
      !p || !Number.isInteger(p.fps) || p.fps < 1 || p.fps > 240 ||
      !Number.isInteger(p.batch) || p.batch < 1 || p.batch > 256 || typeof p.dual_track !== 'boolean')
    throw new Error('Unsupported connection code');
  return value;
}
export async function jsonRequest(url: string, headers?: Record<string,string>, hop = 0): Promise<any> {
  const response = await fetch(url, { headers, signal: AbortSignal.timeout(25000), redirect: 'manual' });
  if ([301,302,303,307,308].includes(response.status)) {
    await response.body?.cancel();
    const location=response.headers.get('location');
    if (headers || !location || hop >= 5) throw new Error('Unexpected Disk redirect');
    const next=new URL(location,url);
    if (next.protocol !== 'https:' || next.username || next.password || next.port ||
        !['.yandex.net','.yandex.ru'].some(s => next.hostname.endsWith(s))) throw new Error('Unexpected storage host');
    return jsonRequest(next.toString(), undefined, hop+1);
  }
  if (!response.ok) throw new Error(`Yandex Disk HTTP ${response.status}`);
  const reader = response.body?.getReader();
  if (!reader) throw new Error('Empty Disk response');
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const {done,value} = await reader.read(); if (done) break;
      size += value.length; if (size > 65536) throw new Error('Disk response too large');
      chunks.push(value);
    }
    return JSON.parse(Buffer.concat(chunks).toString());
  } finally { await reader.cancel(); }
}
export async function resolveRoom(value: SharedConnection): Promise<string> {
  const result = await jsonRequest('https://cloud-api.yandex.net/v1/disk/resources/download?path='+
    encodeURIComponent(value.folder+'/room.json'), { Authorization: 'OAuth '+value.token });
  const href = new URL(result.href);
  if (href.protocol !== 'https:' || href.username || href.password ||
      !['.yandex.net','.yandex.ru'].some(s => href.hostname.endsWith(s))) throw new Error('Unexpected Disk URL');
  const room = await jsonRequest(href.toString());
  const link = new URL(room.link);
  if (link.protocol !== 'https:' || link.host !== 'telemost.yandex.ru' || link.username || link.password ||
      !link.pathname.startsWith('/j/') || link.pathname.length <= 3) throw new Error('Invalid room');
  return link.toString();
}
