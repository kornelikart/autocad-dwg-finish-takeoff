"""Конвертация DWG -> DXF через ODA File Converter (надстройка `odafc` пакета ezdxf)."""

import os
import re
from pathlib import Path
from typing import Mapping

import ezdxf
from ezdxf.addons import odafc
from ezdxf.lldxf.validator import is_binary_dxf_file, is_dxf_file

_ENV_VAR = "ODA_FILE_CONVERTER"
_EXE_NAME = "ODAFileConverter.exe"
_DEFAULT_SEARCH_ROOT = Path(r"C:\Program Files\ODA")
_DXF_VERSION = "R2018"
_NOT_FOUND = (
    "Не найден ODA File Converter: установите его с "
    "opendesign.com/guestfiles/oda_file_converter"
)


class ConvertError(Exception):
    """Файл не удалось преобразовать в DXF."""


def _version_key(folder: Path) -> tuple[tuple[int, ...], str]:
    # «ODAFileConverter 27.10.0» новее «ODAFileConverter 27.9.0», хотя как строка — меньше.
    numbers = tuple(int(n) for n in re.findall(r"\d+", folder.name))
    return numbers, folder.name


def _is_dxf(path: Path) -> bool:
    return is_dxf_file(str(path)) or is_binary_dxf_file(str(path))


def find_oda(
    env: Mapping[str, str] = os.environ,
    search_root: Path = _DEFAULT_SEARCH_ROOT,
) -> Path | None:
    """Найти ODAFileConverter.exe: переменная `ODA_FILE_CONVERTER`, затем самая новая
    версия в `search_root/<папка версии>/`. Не найден — None."""
    configured = env.get(_ENV_VAR)
    if configured and Path(configured).is_file():
        return Path(configured)
    candidates = sorted(search_root.glob(f"*/{_EXE_NAME}"), key=lambda p: _version_key(p.parent))
    return candidates[-1] if candidates else None


def dwg_to_dxf(src: Path, out_dir: Path, oda: Path | None = None) -> Path:
    """DXF возвращается как есть; DWG конвертируется в `out_dir/<имя>.dxf`.

    Raises:
        ConvertError: конвертер не найден или конвертация не удалась.
    """
    src = Path(src)
    if src.suffix.lower() == ".dxf":
        return src
    exe = oda or find_oda()
    if exe is None:
        raise ConvertError(_NOT_FOUND)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / (src.stem + ".dxf")
    ezdxf.options.set("odafc-addon", "win_exec_path", str(exe))
    try:
        odafc.convert(src, dest, version=_DXF_VERSION, audit=True, replace=True)
    except OSError as e:  # FileNotFoundError и все ошибки odafc наследуют OSError
        raise ConvertError(f"Не удалось преобразовать {src.name} в DXF: {e}") from e
    if not dest.is_file():
        raise ConvertError(f"Не удалось преобразовать {src.name} в DXF: ODA File Converter не создал файл")
    if not _is_dxf(dest):
        # На нечитаемом DWG конвертер всё равно создаёт «.dxf», но с текстом ошибки внутри.
        reason = dest.read_text(encoding="utf-8", errors="replace").strip()[:300]
        dest.unlink()
        raise ConvertError(f"Не удалось преобразовать {src.name} в DXF: {reason}")
    return dest
