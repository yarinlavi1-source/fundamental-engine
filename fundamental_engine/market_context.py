"""Market context: country risk, evidenced geopolitical / regulatory exposures, and the market regime (inputs.market_context).

Why: a company's value depends on where it sells and produces and on events that can cut a part of its business off, and the
owner wants the general market state beside every answer. Three parts, each with its own rule:

1. Country risk (Damodaran's method). A packaged, dated table of country risk premiums (brain/country_risk.json: Damodaran's
   ctryprem page, equity CRP = rating-based default spread x relative equity market volatility, with his GDP-weighted
   regional averages) is weighted by the company's exposure: revenue by geography from its own annual report
   (business_map.tables.geography, read by business.py) or an evidenced inputs.market_context.geography override, and, as a
   second channel, where it produces (business_map.assets_by_geography, long-lived assets, or an evidenced
   inputs.market_context.operations override; a foundry's fabs). The engine's ERP is the US implied premium, which already
   carries the US's own CRP (Aa1 since May 2025), so only the part ABOVE the US base is added:
       add-on(channel) = sum_i share_i x max(0, CRP_i - CRP_US)          (Damodaran's operations-weighted CRP)
       add-on = max(add-on(revenue), add-on(operations))                  (one sovereign risk reaches the shares through either
                                                                           channel; adding both would count it twice)
   A country less risky than the US earns no credit (the add-ons only add; conservative). The add-on is its own row of the
   cost-of-equity table (forecast._cost_of_equity, factor 'country_risk'), kept in the terminal rate (structural, not in
   TERMINAL_KE_DROPPED) and in the decision's required return.
   Market-neutral netting: the ERP (the S&P 500's implied premium) and the decision's index return (the cap-weighted implied
   return of the largest US companies) are prices of companies that already sell abroad, so both already carry the market's
   average country exposure. A second row nets it out (factor 'country_risk_market_neutral' = -COUNTRY_RISK_MARKET_RATE x the
   share of the channel mapped to a country or region): on the sweep it was measured on, the value-weighted net is zero (the
   invariant of forecast.MARKET_NEUTRAL_ADJUSTMENT: the average company is discounted at risk-free + ERP); a domestic company
   ends below it, a more exposed one above it, and an unmapped share counts as average (neither added nor netted). The
   decision adds the same net to the index return.
   No exposure data -> no add-on and an open question; never a default exposure. An aggregate member ('Rest of world',
   'International', 'Non US', 'Americas') maps to no country: its share carries no add-on and is listed as an open question.
   A table dated after as_of is not used (point in time).
2. Exposure mechanisms. inputs.market_context.exposures = [{kind, share_of_revenue | share_of_supply, evidence_ids, note,
   shock?, cost_rate?}]: each becomes a named failure scenario (risk.failure_scenarios, 'exposure:<kind>') that re-runs the
   valuation with the shock applied to the bear row (the mechanism's stress value) and to the base row (what the event alone
   costs the base case), and a register entry in the counter-thesis. The shock is sized ONLY from the evidenced share (and an
   evidenced cost rate for a margin shock): no default severity, no probability. Revenue shock: the exposed share of revenue
   is lost from the first full fiscal year and does not come back (later years keep the row's growth on the smaller base;
   costs flex with revenue: no operating deleverage is assumed). Margin shock: the exposed revenue bears the evidenced cost
   rate (share_of_revenue x rate of revenue), or the exposed share of cash costs rises by it (cash cost ratio x share x rate),
   in every year and in the terminal margin. The executed bear/base/bull values are unchanged.
3. Market regime (display and context only: no value, rate or verdict reads it). Dated FRED readings (10-year Treasury
   DGS10, Baa credit spread BAA10Y, VIX VIXCLS, 10y-2y slope T10Y2Y; scripts/collect_market_context.py saves them with
   retrieval time and URL under runs/macro/), the engine's dated implied ERP and the engine market return (calibration
   index input) -> a small classifier with the documented bands below -> one short Hebrew line for the table and paragraph.

No input, no change: without inputs.market_context nothing here runs and every existing output is byte-identical. Readings
added by the CLI's --macro (with_regime) switch the stage on for the regime alone (country_risk: false), so they move no
value; --country-risk (with_country_risk) or a company's own inputs.market_context (even {}) switches the country-risk row on.
"""
from copy import deepcopy
from datetime import date, timedelta
import csv
import json
import math
import os
import re
import unicodedata

COUNTRY_RISK_FILE = os.path.join(os.path.dirname(__file__), 'brain', 'country_risk.json')
KNOWN_KEYS = ('geography', 'operations', 'exposures', 'regime', 'country_risk')   # country_risk: false skips part 1
CHANNELS = ('revenue', 'operations')
# overlap guard: a disclosed revenue split whose parts exceed the total by more than this has overlapping members. Most such
# filings tag two complete splits on one axis (regions Americas / EMEA / Asia Pacific AND countries US / GB / Other countries:
# 183 annual reports of the 2026-10-21 universe, about a quarter of those with a geography table). The engine then looks for
# the subsets of members that add up to the total within PARTITION_TOLERANCE and in which no region or aggregate sits next to
# a country or region it covers (REGION_COVERS: 'EMEA' with 'GB', 'Americas' with 'US'), and uses the one with the LARGEST
# add-on: a member that names no country adds nothing, which pulls a split down, but a region member read at its regional
# average ('EMEA' with Africa, 'Latin America' with Argentina and Venezuela: REGION_NOTES) can pull it up, so the largest is
# not a lower bound and can overstate (PPG's 1.30% on 2026-10-21 comes mostly from such averages). The market-average netting
# row is measured with this same rule, so the rule's average lean cancels in the net; a company-specific lean does not:
# supply an evidenced country split when the regions dominate. Ties -> the smaller unmapped share, the larger share named by
# country, fewer members, then by name (deterministic).
# No such subset -> no add-on and an open question. Searched only up to PARTITION_MAX_MEMBERS members (2^n subsets).
OVERLAP_TOLERANCE = .01
# The market's own country exposure, per unit of mapped exposure: sum(w x add-on) / sum(w x mapped share) over the large-cap
# sweep companies with a country-risk row, w = market value (scripts/measure_country_risk_market.py on
# runs/universe_2026-10-21, queue large500, this table): 0.42pp with the listing feed's market values (125 of 490 companies
# have a readable split, 26% of the sweep's value; 22 complete, 103 partial), 0.49pp with the universe rows' values (which
# count Alphabet at 2.3x its listing value), 0.31pp without Alphabet (half of the weighted add-on). Rounded to 0.40pp. Measured
# with this method, regional averages included, so a method-wide lean (e.g. 'EMEA' or 'Latin America' read at averages that
# include Africa or Argentina) cancels in the net row instead of adding to every company. Re-measure after a table update.
COUNTRY_RISK_MARKET_RATE = .004
PARTITION_TOLERANCE = .015
PARTITION_MAX_MEMBERS = 16

# Filing member names (business._human of the XBRL member, upper-cased, punctuation removed) and analyst keys that name a
# country or a Damodaran region. Anything not here, not a table country name and not an ISO code maps to nothing.
COUNTRY_ALIASES = {
    'UNITED STATES OF AMERICA': 'United States', 'USA': 'United States', 'U S': 'United States', 'U S A': 'United States',
    'UNITED KINGDOM OF GREAT BRITAIN AND NORTHERN IRELAND': 'United Kingdom', 'UK': 'United Kingdom', 'GREAT BRITAIN': 'United Kingdom',
    'BRITAIN': 'United Kingdom', 'ENGLAND': 'United Kingdom', 'SOUTH KOREA': 'Korea', 'REPUBLIC OF KOREA': 'Korea',
    'KOREA REPUBLIC OF': 'Korea', 'MAINLAND CHINA': 'China', 'PRC': 'China', 'PEOPLES REPUBLIC OF CHINA': 'China',
    'CHINA INCLUDING HONG KONG': 'China', 'GREATER CHINA': 'China', 'TURKIYE': 'Turkey', 'HOLLAND': 'Netherlands',
    'THE NETHERLANDS': 'Netherlands', 'UAE': 'United Arab Emirates', 'VIET NAM': 'Vietnam', 'CZECHIA': 'Czech Republic',
    'IVORY COAST': "Côte d'Ivoire", 'NORTH MACEDONIA': 'Macedonia', 'ESWATINI': 'Swaziland', 'MACAU': 'Macao',
    'RUSSIAN FEDERATION': 'Russia', 'REPUBLIC OF IRELAND': 'Ireland', 'SWISS CONFEDERATION': 'Switzerland'}
