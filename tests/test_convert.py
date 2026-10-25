import ezdxf
import pytest

from finishcalc.convert import ConvertError, dwg_to_dxf, find_oda
from tests.fixtures.synth import make_synthetic_plan


def test_dxf_passthrough(tmp_path):
    p = make_synthetic_plan(tmp_path / "a.dxf")
    assert dwg_to_dxf(p, tmp_path / "out") == p


def test_find_oda_env_override(tmp_path):
    exe = tmp_path / "ODAFileConverter.exe"
    exe.write_text("")
    assert find_oda({"ODA_FILE_CONVERTER": str(exe)}, tmp_path / "none") == exe


def test_dwg_without_oda_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("finishcalc.convert.find_oda", lambda *a, **k: None)
    (tmp_path / "a.dwg").write_bytes(b"")
    with pytest.raises(ConvertError, match="ODA File Converter"):
        dwg_to_dxf(tmp_path / "a.dwg", tmp_path / "out")


@pytest.mark.skipif(find_oda() is None, reason="ODA File Converter не установлен")
def test_roundtrip_through_dwg(tmp_path):
    from ezdxf.addons import odafc

    # ezdxf ищет конвертер по пути по умолчанию; указываем найденный.
    ezdxf.options.set("odafc-addon", "win_exec_path", str(find_oda()))
    src = make_synthetic_plan(tmp_path / "a.dxf")
    dwg = tmp_path / "a.dwg"
    odafc.convert(src, dwg, version="R2018", replace=True)
    out = dwg_to_dxf(dwg, tmp_path / "out")
    assert out.suffix == ".dxf"
    assert len(ezdxf.readfile(out).modelspace().query('LWPOLYLINE[layer=="_AREA"]')) == 7


@pytest.mark.skipif(find_oda() is None, reason="ODA File Converter не установлен")
def test_corrupt_dwg_raises(tmp_path):
    # На нечитаемом файле ODA пишет текст ошибки в выходной «.dxf» — это не результат.
    bad = tmp_path / "bad.dwg"
    bad.write_bytes(b"not a dwg at all" * 100)
    with pytest.raises(ConvertError, match="bad.dwg"):
        dwg_to_dxf(bad, tmp_path / "out")
    assert not (tmp_path / "out" / "bad.dxf").exists()
