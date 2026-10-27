# Party codes and emoji markers

`electionwatch/data/parties.yaml` is the single source of truth for party codes, their
bucket and the marker the bot puts in front of a candidate. Adapters should store the
canonical `code`; anything else (a state affiliate, an abbreviation with periods, an
old ballot line) is resolved by `electionwatch/parties.py` through the alias list.

```python
from electionwatch.parties import party_code, party_emoji

party_code("DFL")                            # 'DEM'
party_emoji("Constitution Party of Oregon")  # '📜'
party_emoji("Nobody We Know")                # '⚪'
```

Matching is case- and punctuation-insensitive, and never touches the network. Unknown
parties pass through uppercased with the ⚪ marker.

This page is generated — edit the YAML, then run `python tools/parties_table.py`.

93 parties, 488 aliases.

## Buckets

| Bucket | Meaning |
| --- | --- |
| `dem` | Democratic Party — 🔵 (1) |
| `rep` | Republican Party — 🔴 (1) |
| `libertarian` | Libertarian Party and its state affiliates — 🟡 (1) |
| `green` | Green Party and the state parties with their own ballot line — 🟢 (5) |
| `socialist` | socialist and communist parties — a distinct emoji each, in order of party size (14) |
| `far_right` | parties that reliable sources call neo-Nazi, white nationalist or fascist — a distinct emoji each (5) |
| `constitution` | Constitution Party, its state affiliates and ballot lines — 📜 (3) |
| `independent` | independent and no-party labels — ⚪ (10) |
| `centrist` | centrist and big-tent reform parties (14) |
| `other` | everything else: progressive, conservative, single-issue and regional parties (39) |

## Table

