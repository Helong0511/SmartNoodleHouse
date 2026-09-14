import subprocess
import sys

r = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short", "-p", "no:warnings"],
    capture_output=True,
    text=True,
    cwd=".",
)
print("=== STDOUT TAIL ===")
print("\n".join(r.stdout.strip().split("\n")[-8:]))
print("=== STDERR TAIL ===")
print("\n".join(r.stderr.strip().split("\n")[-20:]))
print("=== EXIT:", r.returncode)
