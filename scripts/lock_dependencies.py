"""Resolve binary application dependencies for the two supported Python profiles."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib

COMPILER_VERSION = "0.11.29"
TARGETS = {"windows": "x86_64-pc-windows-msvc", "linux": "x86_64-manylinux_2_35"}


def header(root: Path, profile: str) -> bytes:
    if profile not in TARGETS:
        raise ValueError("Unknown dependency target")
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = manifest["project"]
    declaration = {
        "dependencies": project.get("dependencies", []),
        "extras": project.get("optional-dependencies", {}),
        "python": project["requires-python"],
        "build": manifest["build-system"]["requires"],
        "compiler": COMPILER_VERSION,
        "target": TARGETS[profile],
        "policy": "CPython3.11/all-extras/binary-only",
    }
    digest = hashlib.sha256(json.dumps(declaration, sort_keys=True).encode()).hexdigest()
    return (
        f"# Application dependency lock: {profile}, CPython 3.11, all extras\n"
        f"# declaration-sha256: {digest}\n"
        f"# Compiler: uv {COMPILER_VERSION}; binary distributions only\n"
    ).encode()


def compile_profile(root: Path, profile: str, *, check: bool) -> None:
    expected_header = header(root, profile)
    output = root / "requirements" / f"{profile}-py311.lock"
    original = output.read_bytes() if output.exists() else None
    if check and (original is None or not original.startswith(expected_header)):
        raise ValueError("Missing lock or changed dependency declaration; regenerate explicitly")
    compiler = shutil.which("uv")
    if compiler is None:
        raise ValueError(f"Install the pinned uv {COMPILER_VERSION} compiler")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("UV_", "PIP_", "PYTHON")) and key.upper() != "VIRTUAL_ENV"
    }
    version = (
        subprocess.run(
            [compiler, "--version"], capture_output=True, check=True, timeout=10, env=environment
        )
        .stdout.decode()
        .strip()
    )
    if version.split()[:2] != ["uv", COMPILER_VERSION]:
        raise ValueError(f"Expected uv {COMPILER_VERSION}")
    with tempfile.TemporaryDirectory(prefix="ai-quant-lock-") as temporary:
        working = Path(temporary)
        resolved = working / "requirements.lock"
        if original is not None:
            resolved.write_bytes(original)
        manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        build_input = working / "build.in"
        build_input.write_text(
            "\n".join(manifest["build-system"]["requires"] + ["wheel"]) + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                compiler,
                "--no-config",
                "pip",
                "compile",
                str(root / "pyproject.toml"),
                str(build_input),
                "--all-extras",
                "--no-python-downloads",
                "--generate-hashes",
                "--only-binary",
                ":all:",
                "--python-version",
                "3.11",
                "--python-platform",
                TARGETS[profile],
                "--no-header",
                "--no-annotate",
                "--output-file",
                str(resolved),
                "--default-index",
                "https://pypi.org/simple",
            ],
            check=True,
            timeout=300,
            env=environment,
            stdout=subprocess.DEVNULL,
        )
        locked = expected_header + resolved.read_bytes().replace(b"\r\n", b"\n")
    if check:
        if locked != original:
            raise ValueError("Resolved dependencies differ from retained lock")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging = tempfile.mkstemp(prefix=".lock-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(locked)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, output)
    finally:
        Path(staging).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=[*TARGETS, "all"], default="all")
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    for profile in TARGETS if arguments.platform == "all" else [arguments.platform]:
        compile_profile(root, profile, check=arguments.check)
        print(f"{profile} dependency lock {'verified' if arguments.check else 'written'}")


if __name__ == "__main__":
    main()
