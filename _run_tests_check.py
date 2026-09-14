import subprocess
import sys

lines_out = []
r = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short", "-p", "no:warnings"],
    capture_output=True,
    text=True,
    cwd=".",
)
lines_out.append("=== STDOUT TAIL ===")
lines_out.append("\n".join(r.stdout.strip().split("\n")[-8:]))
lines_out.append("=== STDERR TAIL ===")
lines_out.append("\n".join(r.stderr.strip().split("\n")[-25:]))
lines_out.append(f"=== EXIT: {r.returncode}")

with open("_test_result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines_out))
