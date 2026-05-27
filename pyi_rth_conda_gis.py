import os
import sys


def _set_if_exists(env_name: str, *relative_paths: str) -> None:
    bundle_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    for rel_path in relative_paths:
        candidate = os.path.join(bundle_dir, rel_path)
        if os.path.exists(candidate):
            os.environ[env_name] = candidate
            return


def _configure_frozen_runtime() -> None:
    if not getattr(sys, "frozen", False):
        return

    bundle_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))

    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(bundle_dir)

    _set_if_exists("PROJ_LIB", "proj", os.path.join("Library", "share", "proj"))
    _set_if_exists("PROJ_DATA", "proj", os.path.join("Library", "share", "proj"))
    _set_if_exists("GDAL_DATA", "gdal", os.path.join("Library", "share", "gdal"))


_configure_frozen_runtime()