# 'Greater China' (Apple's segment: mainland, Hong Kong, Taiwan) is read as China, the largest and riskiest of the three
ALIAS_NOTES = {'GREATER CHINA': 'Greater China read as China (mainland, Hong Kong and Taiwan together; China has the highest CRP of the three)'}
REGION_ALIASES = {
    'EUROPE': 'Europe', 'WESTERN EUROPE': 'Western Europe', 'EASTERN EUROPE': 'Eastern Europe', 'EMEA': 'EMEA',
    'EUROPE MIDDLE EAST AND AFRICA': 'EMEA', 'EUROPE THE MIDDLE EAST AND AFRICA': 'EMEA', 'EUROPE MIDDLE EAST AFRICA': 'EMEA',
    'ASIA': 'Asia', 'ASIA PACIFIC': 'Asia', 'APAC': 'Asia', 'ASIA PAC': 'Asia', 'MIDDLE EAST': 'Middle East', 'AFRICA': 'Africa',
    'MIDDLE EAST AND AFRICA': 'Africa & Mid East', 'MEA': 'Africa & Mid East', 'LATIN AMERICA': 'Latin America & Caribbean',
    'LATAM': 'Latin America & Caribbean', 'SOUTH AMERICA': 'Central and South America', 'CENTRAL AND SOUTH AMERICA': 'Central and South America',
    'NORTH AMERICA': 'North America', 'UNITED STATES AND CANADA': 'North America', 'US AND CANADA': 'North America',
    'AUSTRALIA AND NEW ZEALAND': 'Australia & New Zealand', 'ANZ': 'Australia & New Zealand',
    'MIDDLE EASTAND AFRICA': 'Africa & Mid East',
    # a region less one or two of its countries keeps the region's average (approximation, noted)
    'REST OF EUROPE': 'Europe', 'OTHER EUROPE': 'Europe', 'EUROPE EXCLUDING GERMANY': 'Europe', 'EUROPE EXCLUDING NETHERLANDS': 'Europe',
    'EUROPE EXCLUDING GERMANY AND SWITZERLAND': 'Europe', 'REST OF ASIA': 'Asia', 'OTHER ASIA': 'Asia', 'OTHER ASIA PACIFIC': 'Asia',
    'ASIA PACIFIC EXCLUDING CHINA': 'Asia', 'ASIA PACIFIC EXCLUDING GREATER CHINA': 'Asia', 'ASIA PACIFIC AND JAPAN': 'Asia',
    'ASIA PACIFICAND JAPAN': 'Asia', 'APJ': 'Asia', 'SOUTHEAST ASIA': 'Asia', 'SOUTH EAST ASIA': 'Asia',
    'OCEANIA': 'Australia & New Zealand', 'CARIBBEAN': 'Caribbean', 'EMERGING MARKETS': 'Emerging Markets'}
REGION_NOTES = {'Asia': 'read as Damodaran\'s GDP-weighted Asia (Australia and New Zealand are a separate region; a sub-region keeps '
                        'the whole region\'s average)',
                'Europe': 'read as Damodaran\'s GDP-weighted Europe (a sub-region keeps the whole region\'s average)',
                'North America': 'read as Damodaran\'s GDP-weighted North America (US, Canada; below the US base: no add-on)',
                # regional averages lean high for a typical US company's sales mix: these two carry their riskiest members
                'EMEA': "read as Damodaran's GDP-weighted Europe + Middle East + Africa (1.68%, Western Europe alone 1.04%): Africa "
                        '(7.72%) and the Middle East (2.01%) are in it, so it can overstate a mainly European exposure',
                'Latin America & Caribbean': "read as Damodaran's GDP-weighted Latin America & Caribbean (4.40%; Mexico 2.46%, "
                                             'Brazil 3.24%): Argentina (9.71%) and Venezuela (26.66%) are in it, so it can overstate '
                                             'an exposure concentrated in Mexico, Brazil or Chile'}
# which of Damodaran's country 'region' labels each table region covers: a country listed next to a region that covers it
# is an overlap (used for asset tables, whose shares are normalized and cannot reveal overlap by their sum)
EUROPE_LABELS = {'Western Europe', 'Eastern Europe & Russia'}
REGION_COVERS = {'Western Europe': {'Western Europe'}, 'Europe': EUROPE_LABELS, 'Eastern Europe': {'Eastern Europe & Russia'},
                 'Asia': {'Asia'}, 'Small Asia (No India, China & Japan)': {'Asia'}, 'Middle East': {'Middle East'}, 'Africa': {'Africa'},
                 'Africa & Mid East': {'Africa', 'Middle East'}, 'EMEA': EUROPE_LABELS | {'Middle East', 'Africa'},
                 'North America': {'North America'}, 'Australia & New Zealand': {'Australia & New Zealand'},
                 'Australia, NZ & Canada': {'Australia & New Zealand', 'North America'}, 'Caribbean': {'Caribbean'},
                 'Central and South America': {'Central and South America'},
                 'Latin America & Caribbean': {'Central and South America', 'Caribbean'}}     # Emerging Markets, Global: everything
ALL_LABELS = frozenset().union(*REGION_COVERS.values())
AMERICAS_LABELS = frozenset({'North America', 'Central and South America', 'Caribbean'})
NON_US_WORDS = ('NON US', 'NON UNITED STATES', 'INTERNATIONAL', 'OTHER INTERNATIONAL', 'FOREIGN', 'FOREIGN COUNTRIES',
                'OUTSIDE US', 'OUTSIDE UNITED STATES')
HOME_WORDS = ('COUNTRY OF DOMICILE', 'DOMESTIC', 'HOME COUNTRY')
# aggregates that name no country: their share carries no add-on and is an open question (supply an evidenced split)
AGGREGATE_WORDS = ('AMERICAS', 'REST OF WORLD', 'REST OF THE WORLD', 'ROW', 'OTHER', 'OTHERS', 'OTHER COUNTRIES', 'OTHER FOREIGN',
                   'OTHER FOREIGN COUNTRIES', 'INTERNATIONAL', 'OTHER INTERNATIONAL', 'NON US', 'NON UNITED STATES', 'FOREIGN',
                   'FOREIGN COUNTRIES', 'ALL OTHER', 'ALL OTHER COUNTRIES', 'OTHER REGIONS', 'OUTSIDE UNITED STATES', 'OUTSIDE US',
                   'RESTOFTHEWORLD', 'RESTOF WORLD', 'OTHERFOREIGNLOCATIONS', 'OTHER AMERICAS', 'OTHER COUNTRY', 'OTHER AREAS',
                   'OTHER GEOGRAPHICAL AREAS', 'OTHER GEOGRAPHICAL REGIONS', 'OTHER GEOGRAPHIC AREAS', 'ALL OTHER GEOGRAPHIC AREAS',
                   'AMERICAS EXCLUDING UNITED STATES', 'NORTH AMERICA EXCLUDING UNITED STATES', 'NON NORTH AMERICA',
                   'TOTAL GEOGRAPHICAL AREA', 'TOTAL')
# US state codes that are not also a country code in the table: a split with any of them is by state, so every two-letter
# member is read as a US state (stpr:CA is California, not Canada; business._human drops the namespace)
US_STATE_ONLY = frozenset('AK CT DC FL HI IA KS MI NC ND NH NJ NM NV NY OH OK OR RI SC TX UT VT WA WI WV WY'.split())

