# Philon benchmark harness

Copy `manifest.example.json` to a private, untracked manifest, point each entry at a locally held gold-corpus file, then run:

```sh
.venv/bin/python bench/run.py /path/to/private-manifest.json --output bench/results/run.json
```

It records deterministic native-engine measures: cold/warm duration, pages/second, page and block counts, warnings/uncertainty rate, source-map coverage, cache status, output-contract failures, process peak RSS, native-fast-path routing, and manifest gates. If a private gold Markdown file, formula string, or expected table cells are declared, it also records word accuracy, formula similarity, and table-cell accuracy. The corpus remains private.

The harness deliberately records unavailable comparator commands and absent gold annotations as evidence states; it never converts those absences into a parity claim. It does not benchmark Marker or Docling automatically: those comparisons must use separately approved, version-pinned installations and the same corpus and machine profile.

To benchmark an external tool, declare it in the private manifest's
`comparators` object. Each comparator supplies a string-array `command` with
`{input}` and `{output}` placeholders plus an optional `version_command`.
Philon runs that command as a separate process, records timing, stderr/stdout,
and produced artifacts, and never imports the tool's code, ships its models, or
downloads anything on its behalf. The sample manifest shows a local
`marker_single` invocation; add Docling only after pinning its installed version
and command-line arguments in the same way.
