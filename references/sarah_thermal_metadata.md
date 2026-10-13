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

## Automatic Component Extraction

`export-sarah` writes `ptagent_sarah_components.json` (schema
`ptagent.sarah_components.v1`) and automatically constructs the companion
metadata. The Wolfram bridge reads the initialized SARAH state, not model names
or tutorial benchmarks. It enumerates canonical real scalars and left-handed
Weyl components, expands contracted generation/colour indices, and extracts the
full scalar potential and Weyl mass trace before restricting to background
fields. Complex scalar sectors use the same realification ordering as the
Vevacious++ export. Representation data includes massless Weyl multiplet
members, which contribute to gauge screening even when absent from `.vin`.

Let `q` be the complete canonical real scalar basis, `H = Hessian(V, q)`,
`F = Tr(M_Weyl M_Weyl^dagger)`, and `C_g` the gauge-coupling-weighted scalar
Casimir matrix. The shared Python engine computes

```text
scalar_quartic = Hessian(Tr(H), q)
scalar_yukawa  = Hessian(F, q) / 2
Pi_scalar/T^2 = scalar_quartic/24 + C_g/4 + scalar_yukawa/12
```

Gauge Debye contractions count a complex scalar with weight one, a real scalar
with weight one half, and a left-handed Weyl fermion with weight one in the
schema's `T_scalar` and `T_Weyl`. Generation and spectator representation
dimensions are included. SARAH's RGE Casimirs contain `GUTren^2` for Abelian
groups; the bridge removes that factor for the source-Lagrangian coupling
convention used by `.vin`. The independent source supertrace check verifies the
background projection of the resulting scalar gauge contribution.

This is the leading one-loop high-temperature, relativistic-matter
approximation, not a mass-dependent decoupling calculation. Particle removal
from loop spectra does not automatically rederive a different thermal bath.
Such a change requires a consistent reviewed treatment.

Before declaring readiness, the engine checks the full potential's background
projection, every scalar Hessian block, the Weyl trace, complete basis coverage,
and the independent `.vin` high-T supertrace. Symbolic coefficient comparisons
allow relative `1e-12` roundoff from SARAH's decimal XML serialization; missing
monomials are never accepted. Numeric parameters are not used to hide symbolic
disagreements. Thermal-only SLHA references join the ordinary parameter binding
and missing-value checks.

SARAH 4.14's `++` writer omits unmixed scalar sectors. The bridge registers their
existing `TreeMass` expressions as identity sectors in the running kernel;
it changes neither the installed model nor its Lagrangian. The recorded
`unmixed_scalar_sectors` identifies every such addition. Input model files,
exported XML, component JSON and exporter/engine files are SHA256-tracked.

The initial support boundary is real-parameter non-supersymmetric renormalizable
models with canonical scalar normalization, supported `SU(N)` and at most one
`U(1)` factor, and source sectors that remain closed under thermal corrections.
The implementation bounds expansion to 64 real scalar and 128 Weyl components.
CP-violating scalar bases, multiple Abelian factors, unsupported unmixed indices,
incomplete basis mappings and cross-sector thermal mixing fail closed. These
are capability limits, not recommendations to change a model. The scalar gauge
matrix and group traces come from source representation data; no LLM tensor
completion is involved. SM, a real-singlet extension, and both type-I and type-II
two-doublet models are tested using actual SARAH 4.14.5 exports.

When the loaded model already supplies complete `PTagent...` companion
variables, the legacy metadata interface is retained. Ordinary `.vin` import
without thermal metadata also retains its existing partial-import behavior.

Direct metadata imports obey the same sector-closure rule: scalar and gauge
thermal matrices must not mix a mapped mass sector with another sector or an
unmapped mode. Duplicate/overlapping mappings are blocked. Nonzero off-block
entries require a merged source mass sector; they are never dropped by taking
independent submatrices. Such an import remains inspectable, but its thermal
data cannot authorize resummed compilation.
