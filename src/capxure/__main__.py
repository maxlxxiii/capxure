"""Entry point for `python -m capxure` and the console script."""


def main() -> None:
    from capxure.app import CapxureApp

    app = CapxureApp()
    app.run()


if __name__ == "__main__":
    main()
