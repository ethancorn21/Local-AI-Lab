#!/usr/bin/env python3
"""Repack a symmetric int4 group-128 checkpoint exported in auto_gptq packing (qweight/qzeros/scales,
e.g. AutoRound "packing_format": "auto_round:auto_gptq") into compressed-tensors pack-quantized
(weight_packed/weight_scale/weight_shape), the format HyperQwen's prepare/ scripts and kernels expect.

Lossless: same int4 values, same fp16 scales, only the layout changes. Runs inside the HyperQwen
image (needs torch, safetensors, compressed_tensors); one shard in RAM at a time.

usage: autoround-gptq-to-ct.py SRC DST REF_CONFIG
  REF_CONFIG  config.json of a compressed-tensors checkpoint of the same architecture, before
              HyperQwen's prepare (e.g. config.json.bak-quant); its quantization_config is copied.
Then run prepare/quant_heads_stream.py and prepare/build_draft_vocab.py on DST as for any checkpoint.
"""
import json, os, shutil, sys

import torch
from compressed_tensors.compressors.pack_quantized.base import pack_to_int32, unpack_from_int32
from safetensors import safe_open
from safetensors.torch import save_file

SHIFTS = torch.arange(0, 32, 4, dtype=torch.int32)


def unpack_in(qweight):   # [in/8, out] int32 -> [in, out], nibbles low first along the input dim
    return ((qweight.unsqueeze(1) >> SHIFTS.view(1, 8, 1)) & 0xF).reshape(-1, qweight.shape[1])


def unpack_out(qzeros):   # [groups, out/8] int32 -> [groups, out], nibbles low first along the output dim
    return ((qzeros.unsqueeze(2) >> SHIFTS.view(1, 1, 8)) & 0xF).reshape(qzeros.shape[0], -1)


def convert(f, prefix):
    qw = f.get_tensor(prefix + ".qweight")
    qz = f.get_tensor(prefix + ".qzeros")
    sc = f.get_tensor(prefix + ".scales")
    q = unpack_in(qw)                                   # [in, out], 0..15
    z = unpack_out(qz)
    # symmetric int4: the zero point is 8 by definition; gptq v1 stores it minus one (7), v2 as is (8)
    zs = z.unique().tolist()
    if len(zs) != 1 or zs[0] not in (7, 8):
        sys.exit(f"{prefix}: zero points {zs[:8]} are not one symmetric constant; not a sym checkpoint")
    n_in, n_out = q.shape
    if sc.shape != (n_in // 128, n_out):
        sys.exit(f"{prefix}: scales {tuple(sc.shape)} do not match group-128 of {n_out}x{n_in}")
    signed = (q.to(torch.int16) - 8).to(torch.int8).t().contiguous()   # [out, in], -8..7
    packed = pack_to_int32(signed, 4)
    back = unpack_from_int32(packed, 4, torch.Size([n_out, n_in]))
    if not torch.equal(back.to(torch.int8), signed):
        sys.exit(f"{prefix}: repack does not round-trip")
    return {prefix + ".weight_packed": packed,
            prefix + ".weight_scale": sc.t().contiguous(),            # [out, in/128], dtype kept (fp16)
            prefix + ".weight_shape": torch.tensor([n_out, n_in], dtype=torch.int64)}


def main(src, dst, ref):
    os.makedirs(dst, exist_ok=True)
    idx = json.load(open(os.path.join(src, "model.safetensors.index.json")))
    new_map, done = {}, 0
    for shard in sorted(set(idx["weight_map"].values())):
        out = {}
        with safe_open(os.path.join(src, shard), "pt") as f:
            keys = list(f.keys())
            if any(k.endswith(".g_idx") for k in keys):
                sys.exit(f"{shard}: g_idx present (act-order); this repack assumes none")
            prefixes = [k[:-len(".qweight")] for k in keys if k.endswith(".qweight")]
            for p in prefixes:
                out.update(convert(f, p))
                done += 1
            skip = {p + s for p in prefixes for s in (".qweight", ".qzeros", ".scales")}
            for k in keys:
                if k not in skip:
                    out[k] = f.get_tensor(k)
        save_file(out, os.path.join(dst, shard), metadata={"format": "pt"})
        new_map.update({k: shard for k in out})
        print(f"{shard}: {len(prefixes)} linears repacked, {len(out)} tensors", flush=True)
        del out
    idx["weight_map"] = dict(sorted(new_map.items()))
    json.dump(idx, open(os.path.join(dst, "model.safetensors.index.json"), "w"), indent=2)

    cfg = json.load(open(os.path.join(src, "config.json")))
    cfg["quantization_config"] = json.load(open(ref))["quantization_config"]
    json.dump(cfg, open(os.path.join(dst, "config.json"), "w"), indent=2)
    for name in os.listdir(src):
        if name not in ("config.json", "model.safetensors.index.json") and not name.endswith(".safetensors") \
                and os.path.isfile(os.path.join(src, name)):
            shutil.copy2(os.path.join(src, name), os.path.join(dst, name))
    print(f"{done} linears repacked -> {dst}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
