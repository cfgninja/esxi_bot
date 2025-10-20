"""Compat entry point for esxi_bot package."""

from esxi_bot.main import run as _run


def main() -> None:
    _run()


if __name__ == "__main__":
    main()
