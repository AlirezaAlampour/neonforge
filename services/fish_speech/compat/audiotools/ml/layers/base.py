import inspect
import shutil
import tempfile
import typing
from pathlib import Path

import torch
from torch import nn


class BaseModel(nn.Module):
    """
    Lightweight copy of audiotools.ml.layers.BaseModel.

    Fish Speech's DAC model only needs the save/load helpers and `.device`.
    Keeping a local copy avoids importing the full audiotools package, which
    pulls in torchaudio binaries that do not match the DGX Spark base image.
    """

    EXTERN = [
        "audiotools.**",
        "tqdm",
        "__main__",
        "numpy.**",
        "julius.**",
        "torchaudio.**",
        "scipy.**",
        "einops",
    ]
    INTERN = []

    def save(
        self,
        path: str,
        metadata: dict = None,
        package: bool = True,
        intern: list = [],
        extern: list = [],
        mock: list = [],
    ):
        sig = inspect.signature(self.__class__)
        args = {}

        for key, val in sig.parameters.items():
            arg_val = val.default
            if arg_val is not inspect.Parameter.empty:
                args[key] = arg_val

        for attribute in dir(self):
            if attribute in args:
                args[attribute] = getattr(self, attribute)

        metadata = {} if metadata is None else metadata
        metadata["kwargs"] = args
        if not hasattr(self, "metadata"):
            self.metadata = {}
        self.metadata.update(metadata)

        if not package:
            state_dict = {"state_dict": self.state_dict(), "metadata": metadata}
            torch.save(state_dict, path)
        else:
            self._save_package(path, intern=intern, extern=extern, mock=mock)

        return path

    @property
    def device(self):
        return list(self.parameters())[0].device

    @classmethod
    def load(
        cls,
        location: str,
        *args,
        package_name: str = None,
        strict: bool = False,
        **kwargs,
    ):
        try:
            model = cls._load_package(location, package_name=package_name)
        except Exception:
            model_dict = torch.load(location, "cpu")
            metadata = model_dict["metadata"]
            metadata["kwargs"].update(kwargs)

            sig = inspect.signature(cls)
            class_keys = list(sig.parameters.keys())
            for k in list(metadata["kwargs"].keys()):
                if k not in class_keys:
                    metadata["kwargs"].pop(k)

            model = cls(*args, **metadata["kwargs"])
            model.load_state_dict(model_dict["state_dict"], strict=strict)
            model.metadata = metadata

        return model

    def _save_package(self, path, intern=[], extern=[], mock=[], **kwargs):
        package_name = type(self).__name__
        resource_name = f"{type(self).__name__}.pth"

        if hasattr(self, "importer"):
            kwargs["importer"] = (self.importer, torch.package.sys_importer)
            del self.importer

        with tempfile.NamedTemporaryFile(suffix=".pth") as f:
            with torch.package.PackageExporter(f.name, **kwargs) as exp:
                exp.intern(self.INTERN + intern)
                exp.mock(mock)
                exp.extern(self.EXTERN + extern)
                exp.save_pickle(package_name, resource_name, self)

                if hasattr(self, "metadata"):
                    exp.save_pickle(
                        package_name, f"{package_name}.metadata", self.metadata
                    )

            shutil.copyfile(f.name, path)

        if "importer" in kwargs:
            self.importer = kwargs["importer"][0]
        return path

    @classmethod
    def _load_package(cls, path, package_name=None):
        package_name = cls.__name__ if package_name is None else package_name
        resource_name = f"{package_name}.pth"

        imp = torch.package.PackageImporter(path)
        model = imp.load_pickle(package_name, resource_name, "cpu")
        try:
            model.metadata = imp.load_pickle(package_name, f"{package_name}.metadata")
        except Exception:
            pass
        model.importer = imp

        return model

    def save_to_folder(
        self,
        folder: typing.Union[str, Path],
        extra_data: dict = None,
        package: bool = True,
    ):
        extra_data = {} if extra_data is None else extra_data
        model_name = type(self).__name__.lower()
        target_base = Path(f"{folder}/{model_name}/")
        target_base.mkdir(exist_ok=True, parents=True)

        if package:
            package_path = target_base / "package.pth"
            self.save(package_path)

        weights_path = target_base / "weights.pth"
        self.save(weights_path, package=False)

        for path, obj in extra_data.items():
            torch.save(obj, target_base / path)

        return target_base

    @classmethod
    def load_from_folder(
        cls,
        folder: typing.Union[str, Path],
        package: bool = True,
        strict: bool = False,
        **kwargs,
    ):
        folder = Path(folder) / cls.__name__.lower()
        model_pth = "package.pth" if package else "weights.pth"
        model_pth = folder / model_pth

        model = cls.load(model_pth, strict=strict)
        extra_data = {}
        excluded = ["package.pth", "weights.pth"]
        files = [x for x in folder.glob("*") if x.is_file() and x.name not in excluded]
        for f in files:
            extra_data[f.name] = torch.load(f, **kwargs)

        return model, extra_data
