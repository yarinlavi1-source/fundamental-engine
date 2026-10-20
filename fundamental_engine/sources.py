"""Discovery channels: where ideas come from, what each one can and cannot tell, and how it enters the research.

Every channel has a ROLE, and the role decides how its output is used:
- screen: broad numeric data on (nearly) every listed company; feeds the six discovery lanes directly.
- signal: an action by informed people with their own money or contracts (insider purchases, new contracts, backlog);
  attached to a card as support and able to open a research card on its own (decision 'maybe').
- attention: what people talk about (news volume, social mentions, app or search trends); measures crowding and gives
  leads to verify in filings; never evidence about the economics and never a reason to buy.
- lead: a person's thesis (an analyst, a newsletter, the owner's channels); enters the exploratory path only with a
  written reason and dated evidence, and is verified independently.
- evidence: primary documents (10-K, 10-Q, 8-K, transcripts) that answer the research questions.
Syndicated repetition of one story counts once (CLAUDE.md: repeated content is not independent evidence).
"""

CHANNELS = [
    {'id': 'sec_frames_scan', 'role': 'screen', 'status': 'built', 'how': 'scripts/collect_universe.py + fundamental_engine universe',
     'covers': 'NYSE/Nasdaq US-GAAP filers, annual fundamentals 2009-2025, every company at once',
     'timing': 'annual frames: up to a year old for December filers by October', 'bias': 'IFRS filers and funds out; current listings only for prices'},
    {'id': 'sec_insider_purchases', 'role': 'signal', 'status': 'built',
     'how': 'SEC insider transactions data sets (quarterly zip) -> insiders.purchases / clusters -> universe --insiders',
     'covers': 'every Form 4 open-market purchase (code P) by officers and directors', 'timing': 'filed within 2 business days; data set quarterly',
     'bias': 'routine small purchases and director-only buying are weaker; sales ignored by design'},
    {'id': 'sec_filings_flow', 'role': 'evidence', 'status': 'built (per company)', 'how': 'handoff.filing_documents + business.business_map',
     'covers': 'latest 10-K/20-F and 10-Q of each researched company: segments, products, geography, RPO, concentration, text',
     'timing': 'as filed', 'bias': 'XBRL omits units, prices, retention: read Items 1, 1A, 7'},
    {'id': 'large_cap_valuation_sweep', 'role': 'screen', 'status': 'built', 'how': 'collect_companies --docs-when-needed + deep --name large500',
     'covers': 'the full engine on the ~500 largest operating companies: implied return vs the engine-consistent market return',
     'timing': 'daily prices, quarterly fundamentals', 'bias': 'mechanical drafts: low confidence until researched'},
    {'id': 'sec_8k_events', 'role': 'signal', 'status': 'not built',
     'how': 'EDGAR daily index of 8-K items 1.01 (material agreements), 2.02 (results), 5.02 (management changes)',
     'covers': 'new contracts, guidance, leadership changes', 'timing': 'within 4 business days', 'bias': 'press-release language; verify the exhibit'},
    {'id': 'sec_13f_positions', 'role': 'signal', 'status': 'not built', 'how': 'SEC Form 13F data sets: new positions of long-term investors',
     'covers': 'institutions above $100m', 'timing': '45 days after quarter end (stale)', 'bias': 'herding; long-only, no shorts'},
    {'id': 'earnings_transcripts', 'role': 'evidence', 'status': 'connector',
     'how': 'Alpha Vantage EARNINGS_CALL_TRANSCRIPT / FMP earningsTranscript', 'covers': 'management commentary on demand, pricing, backlog',
     'timing': 'quarterly', 'bias': "management's framing: separate guidance from reported fact"},
    {'id': 'estimate_revisions', 'role': 'signal', 'status': 'connector', 'how': 'FMP analyst estimates, Alpha Vantage EARNINGS_ESTIMATES',
     'covers': 'consensus and its changes', 'timing': 'daily', 'bias': 'consensus is an estimate, never a fact; plan-tier limits'},
    {'id': 'news_sentiment', 'role': 'attention', 'status': 'connector', 'how': 'Alpha Vantage NEWS_SENTIMENT, FMP news',
     'covers': 'news flow and tone per ticker', 'timing': 'live', 'bias': 'syndication repeats one story many times; tone is not value'},
    {'id': 'social_attention', 'role': 'attention', 'status': 'connector', 'how': 'LunarCrush topic/stocks (X, Reddit, YouTube, TikTok)',
     'covers': 'mentions, creators, sentiment, social dominance vs its own 1-year range', 'timing': 'live',
     'bias': 'promotion and bots; a crowded name is priced for attention: use for crowding and leads only'},
    {'id': 'themes_and_supply_chain', 'role': 'lead', 'status': 'connector',
     'how': 'Interactive Brokers get_company_themes / get_company_connections / saved screeners',
     'covers': 'peers in a theme, suppliers and customers (bottleneck mapping)', 'timing': 'vendor-updated', 'bias': 'membership is not exposure'},
    {'id': 'owner_channels', 'role': 'lead', 'status': 'skill', 'how': 'stock-idea-sourcing skill (the owner\'s followed analysts, e.g. on X)',
     'covers': 'long-form theses on small and mid caps', 'timing': 'as posted', 'bias': 'one author\'s view: independent verification required'},
    {'id': 'product_adoption', 'role': 'attention', 'status': 'manual (web search)',
     'how': 'app-store ranks, search trends, web traffic, reviews via web search; cited with date',
     'covers': 'consumer and software adoption before it reaches revenue', 'timing': 'live',
     'bias': 'downloads are not paid adoption; verify against reported revenue'},
]


def by_role(role):
    return [c for c in CHANNELS if c['role'] == role]


def attention_reading(dominance, low, high):
    """Crowding from a social-dominance reading against its own 1-year range: 'under_followed' in the bottom fifth,
    'crowded' in the top fifth, else 'normal'. A reading, not a signal."""
    if None in (dominance, low, high) or high <= low:
        return 'unknown'
    x = (dominance - low) / (high - low)
    return 'under_followed' if x <= .2 else 'crowded' if x >= .8 else 'normal'


def lead_evidence(source, date, statement, role):
    """An evidence item for candidates.cards exploratory/signals; role decides how far it can move a card."""
    if role not in ('signal', 'lead', 'attention', 'evidence'):
        raise ValueError('role must be signal, lead, attention or evidence')
    if not all(str(x or '').strip() for x in (source, date, statement)):
        raise ValueError('source, date and statement are required')
    return {'source': source, 'date': date, 'statement': statement, 'kind': role,
            'weight': {'evidence': 'answers a research question', 'signal': 'supports a research priority',
                       'lead': 'opens a question to verify', 'attention': 'crowding or a lead only; never evidence'}[role]}