# ---- exposure mechanisms (part 2): the shock each kind applies by default, the share fields it accepts, words for the owner
EXPOSURE_KINDS = {
    'taiwan_supply': {'shock': 'revenue', 'shares': ('share_of_supply', 'share_of_revenue'), 'he': 'שיבוש באספקה מטייוואן',
                      'mechanism': 'Supply from Taiwan stops (blockade, conflict, a fab outage): the output that depends on it cannot be '
                                   'shipped and is lost.'},
    'china_export_controls': {'shock': 'revenue', 'shares': ('share_of_revenue',), 'he': 'מגבלות יצוא לסין',
                              'mechanism': 'Export controls close the Chinese market to the products concerned: the revenue sold there is lost.'},
    'tariffs': {'shock': 'margin', 'shares': ('share_of_revenue', 'share_of_supply'), 'he': 'מכסים',
                'mechanism': 'Tariffs raise the cost of the exposed sales or supplies and the company absorbs them (not passed on).'},
    'sanctions': {'shock': 'revenue', 'shares': ('share_of_revenue',), 'he': 'סנקציות',
                  'mechanism': 'Sanctions forbid sales to the exposed customers or countries: that revenue is lost.'},
    'regulatory_ban': {'shock': 'revenue', 'shares': ('share_of_revenue',), 'he': 'איסור רגולטורי',
                       'mechanism': 'A regulator bans the exposed product or market: that revenue is lost.'},
    'conflict_disruption': {'shock': 'revenue', 'shares': ('share_of_revenue', 'share_of_supply'), 'he': 'שיבוש בגלל מלחמה או סכסוך',
                            'mechanism': 'War or conflict cuts off the exposed market or supply: the revenue that depends on it is lost.'},
}
SHOCKS = ('revenue', 'margin')
EXPOSURE_KEYS = ('kind', 'share_of_revenue', 'share_of_supply', 'evidence_ids', 'note', 'shock', 'cost_rate')
GROWTH_FLOOR = -.9          # drivers.build_case's lower bound on a growth entry: a larger loss is clamped and flagged
TERMINAL_MARGIN_FLOOR = .01  # the floor risk.variant_spec uses for a stressed terminal margin

# ---- market regime (part 3): FRED series and descriptive bands
FRED_URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id={}'
REGIME_SERIES = {
    'DGS10': 'Market yield on US Treasury securities at 10-year constant maturity, percent, daily (FRED DGS10)',
    'BAA10Y': "Moody's seasoned Baa corporate bond yield relative to the 10-year Treasury, percent, daily (FRED BAA10Y)",
    'VIXCLS': 'CBOE Volatility Index (VIX) close, daily (FRED VIXCLS)',
    'T10Y2Y': '10-year minus 2-year Treasury constant maturity, percent, daily (FRED T10Y2Y)'}
# Bands = percentiles of each series' full daily FRED history, computed 2026-10-25 from fredgraph.csv: BAA10Y 1986-2026 p25
# 1.74, median 2.13, p75 2.66, p90 3.11; VIXCLS 1990-2026 p25 14.0, median 17.6, p75 22.7, p90 28.5 (30 is also the usual
# 'fear' line); T10Y2Y 1976-2026 p25 0.19, median 0.77, p75 1.47; and Damodaran's implied ERP at the end of each year 1961-2025
# (histimpl.html, 'Implied ERP (FCFE)' column, 65 years): p25 3.48%, median 4.10%, p75 5.08%, 1999 low 2.05%, 1979 high 6.45%.
# Percentiles by linear interpolation; band edges are the rounded percentiles (ERP p75 5.08% -> 5.1%).
# Descriptive context, not forecasts and not timing signals; they move no value.
CREDIT_BANDS = ((1.75, 'tight'), (2.65, 'normal'), (3.10, 'wide'), (math.inf, 'stress'))      # BAA10Y, percent
VOL_BANDS = ((14., 'calm'), (23., 'normal'), (30., 'elevated'), (math.inf, 'high'))          # VIX points
CURVE_BANDS = ((0., 'inverted'), (.25, 'flat'), (1.5, 'normal'), (math.inf, 'steep'))        # T10Y2Y, percent
ERP_BANDS = ((.035, 'rich'), (.051, 'near_history'), (math.inf, 'cheap'))                    # implied ERP, fraction
HE = {'tight': 'צרים', 'normal': 'רגילים', 'wide': 'רחבים', 'stress': 'לחץ אשראי',
      'calm': 'רגועה', 'elevated': 'מוגברת', 'high': 'גבוהה',
      'inverted': 'הפוך', 'flat': 'שטוח', 'steep': 'תלול',
      'rich': 'נמוכה: השוק יקר ביחס להיסטוריה', 'near_history': 'קרובה לממוצע ההיסטורי', 'cheap': 'גבוהה: השוק זול ביחס להיסטוריה'}
