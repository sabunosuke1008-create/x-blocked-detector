// Login engine: the-convocation/twitter-scraper (2026 login flow incl. Castle).
// Reads credentials from env, writes cookies JSON to OUT path.
import { Scraper } from '@the-convocation/twitter-scraper';
import { writeFileSync } from 'node:fs';

const email = process.env.XB_LOGIN_EMAIL || '';
const username = process.env.XB_LOGIN_USERNAME || '';
const password = process.env.XB_LOGIN_PASSWORD || '';
const totp = process.env.XB_LOGIN_TOTP || '';
const out = process.env.OUT || 'login_cookies.json';

const scraper = new Scraper();
await scraper.login(username || email, password, email || undefined, totp || undefined);

let me = null;
try {
  me = await scraper.me();
} catch {}

let cookies = {};
try {
  const raw = await scraper.getCookies();
  const arr = Array.isArray(raw) ? raw : (raw?.cookies || []);
  for (const c of arr) {
    const key = c.key ?? c.name;
    if (key === 'auth_token' || key === 'ct0') cookies[key] = c.value;
  }
} catch (e) {
  console.error('cookie extract failed:', String(e));
}

if (!cookies.auth_token) {
  console.error('no auth_token cookie after login');
  process.exit(2);
}
writeFileSync(out, JSON.stringify({
  cookies,
  user_id: me?.userId ?? null,
  screen_name: me?.userName ?? null,
}, null, 2));
console.log('OK', me?.userName ?? '?');