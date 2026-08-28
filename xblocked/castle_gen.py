import subprocess
import re
from pathlib import Path
from typing import Optional

NODE_CASTLE = Path(__file__).resolve().parent.parent / "node_login" / "castle_mod.mjs"


def generate_castle_token(user_agent: str) -> Optional[str]:
    """Generate a Castle.io token by delegating to the extracted Node module."""
    if not NODE_CASTLE.exists():
        return None
    code = (
        "import { generateLocalCastleToken } from './castle_mod.mjs';"
        "const t = generateLocalCastleToken('" + user_agent.replace("'", "\\'") + "');"
        "console.log(JSON.stringify({token: t.token, cuid: t.cuid}));"
    )
    argv = ["node", "--input-type=module", "-e", code]
    try:
        result = subprocess.run(
            argv,
            cwd=NODE_CASTLE.parent,
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        m = re.search(r'"token":"([^"]+)"', result.stdout)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None
