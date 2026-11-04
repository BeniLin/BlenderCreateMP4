"""Detector：Grounding DINO（Transformers，CPU）。用法與 S2-0 實測相同。"""

from __future__ import annotations

from pathlib import Path


class GroundingDinoDetector:
    def __init__(self, model_dir: Path, box_threshold: float = 0.35, text_threshold: float = 0.25,
                 threads: int = 4):
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        torch.set_num_threads(threads)
        torch.manual_seed(0)
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(model_dir, local_files_only=True)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(model_dir, local_files_only=True).eval()
        self.box_threshold, self.text_threshold = box_threshold, text_threshold

    def detect(self, image, labels: list[str]) -> list[dict]:
        from PIL import Image

        im = Image.fromarray(image)
        w, h = im.size
        text = ". ".join(label.strip().lower() for label in labels) + "."
        size = {"shortest_edge": min(w, h), "longest_edge": max(w, h)}   # 用處理圖的尺寸推論，不放大
        inputs = self.processor(images=im, text=text, return_tensors="pt", size=size)
        with self.torch.inference_mode():
            outputs = self.model(**inputs)
        res = self.processor.post_process_grounded_object_detection(
            outputs, inputs.input_ids, threshold=self.box_threshold, text_threshold=self.text_threshold,
            target_sizes=[(h, w)])[0]
        names = res.get("text_labels", res.get("labels"))
        return [{"label": str(n), "label_raw": str(n), "score": round(float(s), 4),
                 "box": [round(float(v), 1) for v in b]}
                for n, s, b in zip(names, res["scores"], res["boxes"])]

    def close(self) -> None:
        del self.model, self.processor
