import json
import sys
import tomllib
from pathlib import Path

CONFIG = Path(__file__).parent / "config.toml"
COMMENTS = {
    ("seal", "links"): "# Yo-chan gifs/images/videos she replies with",
    ("seal", "sticker_ids"): "# Server sticker ids (right-click sticker > Copy ID)",
    ("gyu", "lines"): "# Rare full lines instead of a gyu",
    ("react", "custom_keywords"): "# Custom server emoji names that count too",
}


def to_toml(cfg: dict, order: dict) -> str:
    out = []
    for section in ("seal", "gyu", "react"):
        out.append(f"[{section}]")
        known = list(order.get(section, {}))
        keys = [k for k in known if k in cfg[section]] + [k for k in cfg[section] if k not in known]
        for key in keys:
            value = cfg[section][key]
            if (section, key) in COMMENTS:
                out.append(COMMENTS[(section, key)])
            if section == "seal" and key == "sticker_ids":
                value = [str(v) for v in value]
            if isinstance(value, list):
                items = "".join(f"    {json.dumps(v, ensure_ascii=False)},\n" for v in value)
                out.append(f"{key} = [\n{items}]" if value else f"{key} = []")
            else:
                out.append(f"{key} = {json.dumps(value)}")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    # export: config.toml -> json, import: json file -> config.toml
    if sys.argv[1] == "export":
        cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
        cfg["seal"]["sticker_ids"] = [str(v) for v in cfg["seal"]["sticker_ids"]]
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
    else:
        cfg = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        cfg.pop("updatedAt", None)
        order = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
        CONFIG.write_text(to_toml(cfg, order), encoding="utf-8")
