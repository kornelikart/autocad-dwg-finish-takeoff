import ezdxf
import pytest

from tests.fixtures.synth import make_synthetic_plan


@pytest.fixture
def synth_doc(tmp_path):
    """Синтетический чертёж: сохранён в tmp_path и открыт заново с диска."""
    return ezdxf.readfile(make_synthetic_plan(tmp_path / "synth.dxf"))
