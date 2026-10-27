Montana Secretary of State — election-night results (electionresults.mt.gov)
===========================================================================

Fetched 2026-10-27 with a plain HTTP client (no cookies, no JS, descriptive User-Agent).

The bare host `https://electionresults.mt.gov/` answers HTTP 403 `cf-mitigated: challenge`
(Cloudflare), but the per-election results pages below answer 200 to a plain request. Every
election is addressed by a numeric `eid`; election ids come from the candidate-filing
dropdown (see 2026_candidate_filing_elections.html) and the same id opens the matching
results pages.

Sources:

  2026_candidate_filing_elections.html
      https://candidatefiling.mt.gov/candidatefiling/CandidateList.aspx?e=450002987
      Content-Type text/html; 168 kB. Trimmed to exactly the
      <select id="ddlElection"> element (the current cycle's elections, id + name + date +
      type). Two options at capture: 450002928 FEDERAL PRIMARY 2026 (06/02/2026),
      450002987 FEDERAL GENERAL 2026 (11/03/2026).

  2026_general_shell.html
      https://electionresults.mt.gov/Default.aspx?eid=450002987
      The live 2026 general election page before any contest has data: "2026 General
      Election - November 3, 2026", turnout 0, registered voters 1. Trimmed to the
      header-box-wrapper plus the "Results last updated" span (no contest blocks exist yet;
      on 2026-10-27 every resultsSW.aspx page for this eid still 302s to the error page).

  2024_general_*.html
      https://electionresults.mt.gov/resultsSW.aspx?type=<T>&map=CTY&eid=450002785
      T = FED, STATE, SENATE, HOUSE, PSC, SCJ, JUD, BQ — the complete 2024 general
      election. Trimmed to the header-box-wrapper, the "Results last updated" span and the
      named contests:

        fed.html               UNITED STATES SENATOR, US REPRESENTATIVE 1ST/2ND CONGRESSIONAL
        state.html             GOVERNOR & LT. GOVERNOR, SECRETARY OF STATE, ATTORNEY GENERAL,
                               STATE AUDITOR, STATE SUPERINTENDENT OF PUBLIC INSTRUCTION,
                               CLERK OF THE SUPREME COURT
        senate.html            STATE SENATOR DISTRICT 2, DISTRICT 3
        house.html             STATE REPRESENTATIVE DISTRICT 1, DISTRICT 2
        psc.html               PUBLIC SERVICE COMMISSIONER, DISTRICT 2
        supreme_court.html     SUPREME COURT CHIEF JUSTICE
        district_court.html    DISTRICT COURT JUDGE DISTRICT 2 DEPT 1 + the Judge MENAHAN
                               retention question
        ballot_issues.html     CI-126

      Inside each kept contest, the per-candidate bar-graph div
      (`col display-results-box-e`) and the "follow this contest" checkbox were removed as
      visual-only; every heading, candidate row, party label, vote count and the precinct
      counters are byte-for-byte the source's.
