from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIRS = [
    ROOT_DIR / "data" / "processed",
    ROOT_DIR / "data" / "indexes",
    ROOT_DIR / "data" / "uploads",
]


def main() -> None:
    for path in DATA_DIRS:
        path.mkdir(parents=True, exist_ok=True)
        gitkeep = path / ".gitkeep"
        gitkeep.touch(exist_ok=True)
        print(f"ready: {path}")


if __name__ == "__main__":
    main()