| Code | Party | Bucket | Emoji | Aliases |
| --- | --- | --- | --- | --- |
| `DEM` | Democratic Party | dem | 🔵 | D, DEM, Democrat, Democrats, Democratic, Democratic Party, DFL, D-F-L, DFL Party, Minnesota DFL, Minnesota Democratic-Farmer-Labor Party, Democratic-Farmer-Labor, Democratic-Farmer-Labor Party, Democratic-NPL, D-NPL, North Dakota Democratic-Nonpartisan League Party, Democratic Nonpartisan League, NPL |
| `REP` | Republican Party | rep | 🔴 | R, REP, GOP, Republican, Republicans, Republican Party, Grand Old Party |
| `LIB` | Libertarian Party | libertarian | 🟡 | LBN, L, LIB, LBT, LP, Libertarian, Libertarian Party, Libertarian Party USA, Libertarian Party of New York, Libertarian Party of Pennsylvania, Libertarian Party of Texas, Libertarian Party of Florida, Libertarian Party of Ohio, Libertarian Party of Michigan, Libertarian Party of Virginia, Libertarian Party of Connecticut, Libertarian Party of New Hampshire, Libertarian Party of Wisconsin, Libertarian Association of Massachusetts |
| `GRN` | Green Party of the United States | green | 🟢 | G, GRE, GRN, GP, GPUS, GP-US, Green, Greens, Green Party, Green Party USA, Green Party of the United States, Green Party of New York, Green Party of Pennsylvania, Green Party of Michigan, Green Party of California, Green Party of Texas, Green Party of Ohio, Green Party of Wisconsin, Green Party of Alaska, Green Party of Connecticut, Connecticut Green Party, Green Party of Rhode Island, DC Statehood Green Party, D.C. Statehood Green Party, Statehood Green |
| `MGIP` | Maine Green Independent Party | green | 🟢 | MGIP, Maine Green Independent, Green Independent Party, Maine Greens |
| `GRP` | Green-Rainbow Party | green | 🟢 | Green-Rainbow, GRP, Massachusetts Green-Rainbow Party |
| `MTP` | Mountain Party | green | 🟢 | MTN, MTP, Mountain Party of West Virginia, West Virginia Mountain Party |
| `PACG` | Pacific Green Party | green | 🟢 | PGP, PACG, Pacific Green, Pacific Green Party of Oregon |
| `DSA` | Democratic Socialists of America | socialist | ✊ | DSA, DSA-USA, Democratic Socialists, Democratic Socialists of America |
| `PSL` | Party for Socialism and Liberation | socialist | 🌹 | PSL, Party for Socialism and Liberation, Socialism and Liberation, Socialism & Liberation, Party for Socialism & Liberation, Liberation Party |
| `CPUSA` | Communist Party USA | socialist | ☭ | CPUSA, CP, Communist, Communist Party, Communist Party USA, Communist Party of the USA, Communist Party of the United States |
| `SWP` | Socialist Workers Party | socialist | 🚩 | SWP, Socialist Workers, Socialist Workers Party |
| `SPUSA` | Socialist Party USA | socialist | 🔨 | SPUSA, SP, SOC, Socialist, Socialist Party, Socialist Party USA, Socialist Party of the United States, Socialist Party of America, Socialist Party of New Jersey |
| `SA` | Socialist Alternative | socialist | 🌾 | SA, SAlt, Socialist Alternative |
| `WWP` | Workers World Party | socialist | ⭐ | WWP, Workers World, Workers World Party |
| `SLP` | Socialist Labor Party of America | socialist | 🛠️ | SLP, Socialist Labor, Socialist Labor Party, Socialist Labor Party of America |
| `FSP` | Freedom Socialist Party | socialist | ⚒️ | FSP, Freedom Socialist, Freedom Socialist Party |
| `SEP` | Socialist Equality Party | socialist | 📕 | SEP, Socialist Equality, Socialist Equality Party |
| `PLP` | Progressive Labor Party | socialist | 🏗️ | PLP, Progressive Labor, Progressive Labor Party |
| `WSPUS` | World Socialist Party of the United States | socialist | 🌍 | WSPUS, World Socialist Party, World Socialist Party of the United States |
| `ACP` | American Communist Party | socialist | 🔥 | ACP, American Communist Party |
| `SACT` | Socialist Action | socialist | ⚡ | SAct, Socialist Action |
| `ANP` | American Nazi Party | far_right | ⛓️ | ANP, American Nazi Party, Nazi Party, National Socialist White People's Party, NSWPP |
| `NSM` | National Socialist Movement | far_right | 🔒 | NSM, National Socialist Movement, National Socialist Movement USA |
| `AFP` | American Freedom Party | far_right | 🚔 | AFP, American Freedom Party, American Third Position Party, Third Position Party |
| `TWP` | Traditionalist Worker Party | far_right | 🏴 | TWP, Traditionalist Worker Party, Traditionalist Workers Party |
| `NRP` | National Renaissance Party | far_right | ⚠️ | NRP, National Renaissance Party |
| `CST` | Constitution Party | constitution | 📜 | IAP, CST, CON, Constitution, Constitution Party, U.S. Constitution Party, US Constitution Party, Constitution Party USA, Constitution Party of Oregon, Constitution Party of Pennsylvania, Constitution Party of Idaho, Constitution Party of Michigan, Constitution Party of Wisconsin, Constitution Party of Virginia, Constitution Party of Nevada, Constitution Party of Missouri, American Constitution Party, Taxpayers Party, U.S. Taxpayers Party, US Taxpayers Party, U.S. Taxpayers, Taxpayers Party of New York |
| `AIP` | American Independent Party | constitution | 📜 | AIP, AI, American Independent, American Independent Party, American Independent Party of California, California American Independent Party |
| `UIAP` | Utah Independent American Party | constitution | 📜 | Independent American Party, UIAP, Independent American, Independent American Party of Nevada, Utah Independent American |
| `IND` | Independent | independent | ⚪ | I, IND, Independent, Independents, Independent Candidate, Independent Party |
| `NP` | No Party / Nonpartisan | independent | ⚪ | NP, NOP, NPA, NPP, U, UNA, UNK, No Party, No Party Affiliation, No Party Preference, No Affiliation, Nonpartisan, Non-Partisan, Unaffiliated, Unenrolled, Decline to State, Blank, None, Not Affiliated, Unknown |
| `IPNY` | Independence Party of New York | independent | ⚪ | IPNY, Independence Party of New York, NY Independence Party |
| `IPDE` | Independent Party of Delaware | independent | ⚪ | IPDE, IDE, IPoD, Independent Party of Delaware, Delaware Independent Party |
| `IPCT` | Independent Party of Connecticut | independent | ⚪ | IPCT, Independent Party of Connecticut, Connecticut Independent Party |
| `IPFL` | Independent Party of Florida | independent | ⚪ | IPFL, IPF, Independent Party of Florida, Independence Party of Florida, Florida Independent Party |
| `IPLA` | Independent Party of Louisiana | independent | ⚪ | IPLA, IPL, Independent Party of Louisiana, Louisiana Independent Party |
| `IPO` | Independent Party of Oregon | independent | ⚪ | IPO, Independent Party of Oregon, Oregon Independent Party |
| `AKIP` | Alaskan Independence Party | independent | ⚪ | AKIP, Alaska Independence, Alaska Independence Party, Alaskan Independence, Alaskan Independence Party |
| `UIP` | United Independent Party | independent | ⚪ | UIP, United Independent Party, United Independent Party of Massachusetts |
| `FWD` | Forward Party | centrist | ➡️ | FWD, Forward, Forward Party, Forward Party USA, Forward Party of Utah, Serve America Movement, SAM, Renew America Movement |
| `REF` | Reform Party | centrist | 🔧 | REF, Reform, Reform Party, Reform Party USA, Reform Party of the United States, Reform Party of the United States of America, Reform Party of Florida |
| `ALN` | Alliance Party | centrist | 🤝 | ALN, Alliance, Alliance Party, Alliance Party USA, Alliance Party of South Carolina |
| `NOL` | No Labels | centrist | 🏷️ | NOL, No Labels, No Labels Party, No Labels Party of Arizona, No Labels Arizona |
| `UNT` | Unity Party of America | centrist | 🤲 | UNT, Unity, Unity Party, Unity Party of America |
| `UUT` | United Utah Party | centrist | 🏔️ | UUT, United Utah, United Utah Party |
| `MOD` | Moderate Party of Rhode Island | centrist | 🧭 | MOD, Moderate, Moderate Party, Moderate Party of Rhode Island, Rhode Island Moderate Party, RI Moderate Party |
| `MNJ` | Moderate Party of New Jersey | centrist | 🎚️ | MNJ, Moderate Party of New Jersey, New Jersey Moderate Party, NJ Moderate Party |
| `CCP` | Colorado Center Party | centrist | 🎯 | CCP, Colorado Center, Colorado Center Party, Center Party |
| `CSP` | Common Sense Party of California | centrist | 💡 | CSP, Common Sense, Common Sense Party, Common Sense Party of California |
| `WTP` | We the People Party | centrist | 🇺🇸 | WTP, We the People, We the People Party |
| `RMA` | Republican Moderate Party of Alaska | centrist | 🌨️ | RMA, Republican Moderate, Republican Moderate Party, Republican Moderate Party of Alaska, Alaskan Republican Moderate Party |
| `FIP` | Forward Independence Party | centrist | ↔️ | FIP, Forward Independence, Forward Independence Party, Independence Party of Minnesota, Independence-Alliance Party, Independence Party, Minnesota Independence Party, IPM |
| `UKS` | United Kansas | centrist | 🔷 | UKS, United Kansas, United Kansas Party, Free State Party |
| `WFP` | Working Families Party | other | 🧰 | WOR, WFP, Working Families, Working Families Party, Working Families Party of New York, Connecticut Working Families Party |
| `PFP` | Peace and Freedom Party | other | 🕊️ | PFP, PAF, PF, Peace and Freedom, Peace & Freedom, Peace and Freedom Party |
| `WCP` | Working Class Party | other | 🏭 | WCP, Working Class, Working Class Party, Working Class Party of Michigan |
| `NLP` | Natural Law Party | other | 🧘 | NLP, Natural Law, Natural Law Party, Natural Law Party of Michigan |
| `PRO` | Prohibition Party | other | 🚱 | PRO, P, Prohibition, Prohibition Party, Prohibition National Party |
| `ASP` | American Solidarity Party | other | ⚖️ | ASP, American Solidarity, American Solidarity Party, Solidarity Party |
| `BTP` | Boston Tea Party | other | 🫖 | BTP, Boston Tea, Boston Tea Party |
| `TRA` | Transhumanist Party | other | 🧬 | TRA, Transhumanist, Transhumanist Party, US Transhumanist Party, U.S. Transhumanist Party |
| `PIR` | United States Pirate Party | other | ⚓ | PIR, USPP, Pirate, Pirate Party, United States Pirate Party, US Pirate Party |
| `MJN` | Legal Marijuana Now Party | other | 🍃 | LMN, MJN, Legal Marijuana Now, Legal Marijuana Now Party, Marijuana Now Party |
| `GLC` | Grassroots-Legalize Cannabis Party | other | 🌱 | GLC, Grassroots, Grassroots Party, Grassroots-Legalize Cannabis, Grassroots-Legalize Cannabis Party, Legalize Cannabis |
| `USMJ` | U.S. Marijuana Party | other | 🍁 | USMJ, Marijuana Party, U.S. Marijuana Party, US Marijuana Party, United States Marijuana Party |
| `GMPJ` | Green Mountain Peace and Justice Party | other | 🌲 | GMPJ, Green Mountain Peace and Justice, Green Mountain Peace and Justice Party, Green Mountain Party, Liberty Union, Liberty Union Party |
| `OPP` | Oregon Progressive Party | other | 🌳 | OPP, Oregon Progressive, Oregon Progressive Party, Progressive Party of Oregon |
| `WAP` | Washington Progressive Party | other | 🌄 | WAP, WAPP, Washington Progressive, Washington Progressive Party |
| `VPP` | Vermont Progressive Party | other | 🌿 | Progressive, PRO-VT, VPP, Vermont Progressive, Vermont Progressive Party, Progressive Party of Vermont |
| `CPNY` | Conservative Party of New York State | other | 🗽 | CPNY, Conservative, Conservative Party, Conservative Party of New York, Conservative Party of New York State, NY Conservative Party, New York Conservative Party |
| `LPNY` | Liberal Party of New York | other | 🏙️ | LPNY, Liberal, Liberal Party, Liberal Party of New York, NY Liberal Party |
| `WEP` | Women's Equality Party | other | ♀️ | WEP, Women's Equality, Women's Equality Party, Womens Equality Party, Women's Equality Party of New York |
| `RTL` | New York State Right to Life Party | other | 🕯️ | RTL, Right to Life, Right to Life Party, New York State Right to Life Party |
| `CNP` | California National Party | other | 🌉 | CNP, California National, California National Party |
| `UCP` | United Citizens Party | other | 🗳️ | UCP, UC, United Citizens, United Citizens Party |
| `CLP` | Christian Liberty Party | other | ✝️ | CLP, Christian Liberty, Christian Liberty Party |
| `APV` | Approval Voting Party | other | ✅ | APV, Approval Voting, Approval Voting Party |
| `PCP` | Personal Choice Party | other | 🎲 | PCP, Personal Choice, Personal Choice Party |
| `ECO` | Ecology Party of Florida | other | 🌴 | ECO, Ecology Party, Ecology Party of Florida, Florida Ecology Party |
| `LAB` | Labor Party | other | ⚙️ | LAB, Labor, Labor Party, Labor Party USA |
| `CPC` | Citizens Party of the United States | other | 🏘️ | CPC, Citizens Party, Citizens Party of the United States, Citizens Party USA |
| `SCW` | South Carolina Workers Party | other | 🪧 | SCW, SCWP, South Carolina Workers Party, SC Workers Party |
| `TPN` | Tea Party of Nevada | other | 🍵 | TPN, Tea Party of Nevada |
| `NJC` | New Jersey Conservative Party | other | 🦅 | NJC, New Jersey Conservative, New Jersey Conservative Party, NJ Conservative Party |
| `JUS` | Justice Party | other | 🔎 | JUS, Justice, Justice Party |
| `IGVA` | Independent Greens of Virginia | other | 🌻 | IGVA, Independent Greens, Independent Greens of Virginia, Indy Greens |
| `PDN` | Progressive Dane | other | 🧀 | PDN, Progressive Dane, ProDane, Pro Dane |
| `ALO` | Aloha ʻĀina Party | other | 🌺 | ALO, Aloha Aina, Aloha ʻĀina, Aloha Aina Party, Aloha ʻĀina Party |
| `TFA` | Third Party for America | other | 🌐 | TFA, Third Party for America |
| `LPA` | Liberal Party USA | other | 🧾 | LPA, Liberal Party USA, LPUSA |
| `WRI` | Write-in | other | ✍️ | WRI, Write-In, Writein, Write-Ins, Write-In Candidate, Scattering, Scattered Write-In |
| `OTH` | Other | other | ❓ | OTH, Other, Other Party, Other Parties, All Others, Miscellaneous |
