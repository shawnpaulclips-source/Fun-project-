import glob
import json
import os
import sys

ROOT = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
MODELS = os.path.join(ROOT, "models")
RULES = os.path.join(ROOT, "rules")

MODEL_MAP = [
    ("haiku", "haiku-5.5.md"),
    ("sonnet", "sonnet-5.5.md"),
    ("fable", "fable-5.2.md"),
    ("opus-4", "opus-4.6.md"),
    ("opus-5", "opus-5.5.md"),
]
DEFAULT_MODEL_FILE = "haiku-5.5.md"


def read_text(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def pick_model_file(payload):
    haystack = " ".join([
        json.dumps(payload),
        os.environ.get("ANTHROPIC_MODEL", ""),
        os.environ.get("CLAUDE_MODEL", ""),
    ]).lower()
    for needle, filename in MODEL_MAP:
        if needle in haystack:
            return filename
    return DEFAULT_MODEL_FILE


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = {}

    model_file = pick_model_file(payload)
    model_text = read_text(os.path.join(MODELS, model_file))
    if model_text is None:
        model_file = DEFAULT_MODEL_FILE
        model_text = read_text(os.path.join(MODELS, model_file)) or ""

    rule_paths = sorted(glob.glob(os.path.join(RULES, "*.md")))
    rule_names = [os.path.relpath(p, ROOT) for p in rule_paths]

    lines = [
        "MARA CALIBRATION, loaded by the SessionStart hook.",
        f"Model file: models/{model_file}. Apply it below.",
        "Before your first reply, also Read every rules file with the Read tool: "
        + ", ".join(rule_names) + ".",
        "The copy below does not replace those reads.",
        "",
        f"===== models/{model_file} =====",
        model_text,
    ]
    context = "\n".join(lines)

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
