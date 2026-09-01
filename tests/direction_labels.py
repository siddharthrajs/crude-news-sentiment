"""Hand-read crude-direction labels for three days of live headlines (ids 751-1124).

The companion to `tests.labels`, which grades the *relevance* filter. This one
grades the *scorer*: given a headline the filter kept, did we call the direction
crude would actually take?

    +1  bullish -- supply threatened, coercion against a producer, chokepoint
                   disruption, demand added
    -1  bearish -- de-escalation, sanctions relief, supply or a deal that adds
                   barrels, reassurance that a feared disruption did not happen
     0  no crude signal -- real news carrying no direction for crude: war
                   casualty counts, territorial gains, domestic retail fuel
                   policy, unchanged forecasts, settlement levels quoted
                   without a move, non-energy business news

The 0 class is the point of this set, not a dumping ground. Roughly a third of
what the filter passes has no directional content, and the failure that matters
most there is not calling it backwards -- it is calling it at all, at confidence
0.9, which is what drags the index around. A scorer that says "no signal" on
these is scoring correctly.

Labels are one analyst's reading of the headline in isolation, which is the same
information the scorer gets. Where a call was genuinely arguable it was labelled
0 rather than forced to a side.
"""

BULLISH, BEARISH, NOSIGNAL = 1, -1, 0

