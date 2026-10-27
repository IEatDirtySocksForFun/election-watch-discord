"""Official state election-night results adapters.

One module per feed family; each registers a `ResultsSource` in
`electionwatch.sources.base.REGISTRY`:

    tx_sos            Texas Secretary of State static JSON
    ga_sos            Georgia Secretary of State export JSON
    nc_sbe            North Carolina State Board of Elections ENR files
    pa_dos            Pennsylvania Department of State election-returns API
    clarity           Clarity / Scytl county feeds (CLARITY_FEEDS)
    enhanced_voting   Enhanced Voting / Scytl CDN export (AZ, GA)
    fl_sos            Florida Department of State tab-delimited extract
    ks_sos            Kansas Secretary of State HTML results pages
    la_sos            Louisiana Secretary of State graphical-results JSON blobs
    md_sbe            Maryland State Board of Elections breakdown CSVs
    ms_sos            Mississippi Secretary of State statewide recap CSV
    nd_sos            North Dakota Secretary of State resultsSW.aspx pages
    nm_sos            New Mexico Secretary of State resultsSW.aspx pages

`_common` holds the shared helpers (party normalisation, dates, payload decoding,
RaceResult construction). See docs/sources-results.md for feed URLs and status.
"""
