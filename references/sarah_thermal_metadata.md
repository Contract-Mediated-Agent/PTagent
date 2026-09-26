# SARAH Companion Thermal Metadata

Schema: `ptagent.sarah_thermal.v1`.

The companion file aligns SARAH bases with Vevacious++ mass sectors and carries
source-derived tensor contractions. Required complete-data fields are:

- ordered `background_fields`, `scalar_basis`, and `gauge_basis`;
- `thermal_invariants.scalar_quartic`, `scalar_gauge`, and `scalar_yukawa`;
- `thermal_invariants.gauge_adjoint`, `gauge_scalar_trace`, and
  `gauge_fermion_trace`;
- scalar and gauge `sector_mappings` with zero-based matrix indices;
- `provenance.exporter_version` and `provenance.source_hashes`.

PTagent computes
`Pi_scalar/T^2 = lambda_abcc/24 + gauge_ab/4 + yukawa_ab/12` and
`Pi_L/T^2 = C_A/3 + T_scalar/3 + T_Weyl/6`. It then checks matrix shape,
symmetry, basis alignment, names, and provenance. Fermions and transverse gauge
modes are never Daisy-resummed.

The scalar result is also checked independently against the Hessian of the
leading high-temperature supertrace constructed from the imported boson and
Weyl-fermion mass matrices. When the thermal scalar basis is larger than the
background-field plane, `consistency.background_scalar_indices` must identify
the corresponding rows and columns.

For Parwani, scalar thermal matrices replace scalar matrices before
diagonalization. Gauge matrices are split into unresummed transverse and
resummed longitudinal modes. For Arnold-Espinosa, ordinary masses remain in
Coleman-Weinberg and thermal integrals, while only the zero-mode Daisy term uses
the resummed scalar and longitudinal gauge eigenvalues. `none` keeps the
unresummed route.

Metadata with `complete=false` is a valid partial export. It preserves source
facts and blockers but cannot authorize resummed compilation.
