# SARAH/Vevacious++ Workflow

Use this route only for deterministic SARAH model exports. PTagent does not
infer a SARAH model from prose or a free-form Lagrangian.

## Import Existing Output

The recommended route needs no SARAH installation:

```bash
python <skill-dir>/scripts/ptagent_cli.py import-sarah \
  --vin MODEL.vin \
  --parameter-map ScaleAndBlock.xml \
  [--thermal-metadata ptagent_thermal_metadata.json] \
  [--slha spectrum.slha] \
  [--parameters parameters.json] \
  [--run-dir RUN_DIR]
```

Accept only Vevacious++ v2 XML. Do not treat an arbitrary `.vin`, `.m`, or
`.wl` file as a SARAH import. The importer rejects DTDs, entities, external XML
resources, oversized files, arbitrary expression syntax, unsupported schemes,
complex SLHA blocks, malformed matrices, and unresolved numerical parameters.

The importer copies source files into `input/` and writes deterministic records
under `proof_materials/`. `contract_template.md` remains the user-reviewed
source of truth. Imported matrix entries use `source_imported`, which is valid
only when the contract contains the input paths and SHA256 provenance.

Without SLHA or explicit JSON, symbolic import may finish but numerical smoke
and phase-transition runs remain blocked. Without complete thermal metadata,
tree-level and zero-temperature spectra remain usable, but Parwani and
Arnold-Espinosa are blocked. Do not fill either gap with an LLM guess.

## Export From SARAH

Use this optional route only when the user already has SARAH and a loadable
model:

```bash
python <skill-dir>/scripts/ptagent_cli.py export-sarah \
  --sarah-root /path/to/SARAH \
  --model MODEL_NAME \
  [--model-search-path /path/to/models] \
  [--slha spectrum.slha] \
  --run-dir RUN_DIR
```

The wrapper runs `MakeVevacious[Version -> "++"]`, then the bundled companion
exporter, and finally invokes the same importer used by `import-sarah`. It does
not modify SARAH. If a model does not expose the tensor data required by the
companion schema, the metadata records a deterministic blocker instead of
inventing thermal masses; the zero-temperature contract is still produced.

On macOS, `wolframscript` uses WSTP shared memory for local evaluation. Some
restricted agent sandboxes can block this transport and leave `wolframscript`
at high CPU before a kernel process is launched. Diagnose this by running the
licensed `WolframKernel` directly with a trivial batch expression. If the
kernel succeeds, request permission to run the existing `wolframscript`
outside the restricted sandbox. Do not change persistent Wolfram configuration
or ask the user to reactivate Mathematica unless the direct kernel check also
fails.

## Review Boundary

Do not ask the user to approve every imported matrix element. Ask for one
physics-policy batch covering species inclusion, resummation, Goldstone/photon
and gauge-mode policy, counterterms, symmetry or phase handling, and backend.
Resolve the contract again after that batch. Ask separately for final compile
approval, as in the ordinary 4D workflow.