VOL_HE = {'calm': 'רגועה', 'normal': 'רגילה', 'elevated': 'מוגברת', 'high': 'גבוהה'}
CURVE_HE = {'inverted': 'הפוך', 'flat': 'שטוח', 'normal': 'רגיל', 'steep': 'תלול'}
# Hebrew names of every table country and region (render and analyst sheet text); a name missing here is written in English
# after a maqaf ('ב־Name'). HE_ARTICLE: names that start with the definite article, which a prefix replaces ('במזרח התיכון').
COUNTRY_HE = {
    'Abu Dhabi': 'אבו דאבי', 'Albania': 'אלבניה', 'Algeria': "אלג'יריה", 'Andorra (Principality of)': 'אנדורה', 'Angola': 'אנגולה',
    'Argentina': 'ארגנטינה', 'Armenia': 'ארמניה', 'Aruba': 'ארובה', 'Australia': 'אוסטרליה', 'Austria': 'אוסטריה',
    'Azerbaijan': "אזרבייג'ן", 'Bahamas': 'איי בהאמה', 'Bahrain': 'בחריין', 'Bangladesh': 'בנגלדש', 'Barbados': 'ברבדוס',
    'Belarus': 'בלארוס', 'Belgium': 'בלגיה', 'Belize': 'בליז', 'Benin': 'בנין', 'Bermuda': 'ברמודה', 'Bolivia': 'בוליביה',
    'Bosnia and Herzegovina': 'בוסניה והרצגובינה', 'Botswana': 'בוטסואנה', 'Brazil': 'ברזיל', 'Brunei': 'ברוניי', 'Bulgaria': 'בולגריה',
    'Burkina Faso': 'בורקינה פאסו', 'Cambodia': 'קמבודיה', 'Cameroon': 'קמרון', 'Canada': 'קנדה', 'Cape Verde': 'כף ורדה',
    'Cayman Islands': 'איי קיימן', 'Chile': "צ'ילה", 'China': 'סין', 'Colombia': 'קולומביה',
    'Congo (Democratic Republic of)': 'הרפובליקה הדמוקרטית של קונגו', 'Congo (Republic of)': 'הרפובליקה של קונגו', 'Cook Islands': 'איי קוק',
    'Costa Rica': 'קוסטה ריקה', 'Croatia': 'קרואטיה', 'Cuba': 'קובה', 'Curacao': 'קוראסאו', 'Cyprus': 'קפריסין', 'Czech Republic': "צ'כיה",
    "Côte d'Ivoire": 'חוף השנהב', 'Denmark': 'דנמרק', 'Dominican Republic': 'הרפובליקה הדומיניקנית', 'Ecuador': 'אקוודור', 'Egypt': 'מצרים',
    'El Salvador': 'אל סלוודור', 'Estonia': 'אסטוניה', 'Ethiopia': 'אתיופיה', 'Fiji': "פיג'י", 'Finland': 'פינלנד', 'France': 'צרפת',
    'Gabon': 'גבון', 'Gambia': 'גמביה', 'Georgia': 'גאורגיה', 'Germany': 'גרמניה', 'Ghana': 'גאנה', 'Greece': 'יוון',
    'Guatemala': 'גואטמלה', 'Guernsey (States of)': 'גרנזי', 'Guinea': 'גינאה', 'Guinea-Bissau': 'גינאה ביסאו', 'Guyana': 'גיאנה',
    'Haiti': 'האיטי', 'Honduras': 'הונדורס', 'Hong Kong': 'הונג קונג', 'Hungary': 'הונגריה', 'Iceland': 'איסלנד', 'India': 'הודו',
    'Indonesia': 'אינדונזיה', 'Iran': 'איראן', 'Iraq': 'עיראק', 'Ireland': 'אירלנד', 'Isle of Man': 'האי מאן', 'Israel': 'ישראל',
    'Italy': 'איטליה', 'Jamaica': "ג'מייקה", 'Japan': 'יפן', 'Jersey (States of)': "ג'רזי", 'Jordan': 'ירדן', 'Kazakhstan': 'קזחסטן',
    'Kenya': 'קניה', 'Korea': 'דרום קוריאה', 'Korea, D.P.R.': 'צפון קוריאה', 'Kuwait': 'כווית', 'Kyrgyzstan': 'קירגיזסטן',
    'Laos': 'לאוס', 'Latvia': 'לטביה', 'Lebanon': 'לבנון', 'Liberia': 'ליבריה', 'Libya': 'לוב', 'Liechtenstein': 'ליכטנשטיין',
    'Lithuania': 'ליטא', 'Luxembourg': 'לוקסמבורג', 'Macao': 'מקאו', 'Macedonia': 'מקדוניה הצפונית', 'Madagascar': 'מדגסקר',
    'Malawi': 'מלאווי', 'Malaysia': 'מלזיה', 'Maldives': 'האיים המלדיביים', 'Mali': 'מאלי', 'Malta': 'מלטה', 'Mauritius': 'מאוריציוס',
    'Mexico': 'מקסיקו', 'Moldova': 'מולדובה', 'Mongolia': 'מונגוליה', 'Montenegro': 'מונטנגרו', 'Montserrat': 'מונטסראט',
    'Morocco': 'מרוקו', 'Mozambique': 'מוזמביק', 'Myanmar': 'מיאנמר', 'Namibia': 'נמיביה', 'Nepal': 'נפאל', 'Netherlands': 'הולנד',
    'New Zealand': 'ניו זילנד', 'Nicaragua': 'ניקרגואה', 'Niger': "ניז'ר", 'Nigeria': 'ניגריה', 'Norway': 'נורווגיה', 'Oman': 'עומאן',
    'Pakistan': 'פקיסטן', 'Panama': 'פנמה', 'Papua New Guinea': 'פפואה גינאה החדשה', 'Paraguay': 'פרגוואי', 'Peru': 'פרו',
    'Philippines': 'הפיליפינים', 'Poland': 'פולין', 'Portugal': 'פורטוגל', 'Qatar': 'קטר', 'Ras Al Khaimah (Emirate of)': "ראס אל-ח'ימה",
    'Romania': 'רומניה', 'Russia': 'רוסיה', 'Rwanda': 'רואנדה', 'Saudi Arabia': 'ערב הסעודית', 'Senegal': 'סנגל', 'Serbia': 'סרביה',
    'Sharjah': "שארג'ה", 'Sierra Leone': 'סיירה לאון', 'Singapore': 'סינגפור', 'Slovakia': 'סלובקיה', 'Slovenia': 'סלובניה',
    'Solomon Islands': 'איי שלמה', 'Somalia': 'סומליה', 'South Africa': 'דרום אפריקה', 'Spain': 'ספרד', 'Sri Lanka': 'סרי לנקה',
    'St. Maarten': 'סינט מארטן', 'St. Vincent & the Grenadines': 'סנט וינסנט והגרנדינים', 'Sudan': 'סודן', 'Suriname': 'סורינאם',
    'Swaziland': 'אסוואטיני', 'Sweden': 'שוודיה', 'Switzerland': 'שווייץ', 'Syria': 'סוריה', 'Taiwan': 'טייוואן',
    'Tajikistan': "טג'יקיסטן", 'Tanzania': 'טנזניה', 'Thailand': 'תאילנד', 'Togo': 'טוגו', 'Trinidad and Tobago': 'טרינידד וטובגו',
    'Tunisia': 'תוניסיה', 'Turkey': 'טורקיה', 'Turks and Caicos Islands': 'איי טרקס וקייקוס', 'Uganda': 'אוגנדה', 'Ukraine': 'אוקראינה',
    'United Arab Emirates': 'איחוד האמירויות', 'United Kingdom': 'בריטניה', 'United States': 'ארה״ב', 'Uruguay': 'אורוגוואי',
    'Uzbekistan': 'אוזבקיסטן', 'Venezuela': 'ונצואלה', 'Vietnam': 'וייטנאם', 'Yemen, Republic': 'תימן', 'Zambia': 'זמביה',
    'Zimbabwe': 'זימבבואה',
    # regions (brain/country_risk.json 'regions')
    'Africa': 'אפריקה', 'Asia': 'אסיה', 'Australia & New Zealand': 'אוסטרליה וניו זילנד', 'Caribbean': 'האיים הקריביים',
    'Central and South America': 'מרכז ודרום אמריקה', 'Eastern Europe': 'מזרח אירופה', 'Middle East': 'המזרח התיכון',
    'North America': 'צפון אמריקה', 'Western Europe': 'מערב אירופה', 'Global': 'העולם כולו', 'Africa & Mid East': 'המזרח התיכון ואפריקה',
    'Australia, NZ & Canada': 'אוסטרליה, ניו זילנד וקנדה', 'Latin America & Caribbean': 'אמריקה הלטינית והאיים הקריביים',
    'Europe': 'אירופה', 'Emerging Markets': 'שווקים מתעוררים', 'Small Asia (No India, China & Japan)': 'אסיה בלי הודו, סין ויפן',
    'EMEA': 'אירופה, המזרח התיכון ואפריקה'}
HE_ARTICLE = frozenset({'Congo (Democratic Republic of)', 'Congo (Republic of)', 'Dominican Republic', 'Isle of Man', 'Maldives',
                        'Philippines', 'Caribbean', 'Middle East', 'Africa & Mid East'})

_TABLE = None


def country_table():
    """The packaged Damodaran table (brain/country_risk.json), loaded once."""
    global _TABLE
    if _TABLE is None:
        with open(COUNTRY_RISK_FILE, encoding='utf-8') as f:
            _TABLE = json.load(f)
    return _TABLE


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _norm(name):
    s = unicodedata.normalize('NFKD', str(name or '')).encode('ascii', 'ignore').decode().upper()
    s = re.sub(r'\bMEMBER\b', ' ', s)
    return re.sub(r'[^A-Z0-9]+', ' ', s).strip()


def _known(fb):
    return set((fb or {}).get('sources') or {}) | {q.get('id') for q in (fb or {}).get('qualitative') or [] if q.get('id')}


def share_text(s):
    """A share as a percent with enough digits to stay visible: 25%, 3.5%, 0.42%, 0.0001% (an evidenced 0.3% never reads 0%)."""
    p = float(s) * 100
    if p >= 9.95 or p == 0:
        return f'{p:.0f}%'
    if p >= .995:
        return f'{p:.1f}'.rstrip('0').rstrip('.') + '%'
    d = max(2, 1 - math.floor(math.log10(p)))
    return f'{p:.{d}f}'.rstrip('0').rstrip('.') + '%'


def _evidence(ev, known, label):
    if not isinstance(ev, list) or not ev or any(e not in known for e in ev):
        raise ValueError(f'{label}: evidence_ids must be a non-empty list of FactBook source or qualitative ids')
    return list(ev)


def resolve(name, home=None, by_state=False):
    """Member or key -> {'kind': country|region|unmapped, 'name', 'crp', 'note'?}. home: the company's country (for 'Country Of
    Domicile'); by_state: two-letter members are US states (see US_STATE_ONLY)."""
    t = country_table()
    n = _norm(name)
    names = {_norm(k): k for k in t['countries']}
    iso = {v['iso2']: k for k, v in t['countries'].items() if v.get('iso2')}
    regions = {_norm(k): k for k in t['regions']}

    def country(k, note=None):
        out = {'kind': 'country', 'name': k, 'crp': t['countries'][k]['crp']}
        return dict(out, note=note) if note else out
    if n in HOME_WORDS:
        if not home:
            return {'kind': 'unmapped', 'name': None, 'crp': None, 'note': 'country of domicile without a company country'}
        r = resolve(home)
        return dict(r, note=f'country of domicile = {home}') if r['kind'] == 'country' else r
    if by_state and len(n) == 2:
        return country('United States', f'{n}: a US state')
    if n in COUNTRY_ALIASES:
        return country(COUNTRY_ALIASES[n], ALIAS_NOTES.get(n))
    if n in names:
        return country(names[n])
    if len(n) == 2 and n in iso:
        return country(iso[n])
    if n in REGION_ALIASES or n in regions:
        k = REGION_ALIASES.get(n) or regions[n]
        out = {'kind': 'region', 'name': k, 'crp': t['regions'][k]['crp']}
        return dict(out, note=REGION_NOTES[k]) if k in REGION_NOTES else out
    why = 'an aggregate that names no country' if n in AGGREGATE_WORDS else 'not a country or region in the table'
    return {'kind': 'unmapped', 'name': None, 'crp': None, 'note': why}


