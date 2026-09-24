# HarfBuzz (vendored)

Version: 14.5.0 (https://github.com/harfbuzz/harfbuzz/releases/tag/14.5.0)
License: Old MIT (see COPYING).

Only `src/harfbuzz.cc`, the upstream single-translation-unit amalgamation, is
compiled (see `library.json` srcFilter). The rest of `src/` is vendored
alongside it because `harfbuzz.cc` and the OT shapers pull in sibling
`.cc`/`.hh` files by `#include`.

## What was dropped from upstream `src/`
- `tests`, `test-*.cc`, `main.cc`, generator/check `*.py` scripts, `util/`.
- Platform text backends we don't use: CoreText, DirectWrite, Uniscribe, GDI,
  Graphite2, ICU, GLib/GObject, FreeType, Cairo, wasm, the `subset` API, and
  `fontations`.
- Since upstream `harfbuzz.cc` unconditionally `#include`s the backend `.cc`
  files above (they normally no-op via `#if defined(HAVE_*)`), those include
  lines were removed from our copy of `harfbuzz.cc` so the file list matches
  what's actually vendored.

## Patches to upstream sources
- `harfbuzz.cc`: removed the 8 `#include` lines for the dropped platform
  backends (CoreText x3, DirectWrite x3, FreeType, GDI, GLib, Graphite2,
  Uniscribe, wasm x2) listed above.
- `hb-open-type.hh` unconditionally includes `hb-subset.hh` even though
  subsetting is a separate optional API — it's part of the core header chain,
  not a backend. Its declarations are needed at parse time even though no
  `hb-subset-*.cc` is compiled (subset entry points are never called and get
  linked out). So `hb-subset*.hh`/`.h` headers were kept/restored; only the
  subset `.cc` implementation files were dropped.

## library.json note
`srcFilter: ["+<harfbuzz.cc>"]` (relative to `includeDir`'s implicit `src/`
source root), not `"+<src/harfbuzz.cc>"` — PlatformIO's library source root for
a `src/`-layout library is already `src/`, so a `src/`-prefixed filter looked
for `src/src/harfbuzz.cc` and silently compiled nothing.

## What's kept
The default OT shaper plus the Arabic, Hebrew, Thai, Khmer, Myanmar, Hangul,
Indic (including the generated indic table) and USE shapers, per the
`-DHB_NO_OT_SHAPER_*` flags in `library.json` (Indic/Bengali is kept enabled).

## Config defines
All `-DHB_NO_*`/`-DHB_TINY`/`-DHB_LEAN`/`-DHB_NO_MT` flags in `library.json`
are valid `hb-config.hh` toggles as of 14.5.0 - none had to be dropped.
