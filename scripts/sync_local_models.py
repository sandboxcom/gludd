"""Pre-download all known small models for local inference."""

from general_ludd.small_models import KnownModels, ModelDownloader


def main() -> None:
    downloader = ModelDownloader()
    for model_id in KnownModels.all():
        print(f"Syncing {model_id} ...")
        result = downloader.download(model_id, force=True)
        print(f"  -> {result.local_path} ({result.size_bytes / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
