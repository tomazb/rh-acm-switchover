from pathlib import Path

path = Path("thermos-resolution-plan.md")
text = path.read_text(encoding="utf-8")
old = "\n\n| TR2D-M1 | confirmed with nuance |"
new = "\n| TR2D-M1 | confirmed with nuance |"
count = text.count(old)
if count != 1:
    raise SystemExit(f"Expected one delta-matrix separator, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
