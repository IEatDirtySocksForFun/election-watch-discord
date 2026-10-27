"""Official state election-night results adapters.

One module per feed family; each registers a `ResultsSource` in
`electionwatch.sources.base.REGISTRY`:

    tx_sos   Texas Secretary of State static JSON
    ga_sos   Georgia Secretary of State export JSON
    nc_sbe   North Carolina State Board of Elections ENR files
    pa_dos   Pennsylvania Department of State election-returns API
    clarity  Clarity / Scytl county feeds (CLARITY_FEEDS)

`_common` holds the shared helpers (party normalisation, dates, payload decoding,
RaceResult construction). See docs/sources-results.md for feed URLs and status.
"""