def _channel(shares, home, source, evidence_ids, as_of=None, note=None):
    """{member: share} -> rows with each member's CRP above the US base and its contribution; the channel's add-on."""
    t = country_table()
    base = t['countries'][t['base_country']]['crp']
    keys = [_norm(k) for k in shares]
    by_state = any(len(k) == 2 and k in US_STATE_ONLY for k in keys)
    rows, unmapped = [], 0.
    for member, share in shares.items():
        r = resolve(member, home, by_state)
        above = max(0., r['crp'] - base) if r['crp'] is not None else None
        row = {'member': member, 'share': share, 'kind': r['kind'], 'mapped_to': r['name'], 'crp': r['crp'], 'above_base': above,
               'contribution': share * above if above is not None else None}
        if r.get('note'):
            row['note'] = r['note']
        rows.append(row)
        if r['kind'] == 'unmapped':
            unmapped += share
    total = sum(shares.values())
    add_on = sum(r['contribution'] for r in rows if r['contribution'] is not None)
    out = {'source': source, 'as_of': as_of, 'rows': rows, 'add_on': add_on, 'mapped_share': total - unmapped,
           'unmapped_share': unmapped, 'not_itemized_share': max(0., 1 - total), 'evidence_ids': evidence_ids,
           'by_state': by_state}
    if note:
        out['note'] = note
    return out


def _override(spec, label, known):
    if not isinstance(spec, dict) or sorted(set(spec) - {'shares', 'evidence_ids', 'note', 'as_of'}):
        raise ValueError(f'{label}: an object with shares, evidence_ids, note and an optional as_of')
    sh = spec.get('shares')
    if not isinstance(sh, dict) or not sh:
        raise ValueError(f'{label}.shares: {{country, region or ISO code: share}}')
    out = {}
    for k, v in sh.items():
        x = _num(v)
        if x is None or not 0 <= x <= 1:
            raise ValueError(f'{label}.shares[{k!r}] must be a fraction from 0 to 1')
        out[str(k)] = x
    if sum(out.values()) > 1 + 1e-6:
        raise ValueError(f'{label}.shares add up to more than 100%')
    if not isinstance(spec.get('note'), str) or not spec['note'].strip():
        raise ValueError(f'{label}: a written note (what the split is and where it comes from) is required')
    return out, _evidence(spec.get('evidence_ids'), known, label)


def _disclosed_revenue(fb):
    """Revenue shares by geography from the annual report (business_map.tables.geography, latest year) or (None, why)."""
    bm = (fb or {}).get('business_map') or {}
    geo = (bm.get('tables') or {}).get('geography') or {}
    if not geo.get('revenue'):
        return None, 'the annual report has no revenue-by-geography table the engine could read'
    year = (geo.get('reconciliation') or {}).get('year')
    vals = {m: (v or {}).get(year) for m, v in geo['revenue'].items()}
    vals = {m: float(v) for m, v in vals.items() if _num(v) is not None and v > 0}
    tot = _num((geo.get('total_revenue') or {}).get(year)) or sum(vals.values())
    if not vals or not tot or tot <= 0:
        return None, 'the revenue-by-geography table has no positive values for its latest year'
    shares = {m: v / tot for m, v in vals.items()}
    note = None
    if sum(shares.values()) > 1 + OVERLAP_TOLERANCE:
        part = _partition(shares, ((fb or {}).get('company') or {}).get('country'))
        if part is None:
            return None, (f'the geography members of {year} add up to {sum(shares.values()):.0%} of revenue (overlapping categories) and '
                          'no subset adds up to the total: supply an evidenced split (inputs.market_context.geography)')
        note = (f'the members add up to {sum(shares.values()):.0%} of revenue (two overlapping splits on one axis); used the split '
                f"{', '.join(part)} (a complete split without overlap; of those, the largest add-on, which can overstate where regional "
                'averages carry it); not used: '
                + ', '.join(m for m in shares if m not in part))
        shares = {m: shares[m] for m in part}
    sid = bm.get('source_id')
    return {'shares': shares, 'as_of': year, 'evidence_ids': [sid] if sid in (fb.get('sources') or {}) else [], 'note': note}, None


def _partition(shares, home):
    """Overlapping members -> the members of the complete split that names the most (OVERLAP_TOLERANCE comment), or None."""
    names = sorted(shares)
    if len(names) > PARTITION_MAX_MEMBERS:
        return None
    res = {m: resolve(m, home) for m in names}
    kind = {m: r['kind'] for m, r in res.items()}
    base = country_table()['countries'][country_table()['base_country']]['crp']
    above = {m: max(0., r['crp'] - base) if r['crp'] is not None else 0. for m, r in res.items()}
    best = None
    for mask in range(1, 1 << len(names)):
        sub = [m for i, m in enumerate(names) if mask >> i & 1]
        if abs(sum(shares[m] for m in sub) - 1) > PARTITION_TOLERANCE or _overlap({m: res[m] for m in sub}):
            continue
        key = (-round(sum(shares[m] * above[m] for m in sub), 9), round(sum(shares[m] for m in sub if kind[m] == 'unmapped'), 9),
               -round(sum(shares[m] for m in sub if kind[m] == 'country'), 9), len(sub), sub)
        if best is None or key < best:
            best = key
    return best[-1] if best else None


def _disclosed_operations(fb):
    a = ((fb or {}).get('business_map') or {}).get('assets_by_geography') or {}
    sh = {m: float(v) for m, v in (a.get('shares') or {}).items() if _num(v) is not None and v > 0}
    if not sh:
        return None, 'no long-lived assets by geography in the annual report'
    sid = ((fb or {}).get('business_map') or {}).get('source_id')
    return {'shares': sh, 'as_of': a.get('as_of'), 'concept': a.get('concept'),
            'evidence_ids': [sid] if sid in (fb.get('sources') or {}) else []}, None


def _covers(member, r):
    """The country 'region' labels a member covers (None: not known, e.g. 'Other countries')."""
    if r['kind'] == 'region':
        return REGION_COVERS.get(r['name'], ALL_LABELS)
    n = _norm(member)
    if n == 'AMERICAS':
        return AMERICAS_LABELS
    if n in NON_US_WORDS:
        return ALL_LABELS | {'non_us'}
    return None


def _overlap(resolved):
    """{member: resolve()} -> why the members may double count (a region or aggregate next to a country or region it covers), or ''."""
    t = country_table()
    country = {m: (r['name'], t['countries'][r['name']].get('region')) for m, r in resolved.items() if r['kind'] == 'country'}
    cov = {m: _covers(m, r) for m, r in resolved.items() if r['kind'] != 'country'}
    for m, c in cov.items():
        if c is None:
            continue
        inside = [x for x, (name, lab) in country.items() if lab in c or ('non_us' in c and name != 'United States')]
        inside += [x for x, c2 in cov.items() if x != m and c2 is not None and c & c2]
        if inside:
            return f"{m} next to {', '.join(sorted(inside))}, which it covers"
    return ''


def _asset_overlap(rows):
    """Why an asset split may double count (an aggregate, or a region next to a country it covers), or ''."""
    agg = [r['member'] for r in rows if r['kind'] == 'unmapped']
    if agg:
        return 'members that name no country (' + ', '.join(agg) + ')'
    why = _overlap({r['member']: {'kind': r['kind'], 'name': r['mapped_to']} for r in rows})
    return f'a region next to what it covers ({why})' if why else ''


