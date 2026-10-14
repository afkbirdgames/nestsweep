"""Entry point: Nestsweep.exe, or `python -m nestsweep` from source."""


def main() -> None:
    from .app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
