"""Immutable S3-compatible mirror for verified bar and tick archives."""

from pathlib import Path, PurePosixPath

from botocore.exceptions import ClientError

from ai_quant_os.data_archive import BarArchive, BarManifest, _write_immutable
from ai_quant_os.tick_archive import TickArchive, TickManifest


class S3ArchiveMirror:
    def __init__(self, client, *, bucket: str, prefix: str = "") -> None:
        if not bucket.strip():
            raise ValueError("Bucket is required")
        parts = PurePosixPath(prefix).parts if prefix else ()
        if any(part in {".", ".."} for part in parts) or prefix.startswith("/") or "\\" in prefix:
            raise ValueError("Invalid object prefix")
        self._client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    def _key(self, relative: str) -> str:
        path = PurePosixPath(relative)
        if not relative or path.is_absolute() or ".." in path.parts or "\\" in relative:
            raise ValueError("Invalid archive object path")
        return f"{self.prefix}/{relative}" if self.prefix else relative

    def _get(self, relative: str) -> bytes:
        return self._client.get_object(Bucket=self.bucket, Key=self._key(relative))["Body"].read()

    def _put_immutable(self, relative: str, content: bytes) -> None:
        key = self._key(relative)
        try:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=content, IfNoneMatch="*")
        except ClientError as error:
            if error.response["Error"]["Code"] not in {
                "PreconditionFailed",
                "412",
                "ConditionalRequestConflict",
                "409",
            }:
                raise
        if self._get(relative) != content:
            raise ValueError(f"Immutable archive object changed: {key}")

    def publish_bar(self, archive: BarArchive, archive_id: str) -> BarManifest:
        archive.replay(archive_id)
        manifest = archive.manifest(archive_id)
        self._publish(
            archive,
            manifest.raw_path,
            manifest.parquet_path,
            f"manifests/{archive_id}.json",
        )
        return manifest

    def publish_tick(self, archive: TickArchive, archive_id: str) -> TickManifest:
        archive.replay(archive_id)
        manifest = archive.manifest(archive_id)
        self._publish(
            archive,
            manifest.raw_path,
            manifest.parquet_path,
            f"manifests/ticks/{archive_id}.json",
        )
        return manifest

    def _publish(self, archive, raw_path: str, parquet_path: str, manifest_path: str) -> None:
        for relative in (raw_path, parquet_path, manifest_path):
            self._put_immutable(relative, archive._path(relative).read_bytes())

    @staticmethod
    def _require_archive_id(archive_id: str) -> None:
        if len(archive_id) != 64 or any(
            character not in "0123456789abcdef" for character in archive_id
        ):
            raise ValueError("Invalid archive id")

    def restore_bar(self, archive_id: str, root: Path) -> BarManifest:
        self._require_archive_id(archive_id)
        archive = BarArchive(root)
        self._restore(archive, f"manifests/{archive_id}.json", archive_id)
        return archive.manifest(archive_id)

    def restore_tick(self, archive_id: str, root: Path) -> TickManifest:
        self._require_archive_id(archive_id)
        archive = TickArchive(root)
        self._restore(archive, f"manifests/ticks/{archive_id}.json", archive_id)
        return archive.manifest(archive_id)

    def _restore(self, archive, manifest_path: str, archive_id: str) -> None:
        _write_immutable(archive._path(manifest_path), self._get(manifest_path))
        manifest = archive.manifest(archive_id)
        for relative in (manifest.raw_path, manifest.parquet_path):
            _write_immutable(archive._path(relative), self._get(relative))
        archive.replay(archive_id)
