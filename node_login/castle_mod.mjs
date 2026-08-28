import crypto from 'node:crypto';
function noop(...args) {}
var FieldEncoding = /* @__PURE__ */ ((FieldEncoding2) => {
  FieldEncoding2[FieldEncoding2["Empty"] = -1] = "Empty";
  FieldEncoding2[FieldEncoding2["Marker"] = 1] = "Marker";
  FieldEncoding2[FieldEncoding2["Byte"] = 3] = "Byte";
  FieldEncoding2[FieldEncoding2["EncryptedBytes"] = 4] = "EncryptedBytes";
  FieldEncoding2[FieldEncoding2["CompactInt"] = 5] = "CompactInt";
  FieldEncoding2[FieldEncoding2["RoundedByte"] = 6] = "RoundedByte";
  FieldEncoding2[FieldEncoding2["RawAppend"] = 7] = "RawAppend";
  return FieldEncoding2;
})(FieldEncoding || {});
const TWITTER_CASTLE_PK = "AvRa79bHyJSYSQHnRpcVtzyxetSvFerx";
const XXTEA_KEY = [1164413191, 3891440048, 185273099, 2746598870];
const PER_FIELD_KEY_TAIL = [
  16373134,
  643144773,
  1762804430,
  1186572681,
  1164413191
];
const TS_EPOCH = 1535e6;
const SDK_VERSION = 27008;
const TOKEN_VERSION = 11;
const FP_PART = {
  DEVICE: 0,
  // Part 1: hardware/OS/rendering fingerprint
  BROWSER: 4,
  // Part 2: browser environment fingerprint
  TIMING: 7
  // Part 3: timing-based fingerprint
};
const DEFAULT_PROFILE = {
  locale: "en-US",
  language: "en",
  timezone: "America/New_York",
  screenWidth: 1920,
  screenHeight: 1080,
  availableWidth: 1920,
  availableHeight: 1032,
  // 1080 minus Windows taskbar (~48px)
  gpuRenderer: "ANGLE (NVIDIA, NVIDIA GeForce GTX 1080 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)",
  deviceMemoryGB: 8,
  hardwareConcurrency: 24,
  colorDepth: 24,
  devicePixelRatio: 1
};
const SCREEN_RESOLUTIONS = [
  { w: 1920, h: 1080, ah: 1032 },
  { w: 2560, h: 1440, ah: 1392 },
  { w: 1366, h: 768, ah: 720 },
  { w: 1536, h: 864, ah: 816 },
  { w: 1440, h: 900, ah: 852 },
  { w: 1680, h: 1050, ah: 1002 },
  { w: 3840, h: 2160, ah: 2112 }
];
const DEVICE_MEMORY_VALUES = [4, 8, 8, 16];
const HARDWARE_CONCURRENCY_VALUES = [4, 8, 8, 12, 16, 24];
function randomizeBrowserProfile() {
  const screen = SCREEN_RESOLUTIONS[randInt(0, SCREEN_RESOLUTIONS.length - 1)];
  return {
    ...DEFAULT_PROFILE,
    screenWidth: screen.w,
    screenHeight: screen.h,
    availableWidth: screen.w,
    availableHeight: screen.ah,
    // gpuRenderer intentionally NOT randomized — see JSDoc above
    deviceMemoryGB: DEVICE_MEMORY_VALUES[randInt(0, DEVICE_MEMORY_VALUES.length - 1)],
    hardwareConcurrency: HARDWARE_CONCURRENCY_VALUES[randInt(0, HARDWARE_CONCURRENCY_VALUES.length - 1)]
  };
}
function getRandomBytes(n) {
  const buf = new Uint8Array(n);
  if (typeof globalThis.crypto !== "undefined" && globalThis.crypto.getRandomValues) {
    globalThis.crypto.getRandomValues(buf);
  } else {
    for (let i = 0; i < n; i++) buf[i] = Math.floor(Math.random() * 256);
  }
  return buf;
}
function randInt(min, max) {
  return min + Math.floor(Math.random() * (max - min + 1));
}
function randFloat(min, max) {
  return min + Math.random() * (max - min);
}
function concat(...arrays) {
  const len = arrays.reduce((s, a) => s + a.length, 0);
  const out = new Uint8Array(len);
  let off = 0;
  for (const a of arrays) {
    out.set(a, off);
    off += a.length;
  }
  return out;
}
function toHex(input) {
  return Array.from(input).map((b) => b.toString(16).padStart(2, "0")).join("");
}
function fromHex(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2)
    out[i / 2] = parseInt(hex.substring(i, i + 2), 16);
  return out;
}
function textEnc(s) {
  return new TextEncoder().encode(s);
}
function u8(...vals) {
  return new Uint8Array(vals);
}
function be16(v) {
  return u8(v >>> 8 & 255, v & 255);
}
function be32(v) {
  return u8(v >>> 24 & 255, v >>> 16 & 255, v >>> 8 & 255, v & 255);
}
function xorBytes(data, key) {
  const out = new Uint8Array(data.length);
  for (let i = 0; i < data.length; i++) out[i] = data[i] ^ key[i % key.length];
  return out;
}
function xorNibbles(nibbles, keyNibble) {
  const k = parseInt(keyNibble, 16);
  return nibbles.split("").map((n) => (parseInt(n, 16) ^ k).toString(16)).join("");
}
function base64url(data) {
  if (typeof Buffer !== "undefined") {
    return Buffer.from(data).toString("base64url");
  }
  let bin = "";
  for (let i = 0; i < data.length; i++) bin += String.fromCharCode(data[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function xxteaEncrypt(data, key) {
  const padLen = Math.ceil(data.length / 4) * 4;
  const padded = new Uint8Array(padLen);
  padded.set(data);
  const n = padLen / 4;
  const v = new Uint32Array(n);
  for (let i = 0; i < n; i++) {
    v[i] = (padded[i * 4] | padded[i * 4 + 1] << 8 | padded[i * 4 + 2] << 16 | padded[i * 4 + 3] << 24) >>> 0;
  }
  if (n <= 1) return padded;
  const k = new Uint32Array(key.map((x) => x >>> 0));
  const DELTA = 2654435769;
  const u = n - 1;
  let sum = 0;
  let z = v[u];
  let y;
  let rounds = 6 + Math.floor(52 / (u + 1));
  while (rounds-- > 0) {
    sum = sum + DELTA >>> 0;
    const e = sum >>> 2 & 3;
    for (let p = 0; p < u; p++) {
      y = v[p + 1];
      const mx2 = ((z >>> 5 ^ y << 2) >>> 0) + ((y >>> 3 ^ z << 4) >>> 0) ^ ((sum ^ y) >>> 0) + ((k[p & 3 ^ e] ^ z) >>> 0);
      v[p] = v[p] + mx2 >>> 0;
      z = v[p];
    }
    y = v[0];
    const mx = ((z >>> 5 ^ y << 2) >>> 0) + ((y >>> 3 ^ z << 4) >>> 0) ^ ((sum ^ y) >>> 0) + ((k[u & 3 ^ e] ^ z) >>> 0);
    v[u] = v[u] + mx >>> 0;
    z = v[u];
  }
  const out = new Uint8Array(n * 4);
  for (let i = 0; i < n; i++) {
    out[i * 4] = v[i] & 255;
    out[i * 4 + 1] = v[i] >>> 8 & 255;
    out[i * 4 + 2] = v[i] >>> 16 & 255;
    out[i * 4 + 3] = v[i] >>> 24 & 255;
  }
  return out;
}
function fieldEncrypt(data, fieldIndex, initTime) {
  return xxteaEncrypt(data, [
    fieldIndex,
    Math.floor(initTime),
    ...PER_FIELD_KEY_TAIL
  ]);
}
function encodeTimestampBytes(ms) {
  let t = Math.floor(ms / 1e3 - TS_EPOCH);
  t = Math.max(Math.min(t, 268435455), 0);
  return be32(t);
}
function xorAndAppendKey(buf, key) {
  const hex = toHex(buf);
  const keyNib = (key & 15).toString(16);
  return xorNibbles(hex.substring(1), keyNib) + keyNib;
}
function encodeTimestampEncrypted(ms) {
  const tsBytes = encodeTimestampBytes(ms);
  const slice = Math.floor(ms) % 1e3;
  const sliceBytes = be16(slice);
  const k = randInt(0, 15);
  return xorAndAppendKey(tsBytes, k) + xorAndAppendKey(sliceBytes, k);
}
function deriveAndXor(keyHex, sliceLen, rotChar, data) {
  const sub = keyHex.substring(0, sliceLen).split("");
  if (sub.length === 0) return data;
  const rot = parseInt(rotChar, 16) % sub.length;
  const rotated = sub.slice(rot).concat(sub.slice(0, rot)).join("");
  return xorBytes(data, fromHex(rotated));
}
function customFloatEncode(expBits, manBits, value) {
  if (value === 0) return 0;
  let n = Math.abs(value);
  let exp = 0;
  while (2 <= n) {
    n /= 2;
    exp++;
  }
  while (n < 1 && n > 0) {
    n *= 2;
    exp--;
  }
  exp = Math.min(exp, (1 << expBits) - 1);
  const frac = n - Math.floor(n);
  let mantissa = 0;
  if (frac > 0) {
    let pos = 1;
    let tmp = frac;
    while (tmp !== 0 && pos <= manBits) {
      tmp *= 2;
      const bit = Math.floor(tmp);
      mantissa |= bit << manBits - pos;
      tmp -= bit;
      pos++;
    }
  }
  return exp << manBits | mantissa;
}
function encodeFloatVal(v) {
  const n = Math.max(v, 0);
  if (n <= 15) return 64 | customFloatEncode(2, 4, n + 1);
  return 128 | customFloatEncode(4, 3, n - 14);
}
function encodeField(index, encoding, val, initTime) {
  const hdr = u8((31 & index) << 3 | 7 & encoding);
  if (encoding === -1 /* Empty */ || encoding === 1 /* Marker */)
    return hdr;
  let body;
  switch (encoding) {
    case 3 /* Byte */:
      body = u8(val);
      break;
    case 6 /* RoundedByte */:
      body = u8(Math.round(val));
      break;
    case 5 /* CompactInt */: {
      const v = val;
      body = v <= 127 ? u8(v) : be16(1 << 15 | 32767 & v);
      break;
    }
    case 4 /* EncryptedBytes */: {
      if (initTime == null) {
        throw new Error("initTime is required for EncryptedBytes encoding");
      }
      const enc = fieldEncrypt(val, index, initTime);
      body = concat(u8(enc.length), enc);
      break;
    }
    case 7 /* RawAppend */:
      body = val instanceof Uint8Array ? val : u8(val);
      break;
    default:
      body = new Uint8Array(0);
  }
  return concat(hdr, body);
}
function encodeBits(bits, byteSize) {
  const numBytes = byteSize / 8;
  const arr = new Uint8Array(numBytes);
  for (const bit of bits) {
    const bi = numBytes - 1 - Math.floor(bit / 8);
    if (bi >= 0 && bi < numBytes) arr[bi] |= 1 << bit % 8;
  }
  return arr;
}
function screenDimBytes(screen, avail) {
  const r = 32767 & screen;
  const e = 65535 & avail;
  return r === e ? be16(32768 | r) : concat(be16(r), be16(e));
}
function boolsToBin(arr, totalBits) {
  const e = arr.length > totalBits ? arr.slice(0, totalBits) : arr;
  const c = e.length;
  let r = 0;
  for (let i = c - 1; i >= 0; i--) {
    if (e[i]) r |= 1 << c - i - 1;
  }
  if (c < totalBits) r <<= totalBits - c;
  return r;
}
function encodeCodecPlayability() {
  const codecs = {
    webm: 2,
    // VP8/VP9
    mp4: 2,
    // H.264
    ogg: 0,
    // Theora (Chrome dropped support)
    aac: 2,
    // AAC audio
    xm4a: 1,
    // M4A container
    wav: 2,
    // PCM audio
    mpeg: 2,
    // MP3 audio
    ogg2: 2
    // Vorbis audio
  };
  const bits = Object.values(codecs).map((c) => c.toString(2).padStart(2, "0")).join("");
  return be16(parseInt(bits, 2));
}
const TIMEZONE_ENUM = {
  "America/New_York": 0,
  "America/Sao_Paulo": 1,
  "America/Chicago": 2,
  "America/Los_Angeles": 3,
  "America/Mexico_City": 4,
  "Asia/Shanghai": 5
};
function getTimezoneInfo(tz) {
  const knownOffsets = {
    "America/New_York": { offset: 20, dstDiff: 4 },
    "America/Chicago": { offset: 24, dstDiff: 4 },
    "America/Los_Angeles": { offset: 32, dstDiff: 4 },
    "America/Denver": { offset: 28, dstDiff: 4 },
    "America/Sao_Paulo": { offset: 12, dstDiff: 4 },
    "America/Mexico_City": { offset: 24, dstDiff: 4 },
    "Asia/Shanghai": { offset: 246, dstDiff: 0 },
    "Asia/Tokyo": { offset: 220, dstDiff: 0 },
    "Europe/London": { offset: 0, dstDiff: 4 },
    "Europe/Berlin": { offset: 252, dstDiff: 4 },
    UTC: { offset: 0, dstDiff: 0 }
  };
  try {
    const now = /* @__PURE__ */ new Date();
    const jan = new Date(now.getFullYear(), 0, 1);
    const jul = new Date(now.getFullYear(), 6, 1);
    const getOffset = (date, zone) => {
      const utc = new Date(date.toLocaleString("en-US", { timeZone: "UTC" }));
      const local = new Date(date.toLocaleString("en-US", { timeZone: zone }));
      return (utc.getTime() - local.getTime()) / 6e4;
    };
    const currentOffset = getOffset(now, tz);
    const janOffset = getOffset(jan, tz);
    const julOffset = getOffset(jul, tz);
    const dstDifference = Math.abs(janOffset - julOffset);
    return {
      offset: Math.floor(currentOffset / 15) & 255,
      dstDiff: Math.floor(dstDifference / 15) & 255
    };
  } catch {
    return knownOffsets[tz] || { offset: 20, dstDiff: 4 };
  }
}
function buildDeviceFingerprint(initTime, profile, userAgent) {
  const tz = getTimezoneInfo(profile.timezone);
  const { Byte, EncryptedBytes, CompactInt, RoundedByte, RawAppend } = FieldEncoding;
  const encryptedUA = fieldEncrypt(textEnc(userAgent), 12, initTime);
  const uaPayload = concat(u8(1), u8(encryptedUA.length), encryptedUA);
  const fields = [
    encodeField(0, Byte, 1),
    // Platform: Win32
    encodeField(1, Byte, 0),
    // Vendor: Google Inc.
    encodeField(2, EncryptedBytes, textEnc(profile.locale), initTime),
    // Locale
    encodeField(3, RoundedByte, profile.deviceMemoryGB * 10),
    // Device memory (GB * 10)
    encodeField(
      4,
      RawAppend,
      concat(
        // Screen dimensions (width + height)
        screenDimBytes(profile.screenWidth, profile.availableWidth),
        screenDimBytes(profile.screenHeight, profile.availableHeight)
      )
    ),
    encodeField(5, CompactInt, profile.colorDepth),
    // Screen color depth
    encodeField(6, CompactInt, profile.hardwareConcurrency),
    // CPU logical cores
    encodeField(7, RoundedByte, profile.devicePixelRatio * 10),
    // Pixel ratio (* 10)
    encodeField(8, RawAppend, u8(tz.offset, tz.dstDiff)),
    // Timezone offset info
    // MIME type hash — captured from Chrome 144 on Windows 10.
    // Source: yubie-re/castleio-gen (Python SDK, MIT license).
    encodeField(9, RawAppend, u8(2, 125, 95, 201, 167)),
    // Browser plugins hash — Chrome no longer exposes plugins to navigator.plugins,
    // so this is a fixed hash. Source: yubie-re/castleio-gen (Python SDK, MIT license).
    encodeField(10, RawAppend, u8(5, 114, 147, 2, 8)),
    encodeField(
      11,
      RawAppend,
      // Browser feature flags
      concat(u8(12), encodeBits([0, 1, 2, 3, 4, 5, 6], 16))
    ),
    encodeField(12, RawAppend, uaPayload),
    // User agent (encrypted)
    // Canvas font rendering hash — generated by Castle.io SDK's canvas fingerprinting (text rendering).
    // Captured from Chrome 144 on Windows 10. Source: yubie-re/castleio-gen (Python SDK, MIT license).
    encodeField(13, EncryptedBytes, textEnc("54b4b5cf"), initTime),
    encodeField(
      14,
      RawAppend,
      // Media input devices
      concat(u8(3), encodeBits([0, 1, 2], 8))
    ),
    // Fields 15 (DoNotTrack) and 16 (JavaEnabled) intentionally omitted
    encodeField(17, Byte, 0),
    // productSub type
    // Canvas circle rendering hash — generated by Castle.io SDK's canvas fingerprinting (arc drawing).
    // Captured from Chrome 144 on Windows 10. Source: yubie-re/castleio-gen (Python SDK, MIT license).
    encodeField(18, EncryptedBytes, textEnc("c6749e76"), initTime),
    encodeField(19, EncryptedBytes, textEnc(profile.gpuRenderer), initTime),
    // WebGL renderer
    encodeField(
      20,
      EncryptedBytes,
      // Epoch locale string
      textEnc("12/31/1969, 7:00:00 PM"),
      initTime
    ),
    encodeField(
      21,
      RawAppend,
      // WebDriver flags (none set)
      concat(u8(8), encodeBits([], 8))
    ),
    encodeField(22, CompactInt, 33),
    // eval.toString() length
    // Field 23 (navigator.buildID) intentionally omitted (Chrome doesn't have it)
    encodeField(24, CompactInt, 12549),
    // Max recursion depth
    encodeField(25, Byte, 0),
    // Recursion error message type
    encodeField(26, Byte, 1),
    // Recursion error name type
    encodeField(27, CompactInt, 4644),
    // Stack trace string length
    encodeField(28, RawAppend, u8(0)),
    // Touch support metric
    encodeField(29, Byte, 3),
    // Undefined call error type
    // Navigator properties hash — hash of enumerable navigator property names.
    // Captured from Chrome 144 on Windows 10. Source: yubie-re/castleio-gen (Python SDK, MIT license).
    encodeField(30, RawAppend, u8(93, 197, 171, 181, 136)),
    encodeField(31, RawAppend, encodeCodecPlayability())
    // Codec playability
  ];
  const data = concat(...fields);
  const sizeIdx = (7 & FP_PART.DEVICE) << 5 | 31 & fields.length;
  return concat(u8(sizeIdx), data);
}
function buildBrowserFingerprint(profile, initTime) {
  const { Byte, EncryptedBytes, CompactInt, Marker, RawAppend } = FieldEncoding;
  const timezoneField = profile.timezone in TIMEZONE_ENUM ? encodeField(1, Byte, TIMEZONE_ENUM[profile.timezone]) : encodeField(1, EncryptedBytes, textEnc(profile.timezone), initTime);
  const fields = [
    encodeField(0, Byte, 0),
    // Constant marker
    timezoneField,
    // Timezone
    encodeField(
      2,
      EncryptedBytes,
      // Language list
      textEnc(`${profile.locale},${profile.language}`),
      initTime
    ),
    encodeField(6, CompactInt, 0),
    // Expected property count
    encodeField(
      10,
      RawAppend,
      // Castle data bitfield
      concat(u8(4), encodeBits([1, 2, 3], 8))
    ),
    encodeField(12, CompactInt, 80),
    // Negative error string length
    encodeField(13, RawAppend, u8(9, 0, 0)),
    // Driver check values
    encodeField(
      17,
      RawAppend,
      // Chrome feature flags
      concat(u8(13), encodeBits([1, 5, 8, 9, 10], 16))
    ),
    encodeField(18, Marker, 0),
    // Device logic expected
    encodeField(21, RawAppend, u8(0, 0, 0, 0)),
    // Class properties count
    encodeField(22, EncryptedBytes, textEnc(profile.locale), initTime),
    // User locale (secondary)
    encodeField(
      23,
      RawAppend,
      // Worker capabilities
      concat(u8(2), encodeBits([0], 8))
    ),
    encodeField(
      24,
      RawAppend,
      // Inner/outer dimension diff
      concat(be16(0), be16(randInt(10, 30)))
    )
  ];
  const data = concat(...fields);
  const sizeIdx = (7 & FP_PART.BROWSER) << 5 | 31 & fields.length;
  return concat(u8(sizeIdx), data);
}
function buildTimingFingerprint(initTime) {
  const minute = new Date(initTime).getUTCMinutes();
  const fields = [
    encodeField(3, 5 /* CompactInt */, 1),
    // Time since window.open (ms)
    encodeField(4, 5 /* CompactInt */, minute)
    // Castle init time (minutes)
  ];
  const data = concat(...fields);
  const sizeIdx = (7 & FP_PART.TIMING) << 5 | 31 & fields.length;
  return concat(u8(sizeIdx), data);
}
const EventType = {
  CLICK: 0,
  FOCUS: 5,
  BLUR: 6,
  ANIMATIONSTART: 18,
  MOUSEMOVE: 21,
  MOUSELEAVE: 25,
  MOUSEENTER: 26,
  RESIZE: 27
};
const HAS_TARGET_FLAG = 128;
const TARGET_UNKNOWN = 63;
function generateEventLog() {
  const simpleEvents = [
    EventType.MOUSEMOVE,
    EventType.ANIMATIONSTART,
    EventType.MOUSELEAVE,
    EventType.MOUSEENTER,
    EventType.RESIZE
  ];
  const targetedEvents = [
    EventType.CLICK,
    EventType.BLUR,
    EventType.FOCUS
  ];
  const allEvents = [...simpleEvents, ...targetedEvents];
  const count = randInt(30, 70);
  const eventBytes = [];
  for (let i = 0; i < count; i++) {
    const eventId = allEvents[randInt(0, allEvents.length - 1)];
    if (targetedEvents.includes(eventId)) {
      eventBytes.push(eventId | HAS_TARGET_FLAG);
      eventBytes.push(TARGET_UNKNOWN);
    } else {
      eventBytes.push(eventId);
    }
  }
  const inner = concat(u8(0), be16(count), new Uint8Array(eventBytes));
  return concat(be16(inner.length), inner);
}
function buildBehavioralBitfield() {
  const flags = new Array(15).fill(false);
  flags[2] = true;
  flags[3] = true;
  flags[5] = true;
  flags[6] = true;
  flags[9] = true;
  flags[11] = true;
  flags[12] = true;
  const packedBits = boolsToBin(flags, 16);
  const encoded = 6 << 20 | 2 << 16 | 65535 & packedBits;
  return u8(encoded >>> 16 & 255, encoded >>> 8 & 255, encoded & 255);
}
const NO_DATA = -1;
function buildFloatMetrics() {
  const metrics = [
    // ── Mouse & key timing ──
    randFloat(40, 50),
    //  0: Mouse angle vector mean
    NO_DATA,
    //  1: Touch angle vector (no touch device)
    randFloat(70, 80),
    //  2: Key same-time difference
    NO_DATA,
    //  3: (unused)
    randFloat(60, 70),
    //  4: Mouse down-to-up time mean
    NO_DATA,
    //  5: (unused)
    0,
    //  6: (zero placeholder)
    0,
    //  7: Mouse click time difference
    // ── Duration distributions ──
    randFloat(60, 80),
    //  8: Mouse down-up duration median
    randFloat(5, 10),
    //  9: Mouse down-up duration std deviation
    randFloat(30, 40),
    // 10: Key press duration median
    randFloat(2, 5),
    // 11: Key press duration std deviation
    // ── Touch metrics (all disabled for desktop) ──
    NO_DATA,
    NO_DATA,
    NO_DATA,
    NO_DATA,
    // 12-15
    NO_DATA,
    NO_DATA,
    NO_DATA,
    NO_DATA,
    // 16-19
    // ── Mouse trajectory analysis ──
    randFloat(150, 180),
    // 20: Mouse movement angle mean
    randFloat(3, 6),
    // 21: Mouse movement angle std deviation
    randFloat(150, 180),
    // 22: Mouse movement angle mean (500ms window)
    randFloat(3, 6),
    // 23: Mouse movement angle std (500ms window)
    randFloat(0, 2),
    // 24: Mouse position deviation X
    randFloat(0, 2),
    // 25: Mouse position deviation Y
    0,
    0,
    // 26-27: (zero placeholders)
    // ── Touch sequential/gesture metrics (disabled) ──
    NO_DATA,
    NO_DATA,
    // 28-29
    NO_DATA,
    NO_DATA,
    // 30-31
    // ── Key pattern analysis ──
    0,
    0,
    // 32-33: Letter-digit transition ratio
    0,
    0,
    // 34-35: Digit-invalid transition ratio
    0,
    0,
    // 36-37: Double-invalid transition ratio
    // ── Mouse vector differences ──
    1,
    0,
    // 38-39: Mouse vector diff (mean, std)
    1,
    0,
    // 40-41: Mouse vector diff 2 (mean, std)
    randFloat(0, 4),
    // 42: Mouse vector diff (500ms mean)
    randFloat(0, 3),
    // 43: Mouse vector diff (500ms std)
    // ── Rounded movement metrics ──
    randFloat(25, 50),
    // 44: Mouse time diff (rounded mean)
    randFloat(25, 50),
    // 45: Mouse time diff (rounded std)
    randFloat(25, 50),
    // 46: Mouse vector diff (rounded mean)
    randFloat(25, 30),
    // 47: Mouse vector diff (rounded std)
    // ── Speed change analysis ──
    randFloat(0, 2),
    // 48: Mouse speed change mean
    randFloat(0, 1),
    // 49: Mouse speed change std
    randFloat(0, 1),
    // 50: Mouse vector 500ms aggregate
    // ── Trailing ──
    1,
    // 51: Universal flag
    0
    // 52: Terminator
  ];
  const out = new Uint8Array(metrics.length);
  for (let i = 0; i < metrics.length; i++) {
    out[i] = metrics[i] === NO_DATA ? 0 : encodeFloatVal(metrics[i]);
  }
  return out;
}
function buildEventCounts() {
  const counts = [
    randInt(100, 200),
    //  0: mousemove events
    randInt(1, 5),
    //  1: keyup events
    randInt(1, 5),
    //  2: click events
    0,
    //  3: touchstart events (none on desktop)
    randInt(0, 5),
    //  4: keydown events
    0,
    //  5: touchmove events (none)
    0,
    //  6: mousedown-mouseup pairs
    0,
    //  7: vector diff samples
    randInt(0, 5),
    //  8: wheel events
    randInt(0, 11),
    //  9: (internal counter)
    randInt(0, 1)
    // 10: (internal counter)
  ];
  return concat(new Uint8Array(counts), u8(counts.length));
}
function buildBehavioralData() {
  return concat(
    buildBehavioralBitfield(),
    buildFloatMetrics(),
    buildEventCounts()
  );
}
function buildTokenHeader(uuid, publisherKey, initTime) {
  const timestamp = fromHex(encodeTimestampEncrypted(initTime));
  const version = be16(SDK_VERSION);
  const pkBytes = textEnc(publisherKey);
  const uuidBytes = fromHex(uuid);
  return concat(timestamp, version, pkBytes, uuidBytes);
}
function generateLocalCastleToken(userAgent, profileOverride) {
  const now = Date.now();
  const profile = { ...DEFAULT_PROFILE, ...profileOverride };
  const initTime = now - randFloat(2 * 60 * 1e3, 30 * 60 * 1e3);
  noop("Generating local Castle.io v11 token");
  const deviceFp = buildDeviceFingerprint(initTime, profile, userAgent);
  const browserFp = buildBrowserFingerprint(profile, initTime);
  const timingFp = buildTimingFingerprint(initTime);
  const eventLog = generateEventLog();
  const behavioral = buildBehavioralData();
  const fingerprintData = concat(
    deviceFp,
    browserFp,
    timingFp,
    eventLog,
    behavioral,
    u8(255)
  );
  const sendTime = Date.now();
  const timestampKey = encodeTimestampEncrypted(sendTime);
  const xorPass1 = deriveAndXor(
    timestampKey,
    4,
    timestampKey[3],
    fingerprintData
  );
  const tokenUuid = toHex(getRandomBytes(16));
  const withTimestampPrefix = concat(fromHex(timestampKey), xorPass1);
  const xorPass2 = deriveAndXor(
    tokenUuid,
    8,
    tokenUuid[9],
    withTimestampPrefix
  );
  const header = buildTokenHeader(tokenUuid, TWITTER_CASTLE_PK, initTime);
  const plaintext = concat(header, xorPass2);
  const encrypted = xxteaEncrypt(plaintext, XXTEA_KEY);
  const paddingBytes = encrypted.length - plaintext.length;
  const versioned = concat(u8(TOKEN_VERSION, paddingBytes), encrypted);
  const randomByte = getRandomBytes(1)[0];
  const checksum = versioned.length * 2 & 255;
  const withChecksum = concat(versioned, u8(checksum));
  const xored = xorBytes(withChecksum, u8(randomByte));
  const finalPayload = concat(u8(randomByte), xored);
  const token = base64url(finalPayload);
  noop(
    `Generated castle token: ${token.length} chars, cuid: ${tokenUuid.substring(
      0,
      6
    )}...`
  );
  return { token, cuid: tokenUuid };
}


export { generateLocalCastleToken, DEFAULT_PROFILE, XXTEA_KEY };
