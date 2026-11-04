"""列出官方模型 repo 的檔案、大小、revision 與授權（需要網路，不下載權重）。"""

from common import MODEL_SPECS, setup_env

setup_env(offline=False)

from huggingface_hub import HfApi  # noqa: E402

api = HfApi()
for name, spec in MODEL_SPECS.items():
    info = api.model_info(spec["repo"], files_metadata=True)
    card = info.card_data.to_dict() if info.card_data else {}
    print(f"== {spec['repo']}  sha={info.sha}  license={card.get('license')}  modified={info.last_modified}")
    for s in info.siblings:
        print(f"   {s.rfilename:45s} {(s.size or 0) / 1e6:10.2f} MB")
