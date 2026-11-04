"""Segmenter：SAM 2.1（Transformers，CPU），以偵測框當提示。用法與 S2-0 實測相同。"""

from __future__ import annotations

from pathlib import Path


class Sam2Segmenter:
    def __init__(self, model_dir: Path, threads: int = 4):
        import torch
        from transformers import Sam2Model, Sam2Processor

        torch.set_num_threads(threads)
        torch.manual_seed(0)
        self.torch = torch
        self.processor = Sam2Processor.from_pretrained(model_dir, local_files_only=True)
        self.model = Sam2Model.from_pretrained(model_dir, local_files_only=True).eval()
        self.last_scores: list[float] = []

    def segment(self, image, boxes: list[list[float]]) -> list:
        import numpy as np
        from PIL import Image

        if not boxes:
            return []
        im = Image.fromarray(image)
        inputs = self.processor(images=im, input_boxes=[boxes], return_tensors="pt")
        with self.torch.inference_mode():
            emb = self.model.get_image_embeddings(inputs["pixel_values"])   # encoder：每張圖一次
            outputs = self.model(input_boxes=inputs["input_boxes"], image_embeddings=emb,
                                 multimask_output=False)
        masks = self.processor.post_process_masks(outputs.pred_masks.cpu(), inputs["original_sizes"])[0]
        masks = masks.reshape(len(boxes), *masks.shape[-2:]).numpy().astype(bool)
        self.last_scores = [round(float(s), 4) for s in outputs.iou_scores.reshape(-1).tolist()]
        return [np.ascontiguousarray(m) for m in masks]

    def close(self) -> None:
        del self.model, self.processor