def country_risk(fb, spec, known=None):
    """Country-risk add-on for the cost of equity (module docstring, part 1). Returns the full record; 'forecast_input' is
    the inputs.forecast.country_risk object the pipeline passes on (None when nothing is applied)."""
    t = country_table()
    known = _known(fb) if known is None else known
    base = t['countries'][t['base_country']]['crp']
    home = ((fb or {}).get('company') or {}).get('country')
    table = {k: t[k] for k in ('title', 'url', 'xlsx_url', 'as_of', 'page_last_updated', 'as_of_basis', 'retrieved_at',
                               'mature_market_erp', 'us_erp', 'relative_equity_volatility')}
    out = {'status': None, 'add_on': None, 'channel': None, 'revenue': None, 'operations': None, 'table': table,
           'base': {'country': t['base_country'], 'crp': base, 'note': t['base_note']}, 'open_questions': [], 'forecast_input': None,
           'formula': 'add-on(channel) = sum(share_i x max(0, CRP_i - CRP_US)); add-on = max(revenue, operations); '
                      'only the part above the US base (the engine ERP is the US implied premium, which carries the US CRP); '
                      'net row = add-on - COUNTRY_RISK_MARKET_RATE x mapped share (the market average already in the ERP and the '
                      'index return)'}
    if str(fb['as_of'])[:10] < t['as_of']:
        out['status'] = 'table_after_as_of'
        out['open_questions'].append(f"the packaged country risk table is dated {t['as_of']}, after as_of {fb['as_of']}: "
                                     'no country-risk row (point in time); supply a table available at as_of')
        return out
    for ch, key, disclosed in (('revenue', 'geography', _disclosed_revenue), ('operations', 'operations', _disclosed_operations)):
        if spec.get(key) is not None:
            sh, ev = _override(spec[key], f'market_context.{key}', known)
            out[ch] = _channel(sh, home, 'input', ev, spec[key].get('as_of'), spec[key]['note'])
            continue
        d, why = disclosed(fb)
        if d is None:
            if ch == 'revenue':
                out['open_questions'].append(f'country risk: {why}; revenue by country is unknown (no add-on, no default)')
            continue
        if not d['evidence_ids']:
            out['open_questions'].append(f'country risk ({ch}): the disclosed split has no FactBook source id; not used')
            continue
        c = _channel(d['shares'], home, 'disclosed', d['evidence_ids'], d.get('as_of'), d.get('note'))
        if ch == 'operations' and _asset_overlap(c['rows']):
            # asset shares are normalized over all members (business.assets_by_geography), so overlap cannot be seen in their
            # sum: a table with an aggregate ('Non US', 'Other countries') or a region next to a country it covers ('Europe'
            # next to 'Germany') is not used
            out['open_questions'].append('country risk (operations): the long-lived assets table has ' + _asset_overlap(c['rows'])
                                         + ', so overlap cannot be ruled out; supply an evidenced inputs.market_context.operations '
                                         'split if production is concentrated abroad')
            continue
        out[ch] = c
    for ch in CHANNELS:
        c = out[ch]
        if c and c['unmapped_share'] > 1e-9:
            out['open_questions'].append(
                f"country risk ({ch}): {c['unmapped_share']:.0%} sits in members that name no country ("
                + ', '.join(r['member'] for r in c['rows'] if r['kind'] == 'unmapped') + '): no add-on for that share; supply an '
                f"evidenced split (inputs.market_context.{'geography' if ch == 'revenue' else 'operations'})")
        if c and c['not_itemized_share'] > OVERLAP_TOLERANCE:
            out['open_questions'].append(f"country risk ({ch}): {c['not_itemized_share']:.0%} is not itemized by country: no add-on for it")
    live = [ch for ch in CHANNELS if out[ch]]
    if not live:
        out['status'] = 'no_exposure_data'
        return out
    ch = max(live, key=lambda k: (out[k]['add_on'], k == 'revenue'))
    gap = out[ch]['unmapped_share'] + out[ch]['not_itemized_share']
    # partial: part of the channel is not mapped to a country (an aggregate, or not itemized): the add-on counts only the mapped
    # part, so it is a lower bound, labelled as such (MercadoLibre's geography table itemizes 0.2% of revenue: its country
    # segments are not on the geographic axis)
    out.update(status='applied' if gap <= OVERLAP_TOLERANCE else 'partial', add_on=out[ch]['add_on'], channel=ch, uncovered_share=gap)
    top = sorted((r for r in out[ch]['rows'] if (r['contribution'] or 0) > 0), key=lambda r: -r['contribution'])
    detail = ('; '.join(f"{r['share']:.0%} {r['mapped_to']} (CRP {r['crp']:.2%}, {r['above_base']:+.2%} above the US)" for r in top[:4])
              or 'no exposure above the US base') + (f'; lower bound: {gap:.0%} of the channel is not mapped to a country' if gap > OVERLAP_TOLERANCE else '')
    mapped = min(max(out[ch]['mapped_share'], 0.), 1.)
    out['market_neutral'] = {'rate': COUNTRY_RISK_MARKET_RATE, 'mapped_share': mapped, 'value': -COUNTRY_RISK_MARKET_RATE * mapped,
                             'net': out['add_on'] - COUNTRY_RISK_MARKET_RATE * mapped,
                             'basis': "the market's average country add-on per unit of mapped exposure (COUNTRY_RISK_MARKET_RATE, measured "
                                      'on the 2026-10-21 large-cap sweep) times the mapped share: already in the ERP and the index '
                                      'return, so it is netted out (an unmapped share counts as average)'}
    out['forecast_input'] = {
        'value': round(out['add_on'], 8), 'mapped_share': round(mapped, 8), 'as_of': t['as_of'], 'channel': ch,
        'title': f"Damodaran country risk premiums ({t['as_of']}) x {ch} exposure ({out[ch]['source']})",
        'url': t['url'], 'detail': detail, 'evidence_ids': sorted(set(out[ch]['evidence_ids']))}
    return out


def exposures(items, known):
    """Validate inputs.market_context.exposures -> [{id, kind, share, share_basis, shock, cost_rate, evidence_ids, note,
    mechanism, label_he}]. Sized only from the evidenced share (and cost rate): no default severity or probability."""
    if items is None:
        return []
    if not isinstance(items, list):
        raise ValueError('market_context.exposures: a list of items')
    out, seen = [], {}
    for i, it in enumerate(items):
        label = f'market_context.exposures {i}'
        if not isinstance(it, dict) or sorted(set(it) - set(EXPOSURE_KEYS)):
            raise ValueError(f"{label}: keys {', '.join(EXPOSURE_KEYS)}")
        kind = it.get('kind')
        if kind not in EXPOSURE_KINDS:
            raise ValueError(f"{label}: kind must be one of {', '.join(EXPOSURE_KINDS)}")
        k = EXPOSURE_KINDS[kind]
        given = [f for f in ('share_of_revenue', 'share_of_supply') if it.get(f) is not None]
        if len(given) != 1 or given[0] not in k['shares']:
            raise ValueError(f"{label}: exactly one of {', '.join(k['shares'])} (the evidenced share exposed)")
        share = _num(it[given[0]])
        if share is None or not 0 < share <= 1:
            raise ValueError(f'{label}: {given[0]} must be a fraction above 0 and at most 1')
        shock = it.get('shock', k['shock'])
        if shock not in SHOCKS:
            raise ValueError(f"{label}: shock must be one of {', '.join(SHOCKS)}")
        rate = it.get('cost_rate')
        if shock == 'margin':
            rate = _num(rate)
            if rate is None or not 0 < rate <= 1:
                raise ValueError(f'{label}: a margin shock needs the evidenced cost_rate (e.g. the tariff rate) as a fraction; '
                                 'there is no default severity')
        elif rate is not None:
            raise ValueError(f'{label}: cost_rate applies to a margin shock only')
        if not isinstance(it.get('note'), str) or not it['note'].strip():
            raise ValueError(f'{label}: a written note (what is exposed and why) is required')
        ev = _evidence(it.get('evidence_ids'), known, label)
        seen[kind] = seen.get(kind, 0) + 1
        xid = kind if seen[kind] == 1 else f'{kind}_{seen[kind]}'
        what = 'ההכנסות' if given[0] == 'share_of_revenue' else 'האספקה'
        out.append({'id': xid, 'kind': kind, 'share': share, 'share_basis': given[0], 'shock': shock, 'cost_rate': rate,
                    'evidence_ids': ev, 'note': it['note'].strip(), 'mechanism': k['mechanism'],
                    'label_he': f"{k['he']} (כ־{share_text(share)} מ{what})"})
    return out


