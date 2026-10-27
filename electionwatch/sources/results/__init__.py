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
    ri_boe            Rhode Island Board of Elections JSON downloads
    sc_sec            South Carolina Election Commission (Clarity ENR, state host)
    ut_lgov           Utah Lieutenant Governor (Enhanced Voting CDN export)
    vt_sos            Vermont Secretary of State static JSON
    wa_sos            Washington Secretary of State XML export

`_common` holds the shared helpers (party normalisation, dates, payload decoding,
RaceResult construction). See docs/sources-results.md for feed URLs and status.
"""
