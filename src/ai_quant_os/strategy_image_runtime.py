"""Parent-side engine/image metadata admission; no container or source execution."""

from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.repair_sandbox import DockerConfinement
from ai_quant_os.strategy_image_content import encode_metadata
from ai_quant_os.strategy_image_contracts import StrategyWorkerImageReceipt


class StrategyImageHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["IMAGE_LOAD_INSPECTED", "IMAGE_PRESENT_INSPECTED"]
    archive: StrategyWorkerImageReceipt
    loaded_image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    engine_id: str = Field(pattern=r"^[A-Za-z0-9:_-]{1,128}$")
    api_version: str = Field(pattern=r"^1\.[0-9]{1,3}$")
    engine_metadata_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    image_metadata_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def checked_handoff(self):
        if (
            self.archive.state != "ARCHIVE_HASHES_VERIFIED"
            or self.loaded_image_id != self.archive.manifest_digest
            or int(self.api_version.split(".")[1]) < 49
        ):
            raise ValueError("Image handoff requires verified archive and admitted identity")
        return self


def require_strategy_image_engine(engine: dict, version: dict) -> None:
    DockerConfinement.require_engine(engine)
    if (
        type(engine.get("ID")) is not str
        or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", engine["ID"])
        or type(engine.get("DriverStatus")) is not list
        or ["driver-type", "io.containerd.snapshotter.v1"] not in engine["DriverStatus"]
        or type(version) is not dict
        or type(version.get("ApiVersion")) is not str
        or not re.fullmatch(r"1\.[0-9]{1,3}", version["ApiVersion"])
        or int(version["ApiVersion"].split(".")[1]) < 49
    ):
        raise ValueError("Unsupported strategy image engine/store/API identity")


def require_loaded_strategy_image(image: dict, *, config: dict, descriptor: dict) -> None:
    if (
        type(image) is not dict
        or image.get("Id") != descriptor["digest"]
        or image.get("Os") != "linux"
        or image.get("Architecture") != "amd64"
        or image.get("Variant") not in (None, "")
        or type(image.get("Descriptor")) is not dict
        or any(
            encode_metadata({"value": image["Descriptor"].get(key)})
            != encode_metadata({"value": descriptor[key]})
            for key in ("digest", "mediaType", "size")
        )
        or type(image.get("RootFS")) is not dict
        or image["RootFS"].get("Type") != "layers"
        or image["RootFS"].get("Layers") != config["rootfs"]["diff_ids"]
        or type(image.get("Config")) is not dict
    ):
        raise ValueError("Loaded strategy image identity/platform/layers mismatch")
    runtime = image["Config"]
    expected = config["config"]
    for key, value in expected.items():
        if key not in runtime or encode_metadata({"value": runtime[key]}) != encode_metadata(
            {"value": value}
        ):
            raise ValueError("Loaded strategy image runtime differs from pinned archive")
    # Docker exposes optional legacy fields absent from the OCI config. Only
    # inert defaults are admitted; future additions require explicit review.
    inert = {
        "Hostname": ("",),
        "Domainname": ("",),
        "Image": ("",),
        "AttachStdin": (False,),
        "AttachStdout": (False,),
        "AttachStderr": (False,),
        "Tty": (False,),
        "OpenStdin": (False,),
        "StdinOnce": (False,),
        "ArgsEscaped": (False,),
        "NetworkDisabled": (False,),
        "OnBuild": (None, []),
        "Volumes": (None, {}),
        "ExposedPorts": (None, {}),
        "Healthcheck": (None,),
        "Shell": (None, []),
        "StopSignal": ("",),
        "StopTimeout": (None,),
    }
    for key in runtime.keys() - expected.keys():
        if key not in inert or all(
            encode_metadata({"value": runtime[key]}) != encode_metadata({"value": allowed})
            for allowed in inert[key]
        ):
            raise ValueError("Loaded strategy image has unsupported runtime setting")