def shock_row(row, x, count, stub):
    """Apply exposure x to one driver row in place (count periods, stub = 1 when the first period is a stub). -> edit texts."""
    def expand(key, n):
        v = row.get(key)
        return list(v) if isinstance(v, list) else [v] * n
    s, edits = x['share'], []
    what = 'revenue' if x['share_basis'] == 'share_of_revenue' else 'supply'
    if x['shock'] == 'revenue':
        g = expand('growth', count - stub)
        g0 = (1 + g[0]) * (1 - s) - 1
        g[0] = max(g0, GROWTH_FLOOR)
        row['growth'] = g
        edits.append(f"first full fiscal year revenue x (1 - {share_text(s)}): the evidenced {share_text(s)} of {what} exposed to {x['kind']} is lost and "
                     'does not come back; later years keep the row\'s growth on the smaller base; costs flex with revenue (no operating '
                     'deleverage assumed: no default severity)')
        if g0 < GROWTH_FLOOR:
            edits.append(f'first-year growth {g0:.1%} clamped at {GROWTH_FLOOR:.0%} (the driver contract\'s bound)')
        return edits
    r = x['cost_rate']
    ccr = expand('cash_cost_ratio', count)
    if x['share_basis'] == 'share_of_revenue':
        delta = [s * r] * count
        edits.append(f'cash cost ratio + {share_text(s)} x {share_text(r)} = +{share_text(s * r)} of revenue in every year: the exposed revenue bears the '
                     'evidenced cost rate and it is not passed on')
    else:
        delta = [c * s * r for c in ccr]
        edits.append(f'cash cost ratio x (1 + {share_text(s)} x {share_text(r)}) in every year: the exposed {share_text(s)} of cash costs rises by the evidenced '
                     'rate and it is not passed on')
    row['cash_cost_ratio'] = [min(c + d, 2.) for c, d in zip(ccr, delta)]
    t = row['terminal']
    tm = t['operating_margin'] - delta[-1]
    t['operating_margin'] = max(TERMINAL_MARGIN_FLOOR, tm)
    edits.append(f"terminal operating margin {t['operating_margin']:.1%} (the same cost increase)"
                 + (f' (floored at {TERMINAL_MARGIN_FLOOR:.0%}; {tm:.1%} before the floor)' if tm < TERMINAL_MARGIN_FLOOR else ''))
    return edits


# ---------------------------------------------------------------- market regime

def _band(x, bands):
    return next(lab for hi, lab in bands if x < hi) if x is not None else None


def read_fred(path):
    """FRED fredgraph.csv (observation_date, SERIES) -> [(date, value)], missing values ('.' or empty) skipped."""
    with open(path, encoding='utf-8') as f:
        rows = list(csv.reader(f))
    out = []
    for r in rows[1:]:
        if len(r) >= 2 and r[1] not in ('', '.'):
            try:
                out.append((date.fromisoformat(r[0][:10]), float(r[1])))
            except ValueError:
                continue
    return out


def read_regime(folder, as_of):
    """The saved collector folder (scripts/collect_market_context.py: MANIFEST.json + one CSV per series) -> the latest reading
    of each series on or before as_of, the reading about a year earlier, URL and retrieval time. Pure (reads files only)."""
    with open(os.path.join(folder, 'MANIFEST.json'), encoding='utf-8') as f:
        man = json.load(f)
    cut = date.fromisoformat(str(as_of)[:10])
    readings, missing = {}, []
    for c in man.get('calls') or []:
        s = c.get('series')
        path = os.path.join(folder, c.get('file') or '')
        if s not in REGIME_SERIES or not os.path.exists(path):
            continue
        obs = [(d, v) for d, v in read_fred(path) if d <= cut]
        if not obs:
            missing.append(s)
            continue
        d, v = obs[-1]
        ago = [(d2, v2) for d2, v2 in obs if d2 <= d - timedelta(days=365)]
        readings[s] = {'value': v, 'date': d.isoformat(), 'title': REGIME_SERIES[s], 'url': c.get('url') or FRED_URL.format(s),
                       'retrieved_at': c.get('retrieved_at_utc'), 'sha256': c.get('sha256')}
        if ago:
            readings[s]['year_ago'] = {'value': ago[-1][1], 'date': ago[-1][0].isoformat()}
    missing += [s for s in REGIME_SERIES if s not in readings and s not in missing]
    return {'as_of': str(as_of)[:10], 'source': 'FRED (Federal Reserve Bank of St. Louis)', 'readings': readings, 'missing': missing,
            'collected_at': man.get('retrieved_at_utc')}


def _he_date(d):
    return f'{d[8:10]}.{d[5:7]}.{d[:4]}' if d and len(d) >= 10 else (d or '')


def classify_regime(regime, erp=None, engine_market=None, as_of=None):
    """Readings -> levels, flags and one Hebrew line. erp: {value, as_of} (the engine's dated implied ERP); engine_market:
    {value, basis} (calibration's engine-consistent market return). Context only: changes no value, rate or verdict."""
    rd = {s: r for s, r in ((regime or {}).get('readings') or {}).items() if s in REGIME_SERIES and _num((r or {}).get('value')) is not None}
    dropped = []
    if as_of:
        for s in list(rd):
            if str(rd[s].get('date') or '')[:10] > str(as_of)[:10]:
                dropped.append(s)
                rd.pop(s)
    v = {s: float(r['value']) for s, r in rd.items()}
    erp_v = _num((erp or {}).get('value'))
    if erp and as_of and str(erp.get('as_of') or '')[:10] > str(as_of)[:10]:
        erp_v = None
    mkt = _num((engine_market or {}).get('value'))
    levels = {'credit': _band(v.get('BAA10Y'), CREDIT_BANDS), 'volatility': _band(v.get('VIXCLS'), VOL_BANDS),
              'curve': _band(v.get('T10Y2Y'), CURVE_BANDS), 'erp': _band(erp_v, ERP_BANDS)}
    flags = [f for f, on in (('curve_inverted', levels['curve'] == 'inverted'), ('credit_stress', levels['credit'] == 'stress'),
                             ('credit_wide', levels['credit'] == 'wide'), ('high_volatility', levels['volatility'] == 'high'),
                             ('elevated_volatility', levels['volatility'] == 'elevated'), ('market_rich', levels['erp'] == 'rich'),
                             ('market_cheap', levels['erp'] == 'cheap')) if on]
    when = max((r['date'] for r in rd.values()), default=None)
    first = min((r['date'] for r in rd.values()), default=None)
    # the readings' dates (series publish on different days): one date, or the range when they differ
    date_he = None if not when else _he_date(when) if first == when else f'{_he_date(first)}–{_he_date(when)}'
    parts = []
    if 'DGS10' in v:
        parts.append(f"ריבית ל־10 שנים {v['DGS10']:.2f}%")
    if 'BAA10Y' in v:
        parts.append(f"פערי אשראי {v['BAA10Y']:.2f}% ({HE[levels['credit']]})")
    if 'VIXCLS' in v:
        parts.append(f"תנודתיות (VIX) {v['VIXCLS']:.1f} ({VOL_HE[levels['volatility']]})")
    if 'T10Y2Y' in v:
        parts.append(f"עקום התשואות {v['T10Y2Y']:+.2f}% ({CURVE_HE[levels['curve']]})")
    if erp_v is not None:
        parts.append(f"פרמיית הסיכון של השוק {erp_v * 100:.2f}% ({HE[levels['erp']]})")
    if mkt is not None:
        parts.append(f'תשואה צפויה לשוק לפי המנוע כ־{mkt * 100:.1f}% בשנה')
    body = ('; '.join(parts) + '. רקע בלבד: לא משנה את השווי.') if parts else None
    line = f"מצב השוק{(' ב־' + date_he) if date_he else ''}: {body}" if body else None
    short = [f"ריבית ל־10 שנים {v['DGS10']:.1f}%" if 'DGS10' in v else None,
             f"פערי אשראי {HE[levels['credit']]}" if levels['credit'] else None,
             f"תנודתיות {VOL_HE[levels['volatility']]}" if levels['volatility'] else None,
             f"עקום {CURVE_HE[levels['curve']]}" if levels['curve'] else None]
    short = [x for x in short if x]
    short_he = ('רקע שוק, לא משנה את השווי: ' + ', '.join(short) + '.') if short else None
    return {'as_of': when, 'readings': rd, 'equity_risk_premium': erp_v, 'engine_market_return': mkt, 'levels': levels, 'flags': flags,
            'line_he': line, 'body_he': body, 'date_he': date_he, 'short_he': short_he, 'dropped_after_as_of': dropped, 'missing': [s for s in REGIME_SERIES if s not in rd],
            'thresholds': {'credit_BAA10Y_pct': CREDIT_BANDS[:-1], 'vix': VOL_BANDS[:-1], 'curve_T10Y2Y_pct': CURVE_BANDS[:-1],
                           'implied_erp': ERP_BANDS[:-1],
                           'basis': 'percentiles of each full FRED daily history and of Damodaran\'s year-end implied ERP 1961-2025 '
                                    '(computed 2026-10-25); descriptive bands, not forecasts'},
            'meaning': 'context only: no value, discount rate, scenario or verdict reads the regime'}