#: (headline id, title, true direction for crude)
LABELLED: list[tuple[int, str, int]] = [
    (751, "The US administration warned Israel against carrying out any unilateral strikes against Iran - Al Hadath citing sources", BEARISH),
    (755, "Chevron and other US firms near deal to invest billions in Venezuelan oil fields - WSJ $CVX", BEARISH),
    (774, "US treasury imposes limits on Egyptian bank for doing business with Iran - FT.", BULLISH),
    (793, "Russia's Gasoline output fell to about 70% of domestic consumption at the end of August - Sources", BULLISH),
    (799, "Russia's Rostov region declares a state of emergency over grain buildup caused by disruptions to shipping in the Azov-Black Sea basin - Authorities", NOSIGNAL),
    (810, "Kremlin: This line of Armenian leadership is a big concern for Russia", NOSIGNAL),
    (812, "Kremlin: Putin will take part in SCO summit on August 31st - September 1st, will meet China's Xi, India's Modi, Iran's Pezeshkian among other leaders", NOSIGNAL),
    (813, "Kremlin Aide Ushakov on Ukraine's expectations of talks: I saw the media reports, but can't say anything concrete about this.", NOSIGNAL),
    (816, "Egypt Central Bank: US Iran-related measures limited to Banque Misr's UAE branch and US-dollar correspondent transactions", NOSIGNAL),
    (817, "Iran's Supreme Leader Mojtaba Khamenei to make speech about Govt Week in a few minutes", NOSIGNAL),
    (827, "US would take stake in 17 oil fields in Venezuela - WaPo", BEARISH),
    (832, "Yemeni drones targeted a Saudi-backed mercenary base in Al-Mukha - IRIB cites news sources.", BULLISH),
    (843, "NYMEX WTI crude October futures settle at $83.40 a barrel, down 13 cents, 0.16%.", BEARISH),
    (844, "NYMEX Diesel September futures settle at $4.3567 a gallon.", NOSIGNAL),
    (845, "NYMEX gasoline September futures settle at $3.4899 a gallon.", NOSIGNAL),
    (846, "NYMEX Natural Gas October futures settle at $2.8880/MMBTU.", NOSIGNAL),
    (847, "Iran's President Pezeshkian: Omani official agrees that the Strait of Hormuz should be managed based on the Islamabad agreement.", BEARISH),
    (849, "Brent Crude futures settle at $89.31/bbl, down 39 cents, 0.43%.", BEARISH),
    (850, "Iran's President Pezeshkian: Iran to increase gasoline prices - ISNA", NOSIGNAL),
    (851, "Iran's President Pezeshkian: The war has worsened Iran's fuel shortage - ISNA", NOSIGNAL),
    (852, "Iran's President Pezeshkian: The route through the Strait of Hormuz agreed internally.", BEARISH),
    (853, "Iran's President Pezeshkian: Iran is ready for cooperation and understanding with regional countries, including Saudi Arabia and the UAE.", BEARISH),
    (854, "Iran's President Pezeshkian: Iran to open route if four commitments are met.", BEARISH),
    (855, "Iran's President Pezeshkian: Commitments include fuel, petrochem sanctions relief, release of funds, and resumption of investment.", BEARISH),
    (858, "Iran's President Pezeshkian: Iran's exports and imports have decreased by up to 35% because of US sanctions and blockade.", BULLISH),
    (860, "Iran President Pezeshkian: Negotiations should not be viewed in black and white.", NOSIGNAL),
    (861, "Iran's President Pezeshkian: Iran sold about 90 mln barrels of oil during the implementation of interim deal.", NOSIGNAL),
    (865, "Pentagon is in talks with a Venezuelan mogul for a massive oil deal", BEARISH),
    (882, "IRGC: Strait of Hormuz is closed to all ships that intend to transit without coordination with Iran.", BULLISH),
    (883, "IRGC Navy: The US claims that the Strait of Hormuz is open is an obvious lie", BULLISH),
    (887, "Trump: United States has just reached an accord with Venezuela", BEARISH),
    (888, "IRGC navy: full control over Strait of Hormuz, waterway closed to ships transiting", BULLISH),
    (889, "Iran says mining continues without coordination with Tehran: statement", BULLISH),
    (890, "Trump: secured majority u.s. control of over 65 bln barrels of proven oil reserves in Venezuela", BEARISH),
    (891, "Trump: United States has just reached an oil pact with Venezuela", BEARISH),
    (893, "Colombia's oil output dropped 2.46% year-over-year in July to 727,920 barrels per day: national hydrocarbon agency", BULLISH),
    (895, "U.S. natural gas output to average 122.6 bcf/day in August vs 122 bcf/day in July: EIA", NOSIGNAL),
    (896, "U.S. oil output to average 13.83 million bpd in August vs 13.82 million bpd in July; set to average 13.77 million bpd in September - EIA", NOSIGNAL),
    (897, "Venezuela interim president Rodriguez welcomes oil pact with U.S.: will bring $209 billion to treasury", BEARISH),
    (900, "Iranian government pledges to resist U.S. sanctions: diplomacy and defense are 'complementary and inseparable' - statement", BULLISH),
    (901, "Three killed in Ukrainian strike in Russia's Belgorod region, authorities say", NOSIGNAL),
    (902, "Kyiv region governor: 27 killed in overnight Russian attack on Bucha district", NOSIGNAL),
    (906, "Ukraine's Zelenskiy vows punishment for those behind secondary blast triggered by Russian attack on Kyiv region warehouse", NOSIGNAL),
    (907, "Banque Misr: Affirms full respect for regulatory and legal frameworks after US sanction - statement", NOSIGNAL),
    (911, "Russia: seized Rubizhne in Ukraine's Donetsk region - TASS", NOSIGNAL),
    (912, "Russia: seized Khrapivshchyna in Ukraine's Sumy region - TASS", NOSIGNAL),
    (913, "Russia: seized Novoandriivka in Ukraine's Donetsk region - TASS", NOSIGNAL),
    (915, "Death toll in Russian strike on Kyiv-area warehouse rises to 37: governor", NOSIGNAL),
    (920, "Iraq's Kurdistan oil exports to Turkey's Ceyhan port at about 150,000 bpd: oil ministry spokesperson", NOSIGNAL),
    (924, "Venezuelan interim president Delcy Rodriguez: energy deal with U.S. will last 25 years", BEARISH),
    (925, "Venezuela's interim president: deal with U.S. aims to boost output to 1.5 million barrels a day", BEARISH),
    (926, "Venezuela's interim president: Venezuela maintains control over its resources", NOSIGNAL),
    (928, "Iran's Supreme Leader Mojtaba Khamenei in written message urges Gulf rulers to identify their 'real enemy,' grasp its plans and confront it: statement", NOSIGNAL),
    (929, "Iran's Khamenei in note urges unity, joint defense against 'enemies' and Muslim cooperation - statement", NOSIGNAL),
    (933, "Russian defence ministry: Russian forces start preparations for large-scale strikes on Ukraine energy infrastructure", BULLISH),
    (935, "One killed in Ukrainian strike on Russia-controlled Luhansk region, Russia-appointed official says", NOSIGNAL),
    (936, "Ukraine's defence minister: Kyiv tests four possible interceptors for jet-powered drones", NOSIGNAL),
    (940, "Trump on Venezuela: plans to replenish strategic national reserves with Venezuelan oil", BULLISH),
    (941, "Trump on Venezuela: 'topping out' process to start very soon", NOSIGNAL),
    (950, "Ukraine preparing for more talks with US president representatives: Zelenskiy", NOSIGNAL),
    (951, "Ukmto: tanker hit by projectile 12 nautical miles north of Oman's Khasab", BULLISH),
    (954, "Turkey, Saudi Arabia foreign and defense ministers and military chiefs to attend Makkah Accord Committee meeting: Pakistan foreign ministry", NOSIGNAL),
    (957, "Earlier Sunday, U.S. forces hit two Iranian launchers on Larak Island: Axios, citing U.S. official", BULLISH),
    (958, "Explosion heard near Iran's Larak island: cause unknown - Iran's Fars", BULLISH),
    (959, "U.S. official: Earlier Sunday, U.S. forces hit two Iranian launchers on Larak Island", BULLISH),
    (960, "U.S. official: Revolutionary Guard Corps forces seen readying to launch rockets with sea mines into Strait of Hormuz", BULLISH),
    (961, "Iran's Revolutionary Guards: U.S. strike on Larak Island will be met with response and punishment - state media", BULLISH),
    (962, "Iran's Revolutionary Guards: several soldiers and civilians killed and wounded in assault - state media", NOSIGNAL),
    (963, "Iran's Revolutionary Guards spokesman: U.S. to face economic and military repercussions for attack on Larak Island - Fars", BULLISH),
    (964, "Iran is targeting U.S. forces in Jordan: Fox reporter on X, citing U.S. source", BULLISH),
    (972, "Commodity ships passing through Strait of Hormuz fall to 5 daily over weekend - data", BULLISH),
    (973, "Iran's IRGC: launched ballistic missile strikes on two US bases in Jordan in retaliation for US attack on Larak Island - Iranian media", BULLISH),
    (985, "US Treasury Secretary Bessent: banks told not to hold Iranian funds or support Iranian regime", BULLISH),
    (986, "U.S. Treasury to impose new Iran secondary sanctions weekly, beginning with banks: Bessent", BULLISH),
    (987, "US Treasury Secretary Bessent: next time 'we'll likely just sanction a bank outright' after U.S. limits one Egyptian bank's UAE branches", BULLISH),
    (988, "Bessent: Iran pressure campaign won't succeed unless Chinese firms face secondary sanctions", BULLISH),
    (989, "US Treasury Secretary Bessent: U.S. blockade of Iran ports has limited Chinese imports of Iranian oil", BULLISH),
    (1000, "U.S. military: U.S. forces carried out limited, precise strike against IRGC mine-laying units posing imminent threat in Strait of Hormuz", BULLISH),
    (1024, "Iran's Revolutionary Guards: They shot down U.S. MQ-9 drone over Strait of Hormuz - Mehr News Agency", BULLISH),
    (1025, "Iran's army: drone strike targeted UAE's Al Minhad air base early Monday - state TV", BULLISH),
    (1027, "Iran: armed forces hit U.S. military bases in Jordan in retaliation for U.S. strike on Larak island - foreign ministry statement", BULLISH),
    (1028, "Iran: U.S. bases in Jordan used to launch and back attack on Larak Island - foreign ministry", BULLISH),
    (1029, "Iran: will respond decisively to any further hostile military aggression - foreign ministry", BULLISH),
    (1030, "Iran: U.S. and allies backing its military moves hold full responsibility for escalation consequences - foreign ministry", BULLISH),
    (1031, "Iran's Revolutionary Guards: Supertanker caught fire, stopped after hitting two naval mines in Strait of Hormuz - state TV", BULLISH),
    (1032, "Iran's Revolutionary Guards: tanker tried to cross southern Strait of Hormuz illegally", BULLISH),
    (1033, "Iran's Revolutionary Guards: vessels must follow its rules for transit through Strait of Hormuz", BULLISH),
    (1056, "UAE: We dealt with drone over its waters coming from Iran.", BULLISH),
    (1057, "Ceo of Iran's National Oil Company: Oil operations have not stopped at Kharg Island - Nour News", BEARISH),
    (1071, "UAE's Foreign Ministry: We reserve full right to respond after Iranian attack.", BULLISH),
    (1072, "UAE condemns Iranian drone attack, says air defences intercepted drone over territorial waters - Foreign Ministry", BULLISH),
    (1073, "India PM Modi meets Iran President Pezeshkian on sidelines of SCO summit in Bishkek - ANI", NOSIGNAL),
    (1075, "EU: We will continue to work closely with the united States and other G7 and international partners to maintain pressure on Iran", BULLISH),
    (1078, "Iraq oil exports in August reached 2.369m bpd - Government Spokesman", NOSIGNAL),
    (1079, "Iran's President Pezeshkian tells India's Prime Minister Tehran still seeks a negotiated solution out of conflict with the US - Tasnim", BEARISH),
    (1080, "Iran's President Pezeshkian: war is in no one's interest - Tasnim.", BEARISH),
    (1081, "Brent crude oil expected to average $85.08 per barrel in 2026 versus $85.22 forecast in July - Poll.", NOSIGNAL),
    (1082, "US crude oil expected to average $80.20 per barrel in 2026 versus $80.14 forecast in July - Poll.", NOSIGNAL),
    (1083, "AMD, Cisco and Humain expand Saudi Arabia's AI infrastructure.", NOSIGNAL),
    (1092, "US Interior Sec. Burgum: Discussions on gasoline prices tomorrow at the White House - Fox Business", NOSIGNAL),
    (1093, "US Interior Sec. Burgum: Want to support refiners, not run them out of business.", NOSIGNAL),
    (1095, "Trump: The US will respond to Iran's attack on US forces. - Fox News", BULLISH),
    (1104, "US CENTCOM: No ships have hit mines in the Strait of Hormuz.", BEARISH),
    (1107, "US Treasury Secretary Bessent: Iran is taking sanctions seriously.", BULLISH),
    (1108, "US Treasury Secretary Bessent on Iran: They are lashing out kinetically because they are losing economically.", BULLISH),
    (1109, "US Treasury Secretary Bessent on Iran: We're going to continue exerting pressure.", BULLISH),
    (1110, "US Treasury Secretary Bessent on Iran: Iran's economy doesn't have to collapse, the regime just needs to come to its senses.", NOSIGNAL),
    (1111, "OpenAI: Starting later today, advertisers can purchase ChatGPT ads directly via Ads Manager across India, Europe, the Middle East, and North Africa.", NOSIGNAL),
    (1112, "US Treasury Secretary Bessent: We will get to the other side of the Iran conflict - CNBC.", NOSIGNAL),
    (1118, "US Treasury Secretary Bessent: We thank the EU for strong support of actions against Iran.", BULLISH),
    (1122, "Senior Iranian Source: Iran's retaliation against US strikes on Larak showed that no target in the region is beyond Tehran's reach.", BULLISH),
    (1123, "Senior Iranian Source: Conditions in the Strait of Hormuz will worsen for vessels that violate Tehran's rules.", BULLISH),
    (1124, "Senior Iranian Source: Recent hostilities remain a limited and contained confrontation between Iran and Washington.", BEARISH),
]
