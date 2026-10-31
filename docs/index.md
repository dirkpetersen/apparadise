# apparadise

apparadise builds CUDA software once per hardware target, ships every build as a signed OCI
image, and exposes that image three ways: `module load`, `srun --container-image`, and
Run:ai. Software comes from Spack, EasyBuild or pixi/conda-forge, with one source per image.

| Target    | Hardware                                      |
|-----------|-----------------------------------------------|
| `vr`      | Vera Rubin NVL72 (Arm Vera CPU, Rubin GPU)    |
| `rtx`     | x86-64 RTX PRO 6000 Blackwell farm (`sm_120`) |
| `generic` | Portable fallback (`sm_70`–`sm_120` + PTX)    |

## Where things are

- **Web app** (`web/`): the catalog, sign-in, and later requests, votes and the builder API.
- **Catalog** (`catalog/*.yml`): hand-maintained in phase 1, loaded at startup.
- **Targets** (`containers/targets.yml`): every architecture flag, in one place.
- **Base images** (`containers/base/`): the builder and runtime images per target.
- **Recipe templates** (`recipes/_templates/`), rendered with `python -m apparadise.recipes`.
- **Modules** (`python -m apparadise.modules`): Lmod files plus Enroot wrappers.
- **Staging** (`tools/stage-sqsh`): a published image becomes read-only `.sqsh` on CephFS.

The phased plan is in `TODO.md`. Open questions and the defaults in use are in `QUESTIONS.md`.