# ---------------------------------------------------------------- the stage

def _regime_inputs(spec, inputs):
    """The ERP and engine market return the regime line shows: the dated forecast ERP input / calibration index input first."""
    rg = spec.get('regime') or {}
    erp = ((inputs or {}).get('forecast') or {}).get('equity_risk_premium') or rg.get('equity_risk_premium')
    idx = ((inputs or {}).get('decision') or {}).get('index') or rg.get('engine_market_return')
    erp = erp if isinstance(erp, dict) and erp.get('as_of') else None            # an undated default is not a reading
    return erp, (idx if isinstance(idx, dict) else None)


def build(fb, spec, inputs=None):
    """inputs.market_context -> {'country_risk', 'exposures', 'regime', 'open_questions'} (pipeline stage 'market_context')."""
    if not isinstance(spec, dict):
        raise ValueError('market_context must be an object')
    extra = sorted(set(spec) - set(KNOWN_KEYS))
    if extra:
        raise ValueError(f"market_context: unknown keys {extra} (known: {', '.join(KNOWN_KEYS)})")
    known = _known(fb)
    if spec.get('country_risk', True) not in (True, False):
        raise ValueError('market_context.country_risk: true (default) or false')
    if spec.get('country_risk', True):
        cr = country_risk(fb, spec, known)
    else:
        cr = {'status': 'not_requested', 'add_on': None, 'open_questions': [], 'forecast_input': None}
    ex = exposures(spec.get('exposures'), known)
    rg = None
    if spec.get('regime') is not None:
        if not isinstance(spec['regime'], dict):
            raise ValueError('market_context.regime: the object read_regime returns (readings by FRED series)')
        erp, idx = _regime_inputs(spec, inputs)
        rg = classify_regime(spec['regime'], erp, idx, fb['as_of'])
    oq = list(cr['open_questions'])
    if rg and rg['missing']:
        oq.append('market regime: no reading for ' + ', '.join(rg['missing']))
    if rg and rg['dropped_after_as_of']:
        oq.append('market regime: readings dated after as_of not used (' + ', '.join(rg['dropped_after_as_of']) + ')')
    return {'market_context_version': 1, 'as_of': fb['as_of'], 'country_risk': cr, 'exposures': ex, 'regime': rg,
            'open_questions': oq,
            'meaning': 'country risk is a cost-of-equity row; each exposure is a named failure scenario and a counter-thesis entry; '
                       'the regime is context only. Nothing is a probability or a price forecast.'}


def with_regime(inputs, regime):
    """inputs with the regime readings added as inputs.market_context.regime (a company's own regime input wins). The regime is
    context only, so when the company has no market_context of its own the stage is switched on for the regime alone
    (country_risk: false): adding market readings never moves a value. A company's own market_context (even {}) keeps its
    country-risk row; with_country_risk switches it on explicitly."""
    inp = deepcopy(inputs or {})
    own = 'market_context' in inp
    mc = dict(inp.get('market_context') or {})
    mc.setdefault('regime', regime)
    if not own:
        mc['country_risk'] = False
    inp['market_context'] = mc
    return inp


def with_country_risk(inputs):
    """inputs with the market_context stage switched on (country risk from the company's own geography disclosures or its
    evidenced overrides): the CLI's --country-risk. Apply it before with_regime. A company's explicit country_risk: false is kept."""
    inp = deepcopy(inputs or {})
    inp['market_context'] = dict(inp.get('market_context') or {})
    return inp


# ---------------------------------------------------------------- Hebrew explanations (render, analyst sheet)

def _he_name(k):
    return COUNTRY_HE.get(k, k)


def _in_he(k):
    """'in <country or region>' in Hebrew: בצרפת, במזרח התיכון (the article drops after the prefix), ב־Name for a name without
    a Hebrew form (a prefix is never glued to Latin letters)."""
    h = COUNTRY_HE.get(k)
    if h is None:
        return 'ב־' + str(k)
    return 'ב' + (h[1:] if k in HE_ARTICLE else h)


def country_risk_he(mc):
    """One short Hebrew explanation of the country-risk row (or why there is none)."""
    cr = (mc or {}).get('country_risk') or {}
    if cr.get('status') in ('applied', 'partial'):
        c = cr[cr['channel']]
        top = sorted((r for r in c['rows'] if (r['contribution'] or 0) > 0), key=lambda r: -r['contribution'])
        where = 'מההכנסות' if cr['channel'] == 'revenue' else 'מהייצור והנכסים'
        gap = (f" {cr['uncovered_share']:.0%} {where} לא מפורטים לפי מדינה ולא נכללו (שאלה פתוחה), ולכן זה רף תחתון."
               if cr['status'] == 'partial' else '')
        mn = cr.get('market_neutral') or {}
        net = mn.get('net', cr['add_on'])
        avg = ('פרמיית השוק כבר כוללת את החשיפה הממוצעת של החברות הגדולות בארה״ב לחו״ל (כ־'
               f"{-mn.get('value', 0.) * 100:.2f}% לפי החלק המפורט)")
        move = (f"נטו נוספו כ־{net * 100:.2f}% לשיעור ההיוון" if net >= .00005 else
                f"נטו שיעור ההיוון נמוך בכ־{-net * 100:.2f}%" if net <= -.00005 else 'נטו שיעור ההיוון כמעט לא משתנה')
        src = f"(שיטת דמודרן, טבלה מ־{_he_date(cr['table']['as_of'])})"
        if not top:
            head = (f"סיכון מדינה: בחלק המפורט של {('ההכנסות' if cr['channel'] == 'revenue' else 'הנכסים')} אין חשיפה למדינות מסוכנות "
                    'יותר מארה״ב')
            if not mn.get('value'):
                return head + ', ולכן לא נוספה פרמיה לשיעור ההיוון.' + gap
            return f'{head}; {avg}, ולכן {move} {src}.{gap}'
        lst = ', '.join(f"כ־{share_text(r['share'])} {where} {_in_he(r['mapped_to'])} (פרמיה {r['crp'] * 100:.2f}%)" for r in top[:3])
        if not mn:
            return f"סיכון מדינה: {lst}, לעומת {cr['base']['crp'] * 100:.2f}% בארה״ב; נוספו כ־{cr['add_on'] * 100:.2f}% לשיעור ההיוון {src}.{gap}"
        return (f"סיכון מדינה: {lst}, לעומת {cr['base']['crp'] * 100:.2f}% בארה״ב: תוספת של כ־{cr['add_on'] * 100:.2f}% לפי החשיפה; "
                f'{avg}, ולכן {move} {src}.{gap}')
    if cr.get('status') in ('no_exposure_data', 'table_after_as_of'):
        return 'סיכון מדינה לא חושב: אין פילוח הכנסות לפי מדינה שאפשר לקרוא (שאלה פתוחה, בלי ברירת מחדל).' \
            if cr['status'] == 'no_exposure_data' else 'סיכון מדינה לא חושב: טבלת הפרמיות מאוחרת לתאריך הניתוח.'
    return None


def exposure_lines_he(cf):
    """Hebrew counter-thesis lines: each evidenced exposure with how much lower the base-scenario value is today if it happens
    (base_effect of the executed failure scenario; a percentage, so it reads the same next to the table's fair value at the
    required return), never a probability. The values themselves are in the report's Market context section."""
    mc = (cf or {}).get('market_context') or {}
    scen = {s.get('name'): s for s in ((cf or {}).get('rk') or {}).get('failure_scenarios') or []}
    out = []
    for x in mc.get('exposures') or []:
        eff = _num((scen.get('exposure:' + x['id']) or {}).get('base_effect'))
        if eff is not None:
            move = f'נמוך בכ־{eff:.0%}' if eff >= .005 else ('גבוה בכ־' + f'{-eff:.0%}' if eff <= -.005 else 'כמעט לא משתנה')
            out.append(f"{x['label_he']}: אם זה יקרה, השווי בתרחיש הבסיסי {move} (המודל הורץ מחדש עם הפגיעה הזו בלבד); "
                       'זה תרחיש, לא הסתברות.')
        else:
            out.append(f"{x['label_he']}: ההשפעה על השווי עוד לא חושבה (המודל לא הצליח להריץ את התרחיש מחדש; פירוט בדוח המלא).")
    return out
