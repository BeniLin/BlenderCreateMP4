"""模型 registry：來源 repo、鎖定的 revision、每個檔案的 SHA-256、授權；下載與驗證。

- 所有模型都放在 config 的 paths.models_dir/<name>/，不使用使用者家目錄的快取。
- revision 一律是 commit hash（不使用浮動的 main）。
- 驗證：每次都完整計算「所有登記檔案」（權重、config、tokenizer…）的 SHA-256，不使用大小 / 修改時間的快取。
  三個模型合計約 875 MB，實測約 1 秒。
- 推論一律離線（子行程設定 HF_HUB_OFFLINE=1，並且只讀本機檔案）；只有 download() 會連網。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from animator.config import Config
from animator.errors import AnimatorError


@dataclass(frozen=True)
class ModelFile:
    name: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ModelSpec:
    name: str
    role: str
    repo: str
    revision: str
    weights: str                       # 主要的權重檔（顯示用）
    license: str
    commercial: bool
    files: tuple[ModelFile, ...] = field(default_factory=tuple)
    note: str = ""

    @property
    def size_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)

    @property
    def sha256(self) -> str:
        return next(f.sha256 for f in self.files if f.name == self.weights)

    @property
    def allow(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.files)


def _files(*items: tuple[str, int, str]) -> tuple[ModelFile, ...]:
    return tuple(ModelFile(*i) for i in items)


# 檔案清單與雜湊：S2-0 / S2-0.1 下載時記錄（models/s2-0-manifest.json、s2-0.1-manifest.json）
MODELS: dict[str, ModelSpec] = {
    "grounding-dino-tiny": ModelSpec(
        name="grounding-dino-tiny", role="detector",
        repo="IDEA-Research/grounding-dino-tiny", revision="a2bb814dd30d776dcf7e30523b00659f4f141c71",
        weights="model.safetensors", license="Apache-2.0", commercial=True,
        files=_files(
            ("model.safetensors", 689359096, "1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3"),
            ("config.json", 1644, "eec82c5ab66e16df12a9a212e68ac011779927c2536cf9078658e35d85f0c67a"),
            ("preprocessor_config.json", 457, "8454179ba95e2ad22947835aad7b45862a601fc0055ab88bf1ee70892d3aea60"),
            ("tokenizer.json", 711396, "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"),
            ("tokenizer_config.json", 1237, "d40ab645b68211910b9170d22433d43186a6ec8ee6fd10ba170524b25bf4fb56"),
            ("special_tokens_map.json", 125, "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3"),
            ("added_tokens.json", 82, "909e96cb32d92ce728a01bc99850cbba26196d74115c17ebeb019275412588f2"),
            ("vocab.txt", 231508, "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3"),
            ("README.md", 2580, "cf46f74c7b6850f1d5cbe406028324d8798148726016d46a69a365b4a2d3e89f"))),
    "sam2.1-hiera-tiny": ModelSpec(
        name="sam2.1-hiera-tiny", role="segmenter",
        repo="facebook/sam2.1-hiera-tiny", revision="de431c4043854a71d8101e17995dfe596bf101a5",
        weights="model.safetensors", license="Apache-2.0", commercial=True,
        files=_files(
            ("model.safetensors", 155908064, "48c14467e5cf9e51870511feb72c89688e82dd74523142c0538b663e193ac2a7"),
            ("config.json", 5695, "860aff9751b139d83a4ad7df1e5535416fded533e0ead02625edbefcb9953cce"),
            ("preprocessor_config.json", 683, "6ebf229ee259368ce4a8d4f2fe893a72b053023710853e257253939e601f583d"),
            ("processor_config.json", 95, "f8a68e865cfad115c1c2763f3d93eca7b1c622da06da2a9273eb437fb2389b6d"),
            ("video_preprocessor_config.json", 705,
             "9fccfe5f464ec38c2f236d0e6a68e95511c80c22132fc2fa4b9f7b65f24fad95"),
            ("README.md", 19965, "dfa401b1b9358b9650ae487dd2f2bb336fd745c3609e3244adf2e97a22343857"))),
    "migan": ModelSpec(
        name="migan", role="inpainter",
        repo="andraniksargsyan/migan", revision="406830d0fa60666da0071c342ad2fbc8f30c5c64",
        weights="migan_pipeline_v2.onnx", license="MIT", commercial=True,
        files=_files(
            ("migan_pipeline_v2.onnx", 28079181, "6f1f3530a1a2324b19752018ce756088b07973cda8d7d890034ace5c8a48c40b")),
        note="官方 Picsart-AI-Research/MI-GAN README 連結的 ONNX pipeline；LICENSE-WEIGHTS = MIT"),
}


def get_spec(name: str) -> ModelSpec:
    try:
        return MODELS[name]
    except KeyError:
        raise AnimatorError(f"不認識的模型：{name!r}（可用：{', '.join(MODELS)}）") from None


def model_dir(cfg: Config, name: str) -> Path:
    return cfg.path("models_dir") / get_spec(name).name


def hf_env(cfg: Config, offline: bool = True) -> dict[str, str]:
    """Hugging Face / torch / numba 的快取全部放在 models_dir 底下；offline=True 時禁止連網。"""
    models = cfg.path("models_dir")
    env = {
        "HF_HOME": str(models / ".hf_home"),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TORCH_HOME": str(models / ".torch"),
        "NUMBA_CACHE_DIR": str(models / ".numba_cache"),
    }
    if offline:
        env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    return env


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class ModelStatus:
    spec: ModelSpec
    path: Path
    present: bool
    verified: bool
    detail: str
    problems: list[str] = field(default_factory=list)


def status(cfg: Config, name: str, full_hash: bool = True) -> ModelStatus:
    """檢查所有登記的檔案。full_hash=False 時只檢查存在與大小（給 doctor / models list 快速顯示）。"""
    spec = get_spec(name)
    d = model_dir(cfg, name)
    problems = []
    present = (d / spec.weights).is_file()
    for f in spec.files:
        p = d / f.name
        if not p.is_file():
            problems.append(f"{f.name}：不存在")
        elif p.stat().st_size != f.size_bytes:
            problems.append(f"{f.name}：大小 {p.stat().st_size} ≠ {f.size_bytes}")
        elif full_hash and sha256_file(p) != f.sha256:
            problems.append(f"{f.name}：SHA-256 不符")
    if not present:
        return ModelStatus(spec, d, False, False, "尚未下載", problems)
    if problems:
        return ModelStatus(spec, d, True, False, "；".join(problems), problems)
    detail = f"{len(spec.files)} 個檔案 SHA-256 正確" if full_hash else f"{len(spec.files)} 個檔案大小正確（未計算雜湊）"
    return ModelStatus(spec, d, True, full_hash, detail)


def ensure_ready(cfg: Config, name: str) -> Path:
    """推論前呼叫：授權防呆 + 所有檔案的 SHA-256 驗證。回傳模型資料夾。"""
    spec = get_spec(name)
    if not spec.commercial and not cfg.get("ai.models.allow_noncommercial"):
        raise AnimatorError(f"模型 {name} 是非商用授權（{spec.license}），"
                            f"ai.models.allow_noncommercial 為 false，拒絕載入")
    st = status(cfg, name)
    if not st.verified:
        raise AnimatorError(f"模型 {name} 無法使用：{st.detail}（{st.path}）\n"
                            f"請執行：animator models download {name}")
    return st.path


def download(cfg: Config, name: str) -> ModelStatus:
    """唯一需要網路的步驟：從鎖定的 revision 只下載登記的檔案，下載後驗證 SHA-256，不符就刪除。"""
    spec = get_spec(name)
    d = model_dir(cfg, name)
    d.mkdir(parents=True, exist_ok=True)
    keys = list(hf_env(cfg, offline=True))
    old = {k: os.environ.get(k) for k in keys}
    os.environ.update(hf_env(cfg, offline=False))
    for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ.pop(k, None)
    try:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise AnimatorError("沒有安裝 huggingface_hub（AI 依賴見 requirements-ai.txt）") from exc
        snapshot_download(spec.repo, revision=spec.revision, local_dir=d, allow_patterns=list(spec.allow))
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    st = status(cfg, name)
    if not st.verified:
        for f in spec.files:
            p = d / f.name
            if p.is_file() and (p.stat().st_size != f.size_bytes or sha256_file(p) != f.sha256):
                p.unlink()
        raise AnimatorError(f"模型 {name} 下載後驗證失敗：{st.detail}（不符的檔案已刪除）")
    return st
