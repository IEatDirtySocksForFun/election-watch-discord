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
    va_elect          Virginia Department of Elections ENR JSON API
    oh_sos            Ohio Secretary of State live-results JSON API
    ne_sos            Nebraska Secretary of State server-rendered HTML (scraper)
    co_sos            Colorado Secretary of State Clarity feed
    ia_sos            Iowa Secretary of State Clarity feed
    ks_sos            Kansas Secretary of State HTML results pages
    la_sos            Louisiana Secretary of State graphical-results JSON blobs
    md_sbe            Maryland State Board of Elections: unofficial election-night pages,
                      with the certified breakdown CSVs as the fallback
    ms_sos            Mississippi Secretary of State statewide recap CSV
    mt_sos            Montana Secretary of State resultsSW.aspx pages (id from candidate filing)
    nd_sos            North Dakota Secretary of State resultsSW.aspx pages
    nm_sos            New Mexico Secretary of State resultsSW.aspx pages
    or_sos            Oregon Secretary of State ResultsAjax.svc JSON (GetMapData)
    ri_boe            Rhode Island Board of Elections JSON downloads
    sc_sec            South Carolina Election Commission (Clarity ENR, state host)
    sd_sos            South Dakota Secretary of State media CSV feed
    ut_lgov           Utah Lieutenant Governor (Enhanced Voting CDN export)
    vt_sos            Vermont Secretary of State static JSON
    wa_sos            Washington Secretary of State XML export
    mn_sos            Minnesota Secretary of State results file host (plain text exports)
    mi_county         Michigan Clarity county feeds, merged (partial state)
    wi_dane           Dane County, Wisconsin unofficial results page (scraper, opt-in)

Alternates for states whose statewide site is blocked (docs/results/alt-b.md):

    tx_counties       Texas county Clarity ENR feeds, merged (partial: N counties)
    nj_counties       New Jersey county Clarity ENR feeds, merged
    ny_nycboe         New York City Board of Elections recap CSVs
    mo_stlouis        St. Louis County per-precinct CSV export

`_common` holds the shared helpers (party normalisation, dates, payload decoding,
RaceResult construction). See docs/sources-results.md for feed URLs and status.
"""
