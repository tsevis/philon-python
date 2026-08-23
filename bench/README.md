# Philon benchmark harness

Copy `manifest.example.json` to a private, untracked manifest, point each entry at a locally held gold-corpus file, then run:

```sh
.venv/bin/python bench/run.py /path/to/private-manifest.json --output bench/results/run.json
```

Every result records the identity of the document it measured — filename, byte
count and SHA-256 — and never its path. The corpus is private, so the path is
not the thing to record; the digest is. Two runs carrying the same digests
provably read the same bytes, which is what makes "the same version-pinned
corpus" below something a reader can check rather than something a runner
asserts. Results written before 2026-08-23 do not carry it, and the corpora
behind them are not recoverable.

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
