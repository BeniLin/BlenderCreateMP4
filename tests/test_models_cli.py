"""M5：`animator models list` / `doctor` 的模型狀態標籤（只用標準函式庫；用假的模型，不需要下載）。

list / doctor 只做快速檢查（檔案存在 + 大小，不計算 SHA-256）。大小都正確時應該顯示 OK，
並提示完整驗證的指令；只有 `models verify` 會計算 SHA-256，結果才算 verified。
"""

from __future__ import annotations

import hashlib

import pytest


@pytest.fixture
def fake(make_config, tmp_path, monkeypatch):
    from animator.segmentation import models as M

    d = tmp_path / "models" / "fake"
    d.mkdir(parents=True)
    files = {"weights.bin": b"w" * 1000, "config.json": b'{"a": 1}'}
    for n, b in files.items():
        (d / n).write_bytes(b)
    spec = M.ModelSpec(name="fake", role="test", repo="x/fake", revision="0" * 40, weights="weights.bin",
                       license="MIT", commercial=True,
                       files=tuple(M.ModelFile(n, len(b), hashlib.sha256(b).hexdigest()) for n, b in files.items()))
    monkeypatch.setattr(M, "MODELS", {"fake": spec})
    cfg = make_config(f"paths:\n  models_dir: '{(tmp_path / 'models').as_posix()}'\n"
                      f"  output_dir: '{(tmp_path / 'out').as_posix()}'\n  temp_dir: '{(tmp_path / 'tmp').as_posix()}'\n")
    return cfg, d


def _models(cfg, *args):
    from animator import cli

    return cli.main(["--config", str(cfg.config_file), "models", *args])


def _line(out):
    return next(l for l in out.splitlines() if " fake " in l)


def test_list_shows_ok_when_sizes_match_and_points_to_verify(fake, capsys):
    cfg, _ = fake
    assert _models(cfg, "list") == 0
    line = _line(capsys.readouterr().out)
    assert line.startswith("[OK]") and "未計算雜湊" in line and "animator models verify" in line


def test_verify_hashes_and_reports_ok(fake, capsys):
    cfg, _ = fake
    assert _models(cfg, "verify") == 0
    line = _line(capsys.readouterr().out)
    assert line.startswith("[OK]") and "SHA-256 正確" in line and "models verify" not in line


def test_wrong_size_is_fail_in_list_and_verify(fake, capsys):
    cfg, d = fake
    (d / "config.json").write_bytes(b'{"a": 12}')                    # 大小不同
    assert _models(cfg, "list") == 0                                  # list 的結束碼一律是 0（和以前一樣）
    assert _line(capsys.readouterr().out).startswith("[FAIL]")
    assert _models(cfg, "verify") == 1
    assert _line(capsys.readouterr().out).startswith("[FAIL]")


def test_same_size_but_wrong_hash_is_only_caught_by_verify(fake, capsys):
    cfg, d = fake
    (d / "config.json").write_bytes(b'{"a": 2}')                     # 大小相同、內容不同
    assert _models(cfg, "list") == 0
    assert _line(capsys.readouterr().out).startswith("[OK]")          # 快速檢查看不出來
    assert _models(cfg, "verify") == 1
    line = _line(capsys.readouterr().out)
    assert line.startswith("[FAIL]") and "SHA-256 不符" in line


def test_missing_model_is_not_ok(fake, capsys):
    cfg, d = fake
    (d / "weights.bin").unlink()
    assert _models(cfg, "list") == 0
    assert _line(capsys.readouterr().out).startswith("[----]")
