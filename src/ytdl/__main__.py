"""Entry point. Activates any downloaded yt-dlp update before yt_dlp is imported."""

from ytdl import updater


def main() -> None:
    updater.activate()
    from ytdl.ui.app import main as run_app

    run_app()


if __name__ == "__main__":
    main()
