Trimmed real civicAPI early-vote responses, fetched 2026-10-27 with the User-Agent
"election-watch-discord/0.1 (+https://github.com/IEatDirtySocksForFun/election-watch-discord)".
Base: https://civicapi.org/api/v2/early-vote/ (endpoints used by https://civicapi.org/early-vote,
found in https://civicapi.org/assets/index-CIta9kix.js; not in the API docs).

us_returned.json / us_requested.json / us_inperson.json   GET .../US/{returned|requested|inperson}
    national map; "regions" cut from 44/40/33 states to AZ FL GA NC PA TX WI NV MI TN WV LA
us_grace.json                       GET .../US/grace                  (complete: IL only)
nc_returned.json, nc_inperson.json  GET .../NC/returned, .../NC/inperson
    regions cut from 100 counties to Wake Mecklenburg Guilford Durham Alamance Tyrrell
ga_returned.json, ga_inperson.json  GET .../GA/returned, .../GA/inperson
    regions cut from 159 counties to Fulton Cobb Gwinnett DeKalb Appling (no party data in GA)
nc_capabilities.json, ga_capabilities.json, tn_capabilities.json   GET .../{ST}/capabilities
ga_inperson_demographics_age_range.json  GET .../GA/inperson/demographics?by=age_range
nc_returned_demographics_party.json      GET .../NC/returned/demographics?by=party
historical_2022_nc_returned.json    GET .../historical/2022/NC/returned?date=2022-11-01
historical_2024_nc_returned.json    GET .../historical/2024/NC/returned?date=2024-10-29
    (regions cut to Wake, Mecklenburg). Argument order is year/state/category: the
    order historical/{state}/{country}/{year} answers 400.
historical_not_found.json           GET .../historical/2024/GA/inperson?date=2024-10-29  (HTTP 404)
not_found.json                      GET .../NY/returned                                  (HTTP 404)
invalid_parameters.json             GET .../US/GA                                        (HTTP 400)

States with any early-vote data on 2026-10-27 (46): every state except AL, MA, NH, NY and DC.
