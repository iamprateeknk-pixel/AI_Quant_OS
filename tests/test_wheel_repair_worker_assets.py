from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def test_built_wheel_contains_repair_worker_runtime_assets(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    build_root = tmp_path / "source"
    build_root.mkdir()
    shutil.copy2(repository / "pyproject.toml", build_root / "pyproject.toml")
    shutil.copytree(repository / "src", build_root / "src")
    shutil.copytree(repository / "infra" / "repair-worker", build_root / "infra" / "repair-worker")
    wheel_directory = tmp_path / "wheels"
    wheel_directory.mkdir()

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_directory),
            str(build_root),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    [wheel_path] = wheel_directory.glob("*.whl")
    with zipfile.ZipFile(wheel_path) as wheel:
        packaged_assets = {
            name.split(".data/data/", maxsplit=1)[1]
            for name in wheel.namelist()
            if ".data/data/infra/repair-worker/" in name
        }
    expected_assets = {
        f"infra/repair-worker/{path.name}"
        for path in (repository / "infra" / "repair-worker").iterdir()
        if path.is_file()
    }

    assert packaged_assets == expected_assets
