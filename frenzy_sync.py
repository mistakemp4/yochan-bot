import json
import sys
import tomllib
from pathlib import Path

FILE = Path(__file__).parent / "frenzy.toml"
HEADER = """# Fish Frenzy riddles. Players react with `answer` (or anything in `also`).
# clue shows first, hint after a few seconds. name is only for the emoji search.
"""


def to_toml(items: list[dict]) -> str:
    out = [HEADER]
    for r in items:
        out.append("[[riddle]]")
        for key in ("answer", "also", "name", "clue", "hint"):
            out.append(f"{key} = {json.dumps(r.get(key, [] if key == 'also' else ''), ensure_ascii=False)}")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    # export: frenzy.toml -> json, import: json file ({items: [...]} or a list) -> frenzy.toml
    if sys.argv[1] == "export":
        items = tomllib.loads(FILE.read_text(encoding="utf-8")).get("riddle", [])
        print(json.dumps({"items": items}, ensure_ascii=False, indent=1))
    else:
        data = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        items = data["items"] if isinstance(data, dict) else data
        FILE.write_text(to_toml(items), encoding="utf-8")
