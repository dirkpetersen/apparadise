# apparadise overlay: builtin relion plus one CUDA 13 fix. Drop it once upstream has the fix.
from spack_repo.builtin.packages.relion.package import Relion as BuiltinRelion

from spack.package import *


class Relion(BuiltinRelion):
    def patch(self):
        super().patch()
        # relion passes -D__INTEL_COMPILER to nvcc to hide fftw's __float128 API (fftw3.h
        # already skips it under __CUDACC__ since 3.3.5). CUDA 13's CCCL then takes GCC for
        # icc and fails: "use of built-in trait '__remove_reference(_Tp)' in function signature".
        filter_file("-D__INTEL_COMPILER ", "", "cmake/BuildTypes.cmake", string=True)
