"""scipy ≥ 1.16 removed `sph_harm` (renamed to `sph_harm_y`). The unmaintained
`acoustics` lib (last release 2022) imports the old name at module load —
which fires the moment OWW does `import openwakeword.data`.

We don't care about acoustics.directivity (the only consumer of sph_harm);
OWW only uses acoustics.generator.noise. So aliasing the name is enough to
get past the import. The aliased function is never actually called at
runtime in any code path OWW touches.

Universal: applies on Linux + Windows alike when scipy is new. No-op on
older scipy (gated by hasattr).
"""
from __future__ import annotations


def apply() -> str:
    import scipy.special as _ss
    if hasattr(_ss, "sph_harm"):
        return "patch_scipy_acoustics: not needed (scipy still has sph_harm)"
    if not hasattr(_ss, "sph_harm_y"):
        return "patch_scipy_acoustics: SKIPPED (neither sph_harm nor sph_harm_y present — scipy too new?)"
    _ss.sph_harm = _ss.sph_harm_y  # type: ignore[attr-defined]
    return "patch_scipy_acoustics: aliased sph_harm -> sph_harm_y"
