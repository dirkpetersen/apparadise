# Base images

Each target has two images, and both are multi-arch where the target needs it:

| Image | From | Used by |
|---|---|---|
| `cache/base-builder:<cuda>-<target>` | `nvcr.io/nvidia/cuda:<cuda_full>-devel-ubuntu24.04` | Every build (Spack, EasyBuild, pixi) |
| `cache/base-runtime:<cuda>-<target>` | `nvcr.io/nvidia/cuda:<cuda_full>-runtime-ubuntu24.04` | The final stage of every published image |

```bash
cd containers/base
# rtx: build on an x86 node
podman build --platform linux/amd64 --build-arg CUDA_FULL=13.0.3 --build-arg TARGET=rtx \
  -f Containerfile.builder -t $REGISTRY/cache/base-builder:13.0-rtx .
podman build --platform linux/amd64 --build-arg CUDA_FULL=13.0.3 --build-arg TARGET=rtx \
  -f Containerfile.runtime -t $REGISTRY/cache/base-runtime:13.0-rtx .
# vr: the same on an aarch64 node, with --platform linux/arm64 and TARGET=vr
```

The rules:

- **Build natively.** Never use `--platform` for a foreign architecture (QEMU).
- **Record the pushed digests.** Push with `podman push --digestfile d.txt ...`, then put
  the digests under `base_digests` in `containers/targets.yml`. From then on every job
  builds `FROM cache/base-builder:<cuda>-<target>@sha256:...`, so a rebuilt base never
  silently changes an existing recipe.
- **Pin the NGC parent by digest** for production (`--build-arg BASE=nvcr.io/...@sha256:...`).
  Check that the NGC tag has an arm64 variant before relying on it for `vr`.
- **Use only the builder image's own toolchain.** The image carries Lmod, EasyBuild
  (non-editable), Spack, pixi, the RDMA headers and `config.guess`. That avoids every
  bootstrap failure in [the EasyBuild lessons](../lessons/easybuild-host-build.md).
- **Bump tool versions together:** `SPACK_REF`, `EASYBUILD_VERSION` and `PIXI_VERSION` move
  with [the stack page](../stack.md).
