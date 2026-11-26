# Topaz Photo AI / Gigapixel AI – manual integration

The user's currently reported desktop installations are recorded in `TOPAZ_VERSION_INVENTORY.md`.

## What works without an API key

The desktop applications can be used interactively with their normal activation/account requirements.
Batch import, manual/automatic settings and export can provide a practical workflow without a platform API key.

## Supported platform workflow

1. Preserve original source and SHA-256.
2. Export a working copy plus manifest to a Topaz handoff folder.
3. Operator processes the copy in the installed Topaz desktop application.
4. Operator exports to a separate result folder.
5. Platform imports the result, validates image integrity, records provenance and compares quality.

## Automation boundary

No direct API integration is assumed. CLI use is not enabled by default: the exact product, version,
license tier and applicable EULA must be checked first. A product-window label containing “Pro”
does not by itself prove that the account holds a Professional license or CLI entitlement.

No unofficial GUI scripting, login automation, license bypass or cloud-account sharing is permitted.

## Cloud note

A missing API key does not prevent normal desktop use. It also does not guarantee offline operation:
activation, model downloads and optional cloud rendering may require an internet connection and an
authenticated Topaz account.
