"""Production entry point for geochemical anisotropic halo reconstruction.

The implementation lives in ``geochem_geology_constrained`` so validation,
candidate-region extraction and the established service contract stay shared.
This named entry point prevents the enhanced workflow from silently falling
back to the former isotropic baseline.
"""

try:
    from .geochem_geology_constrained import main
except ImportError:
    from geochem_geology_constrained import main


if __name__ == "__main__":
    main()
