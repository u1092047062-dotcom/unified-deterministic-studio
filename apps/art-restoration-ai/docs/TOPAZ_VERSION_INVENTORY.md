# Topaz inventory – operator-reported installations

Recorded from user-provided Windows screenshots and the successful Scanner 2.0 report on 2026-11-21.

| Product | Registry version | EXE file version | Confirmed path | Desktop launch | Authenticode |
|---|---:|---:|---|---|---|
| Topaz Photo AI | 4.1.0 | 4.1.0.0 | `C:\Program Files\Topaz Labs LLC\Topaz Photo AI\tpai.exe` | Installed; launch not separately demonstrated in this session | `NotSigned` |
| Topaz Gigapixel AI | 8.4.4 | 8.4.4.0 | `C:\Program Files\Topaz Labs LLC\Topaz Gigapixel AI\gigapixel.exe` | Desktop UI shown running | `NotSigned` |

## Interpretation and safeguards

- Registry and EXE version strings match for both detected products.
- `NotSigned` means Windows did not report a valid Authenticode signature for those EXE files. This alone does not prove tampering or authenticity. Verify installer provenance and hashes against a trusted vendor-provided source before production use.
- A visible “Pro” product label or “Floating-License” label is not proof of a particular API/CLI entitlement. Do not infer automation rights from UI labels.
- Normal interactive desktop usage may work without a platform API key; account activation, internet connectivity, downloads and vendor licensing still apply.
- Manual batch export/import is the supported integration baseline. CLI/API automation remains disabled until the exact version, license and vendor terms support it.
- No unofficial GUI scripting, login automation, license bypass or cloud-account sharing.
