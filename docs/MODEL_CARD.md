# Model Card: Kuro Siwo SNUNet

## Provenance

- Model: SNUNet-ECAM linked as the pretrained SNUNet by [Orion-AI-Lab/KuroSiwo](https://github.com/Orion-AI-Lab/KuroSiwo).
- Checkpoint: `best_segmentation.pt`, 144,657,509 bytes; SHA-256 `6c6bcb78d956f9d139743eb5bc44cf8e14528b643f9cbf5f4478f4e321a609ed`.
- Repository revision inspected: `4347ed173c4e48f5a9d578bb5fe8453706b08e5`. The hosted checkpoint is not versioned and contains no data configuration.
- License: the user supplied the MIT notice for Orion Lab, matching the upstream repository `LICENSE`; see [Third-Party Notices](../THIRD_PARTY_NOTICES.md) and assumption A5. The upstream README explicitly identifies CC BY for the dataset, not separately for the checkpoint.

## Input Contract

The verified checkpoint has a 3-channel stem and 3 output logits. The input semantics and normalization follow the Kuro Siwo [SNUNet wrapper reference](https://github.com/Orion-AI-Lab/KuroSiwo/pull/4/files); that wrapper is a source reference, not embedded metadata in the Dropbox checkpoint.

Pass two aligned tensors in `(pre_event, post_event)` order. Each tensor has bands `(VV sigma0 linear, VH sigma0 linear, slope_riserun)`. VV and VH are clamped to `[0, 0.15]`, then standardized using means `(0.0953, 0.0264)` and standard deviations `(0.0427, 0.0215)`. The shared dimensionless Horn slope is standardized with mean `2.9482` and standard deviation `79.2493`.

Kuro Siwo training samples use `224 x 224` patches. SNUNet is fully convolutional but has four pooling stages, so each tile dimension must be divisible by 16. VALLEYEYE defaults to `224 x 224` inference tiles and keeps a configurable 32-pixel context margin.

## Output Contract

The three logits map to class IDs `0=no_water`, `1=permanent_water`, and `2=flood`, based on the Kuro Siwo training labels. VALLEYEYE exports class-2 softmax probability; its binary mask applies the configured threshold. Nodata stays nodata. Permanent-water predictions are not reported as flood.

## Limitations

The checkpoint file does not itself identify the third channel or embed normalization metadata. The channel meaning and slope statistics are inferred from the Kuro Siwo wrapper reference and must be validated on an allowed held-out dataset before operational use. Synthetic tests verify software behavior and checkpoint loading, not flood-detection accuracy. Download the weights with `python -m valleyeye.ml.fetch_weights`; the file is ignored by Git and its hash is checked at load time.
