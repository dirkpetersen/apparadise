# Hand-building a package (phase 1)

Phase 1 builds the first 20 packages by hand, following the same six stages the automated
pipeline will use. Record each stage's result. A failure is a catalog entry with its stage,
not a silent gap.

## 1. Resolve

Pick **one** source with the fixed rule order (README, "Source resolution"). Never mix
sources inside one image.

## 2. Generate

Render the recipe for each target:

```bash
cd web
.venv/bin/python -m apparadise.recipes spack gromacs 2026.3 rtx --spec 'gromacs@2026.3+mpi+cufftmp'
.venv/bin/python -m apparadise.recipes easybuild relion 5.0.1 rtx --easyconfig RELION-5.0.1-foss-2025b-CUDA-12.9.1.eb
.venv/bin/python -m apparadise.recipes pixi pytorch 2.9.1 rtx --conda-name pytorch
```

The files land in `recipes/<name>/<version>/<target>/`. Commit them. For pixi, also run
`pixi lock` and commit `pixi.lock`. `vr` refuses to render until Rubin's compute capability
is filled into `containers/targets.yml` (Q13).

Failure stage: `concretize`. That covers Spack not solving, pixi not locking, or no
easyconfig for the toolchain.

## 3. Build

Build inside `cache/base-builder:<cuda>-<target>`, on a CPU node of the target's
architecture:

- **Spack:** `spack -e . containerize > Containerfile && podman build ...`, or install in the
  builder and copy `/opt/view` into the runtime image.
- **EasyBuild:** `eb --easystack easystack.yml --robot`. Run one EasyBuild job per
  (target, toolchain) tree at a time (Q9). Never use `--ignore-osdeps`.
- **pixi:** `pixi install --locked`, then copy the environment into the runtime image.

Failure stage: `build`.

## 4. Package

The final image is `FROM cache/base-runtime:<cuda>-<target>` and contains the software and
an SBOM. Tag it `public/<name>:<version>-<target>`, or `restricted/...` for restricted
software. Failure stage: `package`.

## 5. Test

On a real GPU node of the target, run as a low-priority job:

```bash
srun -p <gpu-partition> --gpus=1 --container-image=$REGISTRY#public/gromacs:2026.3-rtx gmx_mpi --version
```

The container must start unprivileged, see the GPU, and pass the smoke test. Failure stage:
`test`.

## 6. Publish

```bash
cosign sign --key ... $REGISTRY/public/gromacs@sha256:...
tools/stage-sqsh $REGISTRY public gromacs 2026.3 rtx sha256:... /shared/containers
# generic: run once on an x86 node and once on an Arm node, each with that arch's digest;
# the files become ...-generic-amd64.sqsh and ...-generic-arm64.sqsh
```

Then update `catalog/gromacs.yml` (`status: published`, `digest`, `test`, `built`) and
regenerate the modules (see [Modules and wrappers](modules.md)). Failure stage: `publish`.
