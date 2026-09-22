from pathlib import Path

from app.config import caminho_ascii, get_settings


def test_caminho_ascii_mantem_ascii(tmp_path):
    p = tmp_path / "chroma"
    assert caminho_ascii(p) == p


def test_caminho_ascii_converte_acento(tmp_path):
    p = tmp_path / "Históricos" / "chroma"
    out = caminho_ascii(p)
    assert str(out).isascii(), out
    # o caminho convertido deve ser utilizável e, quando é o nome curto 8.3, apontar para a mesma pasta
    out.mkdir(parents=True, exist_ok=True)
    (out / "marcador.txt").write_text("1", encoding="utf-8")
    assert (out / "marcador.txt").exists()
    if str(out).lower().startswith(str(tmp_path)[:3].lower()) and "~" in str(out):
        assert (p / "marcador.txt").exists()


def test_chroma_dir_e_ascii():
    d = get_settings().chroma_dir
    assert isinstance(d, Path)
    assert str(d).isascii()
