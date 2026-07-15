#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd)


def main() -> None:
    dev = "--dev" in sys.argv[1:]
    root = Path(__file__).resolve().parent
    venv_dir = root / ".venv"

    if not venv_dir.exists():
        print("Creando virtualenv...")
        run([sys.executable, "-m", "venv", str(venv_dir)])

    if os.name == "nt":
        python_bin = venv_dir / "Scripts" / "python.exe"
    else:
        python_bin = venv_dir / "bin" / "python"

    run([str(python_bin), "-m", "pip", "install", "--upgrade", "pip"])

    target = ".[dev]" if dev else "."
    run([str(python_bin), "-m", "pip", "install", "-e", target])

    env_file = root / ".env.redshift_extractor"
    example = root / ".env.example"
    if not env_file.exists() and example.exists():
        shutil.copyfile(example, env_file)
        print(f"Creado {env_file.name} desde .env.example. Editalo con tus datos.")

    print("\nInstalacion completada.")
    print("Activa el entorno virtual con:")
    if os.name == "nt":
        print(r"  .\.venv\Scripts\activate")
    else:
        print("  source .venv/bin/activate")
    print("Verifica con:  redshift-extractor ls")


if __name__ == "__main__":
    main()
