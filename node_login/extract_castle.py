# Extract castle module from compiled twitter-scraper bundle
import io

src_path = r'node_login\node_modules\@the-convocation\twitter-scraper\dist\node\esm\index.mjs'
s = io.open(src_path, encoding='utf-8').read()

# FieldEncoding enum definition starts near the castle code
start_field_encoding = s.find('var FieldEncoding')
start = start_field_encoding if start_field_encoding > 0 else s.find('const TS_EPOCH')
end_marker = 'const log$6 = debug'
end = s.find(end_marker)
if start == -1 or end == -1:
    print(f'start={start} end={end}')
    raise SystemExit(1)

seg = s[start:end]
# remove duplicate crypto.randomBytes declaration
seg = seg.replace("const getRandomBytes = (n) => new Uint8Array(crypto.randomBytes(n));", "")

# neutralize debug logger refs: log$7(...) -> noop(...)
import re as _re
seg = _re.sub(r'\blog\$\d+\(', 'noop(', seg)

header = """import crypto from 'node:crypto';
function noop(...args) {}
"""

footer = "\nexport { generateLocalCastleToken, DEFAULT_PROFILE, XXTEA_KEY };\n"

out = header + seg + footer
io.open(r'node_login\castle_mod.mjs', 'w', encoding='utf-8').write(out)
print(f'saved node_login/castle_mod.mjs: {len(out)} bytes')

# Quick smoke test: import and generate a token
import subprocess
code = (
    "import { generateLocalCastleToken } from './castle_mod.mjs';"
    "const t = generateLocalCastleToken('Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/145.0.0.0');"
    "console.log('LEN:', t.token.length);"
    "console.log(t.token.substring(0,80)+'...');"
)
result = subprocess.run(
    ['node', '--input-type=module', '-e', code],
    cwd=r'node_login', capture_output=True, text=True, timeout=30,
)
print('stdout:', result.stdout[:300])
print('stderr:', result.stderr[:300])
print('returncode:', result.returncode)
