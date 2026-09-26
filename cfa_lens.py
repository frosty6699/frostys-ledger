"""The CFA Lens: pairs the day's top stories with the CFA Level I concept they illustrate.

Each concept lists the words that signal it. The paper tries concepts in the order
below (specific before general), walking the stories from the front page down, and
keeps three that cover different topic areas where it can.
"""
import re

CONCEPTS = [
    {"title": "Interest rate parity", "topic": "Economics",
     "match": [r"carry trade", r"rate differential", r"forward premium", r"hedging costs?", r"spreads? with asia"],
     "explain": "Covered interest parity says forward exchange rates adjust for the gap in interest rates, so you "
                "can't lock in a riskless profit by borrowing in a low-rate currency and lending in a high-rate one.",
     "angle": "F/S = (1 + i_price) ÷ (1 + i_base). The higher-rate currency trades at a forward discount."},
    {"title": "The yield curve", "topic": "Fixed Income",
     "match": [r"\b(2|5|10|30)-year\b", r"yield curve", r"long-term borrowing", r"term premium",
               r"\bsteepen", r"\bflatten", r"\binvert"],
     "explain": "The yield curve plots yields across maturities. When long-dated yields rise faster than short "
                "ones the curve steepens, often a sign of higher expected inflation or heavy government borrowing.",
     "angle": "Know the three theories: pure expectations, liquidity preference and market segmentation. "
              "An inverted curve (short above long) has often come before recessions."},
    {"title": "Liquidity operations", "topic": "Economics",
     "match": [r"\bliquidity\b", r"\bvrrr\b", r"reverse repo", r"\blaf\b", r"\bcrr\b", r"\bslr\b",
               r"open market operation", r"\bomos?\b", r"currency swaps?"],
     "explain": "Besides setting rates, the RBI manages how much cash sits in the banking system: reverse repos "
                "soak up surplus cash, while repos and bond purchases inject it.",
     "angle": "Open market operations: buying bonds adds bank reserves (easing); selling bonds drains them "
              "(tightening)."},
    {"title": "Forex reserves & intervention", "topic": "Economics",
     "match": [r"forex reserves", r"foreign exchange reserves", r"\binterven"],
     "explain": "The RBI keeps a stockpile of dollars and other assets. It sells dollars to steady the rupee when "
                "it falls too fast, and buys them when it rises.",
     "angle": "Sterilised intervention offsets the effect on domestic money supply with opposite bond trades; "
              "unsterilised intervention lets the money supply change."},
    {"title": "The repo rate & policy transmission", "topic": "Economics",
     "match": [r"\brepo rate\b", r"\bmpc\b", r"\brbi\b.{0,40}\b(rate|policy)\b", r"monetary policy",
               r"rate (cut|hike)s?"],
     "explain": "The repo rate is what the RBI charges banks for overnight loans against government bonds. "
                "Raising it makes borrowing costlier across the economy to cool inflation; cutting it does the "
                "opposite.",
     "angle": "Transmission runs policy rate → bank lending and deposit rates → spending and investment → growth "
              "and inflation, with a lag. Policy is contractionary when the policy rate sits above the neutral rate."},
    {"title": "The Fed & global capital flows", "topic": "Economics",
     "match": [r"\bfed\b", r"federal reserve", r"\bfomc\b", r"\bwarsh\b", r"\bpowell\b"],
     "explain": "The US Federal Reserve sets the price of dollars. When US rates rise, money tends to leave "
                "emerging markets like India for dollar assets, pressuring the rupee and local shares.",
     "angle": "Interest-rate differentials drive capital flows and exchange rates: tighter US policy usually "
              "strengthens the dollar against emerging-market currencies."},
    {"title": "The current account", "topic": "Economics",
     "match": [r"current account", r"trade deficit", r"trade balance", r"\bcad\b", r"balance of payments",
               r"\bbop\b"],
     "explain": "The current account adds up trade in goods and services plus income flows. A deficit has to be "
                "financed by foreign money coming in: FDI, portfolio flows or loans.",
     "angle": "Current account = (private saving − investment) + (taxes − government spending). A deficit means "
              "the country invests more than it saves."},
    {"title": "Fiscal deficits & crowding out", "topic": "Economics",
     "match": [r"fiscal deficit", r"government borrowing", r"\bbudget\b", r"tax revenue", r"gst collection",
               r"\bsubsid(y|ies)\b"],
     "explain": "The fiscal deficit is how much more the government spends than it earns. Financing it means "
                "issuing bonds, which competes with companies for the same pool of savings.",
     "angle": "Crowding out: heavy government borrowing pushes up interest rates and can squeeze private "
              "investment."},
    {"title": "Tariffs & trade", "topic": "Economics",
     "match": [r"\btariffs?\b", r"trade (deal|war|pact|pacts|talks)", r"\bftas?\b", r"import dut(y|ies)",
               r"export ban", r"anti-dumping"],
     "explain": "A tariff is a tax on imports. It shields domestic producers but raises prices for consumers and "
                "often invites retaliation.",
     "angle": "For a small importing country a tariff creates a deadweight loss: consumers lose more than "
              "producers and the government gain."},
    {"title": "Foreign portfolio flows", "topic": "Economics",
     "match": [r"\bfpis?\b", r"\bfiis?\b", r"foreign (portfolio )?investors?", r"\boutflows?\b"],
     "explain": "Foreign portfolio investors buy Indian shares and bonds through the market. Because they can sell "
                "quickly, their exits can hit the Nifty and the rupee at the same time.",
     "angle": "Portfolio investment is liquid, fast-moving money; FDI is a lasting ownership stake of 10% or more "
              "of voting power."},
    {"title": "Inflation & price indexes", "topic": "Economics",
     "match": [r"\binflation\b", r"\bcpi\b", r"\bwpi\b", r"price rise"],
     "explain": "Consumer price inflation measures how fast the cost of a typical household basket is rising. "
                "Stubborn inflation keeps central banks from cutting rates.",
     "angle": "Price indexes tend to overstate true cost-of-living changes because of substitution, quality and "
              "new-product biases."},
    {"title": "GDP & the business cycle", "topic": "Economics",
     "match": [r"\bgdp\b", r"growth (forecast|outlook|rate)", r"\brecession\b", r"\bslowdown\b", r"\bpmi\b"],
     "explain": "GDP measures everything an economy produces. Upgrades and downgrades to growth forecasts feed "
                "straight into expectations for company earnings.",
     "angle": "GDP = C + I + G + (X − M). A PMI above 50 signals expansion and works as a leading indicator."},
    {"title": "Jobs & the labour market", "topic": "Economics",
     "match": [r"jobs report", r"\bpayrolls\b", r"\bunemployment\b", r"\bhiring\b", r"\blayoffs?\b",
               r"job cuts", r"seasonal jobs"],
     "explain": "Jobs data tell central banks whether an economy is running hot. Strong hiring can push up wages "
                "and inflation; layoffs signal slowing demand.",
     "angle": "Unemployment can be frictional, structural or cyclical; only cyclical unemployment moves with the "
              "business cycle."},
    {"title": "IPOs & the grey market", "topic": "Equity Investments",
     "match": [r"\bipos?\b", r"\bgmp\b", r"\bdrhp\b", r"\blists?\b.{0,30}\b(premium|discount)\b", r"\blisting\b"],
     "explain": "An IPO is a company's first sale of shares to the public. The grey-market premium is an "
                "unofficial pre-listing price signal, not a promise of listing gains.",
     "angle": "Primary market: the company raises new capital. Secondary market: investors trade existing shares "
              "and the company receives nothing."},
    {"title": "Regulation & investor protection", "topic": "Ethics",
     "match": [r"\bsebi\b", r"\birdai\b", r"\bregulator", r"\bpms\b", r"portfolio manag", r"\baifs?\b",
               r"mis-?selling"],
     "explain": "Regulators like SEBI and IRDAI set the rules for how financial products are sold and who may "
                "invest in what, with protecting investors as the goal.",
     "angle": "CFA Standard III(C), Suitability: understand a client's objectives and constraints before "
              "recommending an investment."},
    {"title": "Mergers & synergies", "topic": "Corporate Issuers",
     "match": [r"\bacqui(re|res|red|ring|sition|sitions)\b", r"\bmergers?\b", r"\btakeover\b",
               r"\bbuys? .{0,30}\bstake\b", r"\bstake (sale|in)\b"],
     "explain": "In an acquisition, the buyer is betting the combined business is worth more than what it pays, "
                "through cost savings or new growth: the synergies.",
     "angle": "Synergy = V(combined) − [V(acquirer) + V(target)]. Pay a premium bigger than the synergies and the "
              "value goes to the target's shareholders."},
    {"title": "Buybacks & dividends", "topic": "Corporate Issuers",
     "match": [r"\bbuy-?backs?\b", r"\bdividends?\b", r"\bpayouts?\b"],
     "explain": "Companies return spare cash through dividends or buybacks. A buyback shrinks the share count, "
                "spreading profits over fewer shares.",
     "angle": "A debt-financed buyback lifts EPS only if the earnings yield (E/P) is higher than the after-tax "
              "cost of debt."},
    {"title": "Credit risk & ratings", "topic": "Fixed Income",
     "match": [r"credit rating", r"\bdowngrade", r"\bupgrade", r"moody'?s", r"\bfitch\b", r"\bdefaults?\b",
               r"credit spreads?"],
     "explain": "Credit ratings grade a borrower's ability to repay. A downgrade raises its borrowing costs, and "
                "bondholders lose if it defaults.",
     "angle": "Expected loss = probability of default × loss given default. The credit spread pays for expected "
              "loss plus liquidity and uncertainty."},
    {"title": "Bad loans & bank health", "topic": "Financial Statement Analysis",
     "match": [r"\bnpas?\b", r"bad loans", r"asset quality", r"\bprovisions?\b", r"\bunderwriting\b"],
     "explain": "A loan becomes a non-performing asset when repayments stop for 90 days. Banks then set aside "
                "provisions, which come straight out of reported profit.",
     "angle": "Provisions are an income-statement expense and shrink the net loan book on the balance sheet; "
              "watch the provision coverage ratio."},
    {"title": "Futures, options & F&O", "topic": "Derivatives",
     "match": [r"\bf&o\b", r"\bfutures\b", r"\bderivatives?\b", r"options (trading|traders|market|expiry)",
               r"\bexpiry\b"],
     "explain": "Futures lock in a price today for a trade later; options give the right but not the "
                "obligation. SEBI studies found about 9 in 10 individual F&O traders lose money.",
     "angle": "Put–call parity: S + P = C + PV(X). A long call plus a short put at the same strike behaves like "
              "a long forward."},
    {"title": "Gold as a safe haven", "topic": "Alternative Investments",
     "match": [r"\bgold\b", r"\bsilver\b", r"\bbullion\b", r"safe haven"],
     "explain": "Gold pays no interest, so it tends to shine when real interest rates fall or fear rises, and to "
                "lose its lustre when real yields climb.",
     "angle": "Commodity futures return = spot price return + roll return + collateral return."},
    {"title": "Mutual funds, SIPs & ETFs", "topic": "Portfolio Management",
     "match": [r"mutual funds?", r"\bsips?\b", r"\betfs?\b", r"\bamcs?\b", r"index funds?", r"\bnfos?\b"],
     "explain": "Funds pool many investors' money into diversified portfolios. A SIP invests a fixed amount each "
                "month, automatically buying more units when prices are low.",
     "angle": "With a SIP the average cost per unit is the harmonic mean of the prices paid, which is never above "
              "their arithmetic mean."},
    {"title": "Venture capital & startup valuations", "topic": "Alternative Investments",
     "match": [r"\bfunding\b", r"series [a-f]\b", r"\bunicorns?\b", r"venture capital", r"private equity",
               r"\braises? \$"],
     "explain": "Startups raise money in rounds, and each round sets a price for the whole company. A unicorn is "
                "a private company valued above $1 billion.",
     "angle": "Post-money valuation = pre-money valuation + new investment. VC returns come from a few big "
              "winners that make up for many failures."},
    {"title": "How insurers make money", "topic": "Portfolio Management",
     "match": [r"\binsur(ance|er|ers)\b"],
     "explain": "Insurers collect premiums up front and pay claims later, investing the float in between. "
                "Commissions to agents and distributors are one of their biggest costs.",
     "angle": "Insurers are liability-driven investors: they match the timing of their assets to expected claims."},
    {"title": "Payments & network effects", "topic": "Equity Investments",
     "match": [r"\bupi\b", r"\bmdr\b", r"\bnpci\b", r"digital payments?"],
     "explain": "UPI moves money instantly between bank accounts. The MDR is the fee a merchant pays on each "
                "digital payment, and who bears it shapes how widely it is used.",
     "angle": "Network effects: each new user makes a platform more valuable to every existing user, which can "
              "build a strong competitive moat."},
    {"title": "Who really pays a tax", "topic": "Economics",
     "match": [r"\bgst\b", r"income tax", r"\btds\b", r"tax (cut|cuts|hike|relief)", r"customs duty"],
     "explain": "Taxes change behaviour: cuts leave more money to spend or invest, while indirect taxes like GST "
                "are ultimately paid through prices.",
     "angle": "Tax incidence depends on elasticity: the less price-sensitive side of the market carries more of "
              "the burden."},
    {"title": "Capex & project returns", "topic": "Corporate Issuers",
     "match": [r"\bcapex\b", r"\bcapacity\b", r"\bplant\b", r"\bfactory\b", r"data cent(re|er)s?",
               r"\bexpansion\b", r"\binvest(s|ment)? .{0,30}(crore|billion|trillion)"],
     "explain": "Capital expenditure is money spent on long-lived assets like plants or data centres. It creates "
                "value only if its returns beat the cost of the money used.",
     "angle": "Accept projects with NPV > 0, which for conventional cash flows means IRR > the cost of capital "
              "(WACC)."},
    {"title": "Oil prices & India's economy", "topic": "Alternative Investments",
     "match": [r"\bcrude\b", r"\bbrent\b", r"\boil prices?\b", r"\bopec\b", r"\bhormuz\b"],
     "explain": "India imports most of the oil it uses, so pricier crude widens the trade deficit, weakens the "
                "rupee and pushes up inflation, all at once.",
     "angle": "Backwardation (spot above futures) signals tight near-term supply; contango (futures above spot) "
              "signals plentiful supply and storage costs."},
    {"title": "The rupee & exchange rates", "topic": "Economics",
     "match": [r"\brupee\b", r"\bforex\b", r"usd/inr", r"\bcurrenc(y|ies)\b"],
     "explain": "A weaker rupee means more rupees per dollar. Imports like oil and electronics get costlier, but "
                "exporters such as IT services earn more in rupee terms.",
     "angle": "Marshall–Lerner: a depreciation improves the trade balance only if export and import demand "
              "elasticities together exceed 1, and often only after a J-curve delay."},
    {"title": "Bond prices & yields", "topic": "Fixed Income",
     "match": [r"\byields?\b", r"\btreasur(y|ies)\b", r"\bbonds?\b", r"\bg-?secs?\b", r"\bgilts?\b"],
     "explain": "A bond's price and its yield move in opposite directions. When market yields rise, existing "
                "bonds paying lower coupons are worth less, so their prices fall.",
     "angle": "%ΔPrice ≈ −ModDur × Δyield + ½ × Convexity × (Δyield)². Longer-duration bonds fall more when "
              "yields rise."},
    {"title": "Valuation & the P/E ratio", "topic": "Equity Investments",
     "match": [r"\bvaluation\b", r"target price", r"price target", r"\bre-?rating\b", r"\bp/e\b", r"market cap",
               r"valued at", r"most valued"],
     "explain": "A P/E tells you how many rupees investors pay for each rupee of earnings. A high P/E means the "
                "market expects fast growth ahead.",
     "angle": "Justified forward P/E = (D₁/E₁) ÷ (r − g): it rises with growth and payout, and falls as the "
              "required return rises."},
    {"title": "Earnings & margins", "topic": "Financial Statement Analysis",
     "match": [r"\bq[1-4]\b", r"\bresults\b", r"net profit", r"\bprofits?\b", r"\bearnings\b", r"\brevenue\b",
               r"\bebitda\b", r"\bmargins?\b"],
     "explain": "Results show whether a company is growing profits, not just sales. Margins show how much of each "
                "rupee of revenue survives as profit.",
     "angle": "Gross margin → operating margin (EBIT) → net margin. EBITDA adds back depreciation and "
              "amortisation, which are non-cash expenses."},
    {"title": "Market efficiency", "topic": "Equity Investments",
     "match": [r"\bshares? (jump|jumps|surge|surges|soar|soars|rise|rises|fall|falls|slump|slumps|plunge|plunges|"
               r"tumble|tumbles|crash|crashes|sink|sinks)\b", r"\bstocks? (jump|surge|fall|tumble|crash|plunge|sink)",
               r"\brall(y|ies)\b"],
     "explain": "Share prices react within minutes of news because traders race to price in new information.",
     "angle": "Semi-strong-form efficiency: prices already reflect all public information, so trading on "
              "headlines alone shouldn't earn excess returns."},
    {"title": "Systematic risk", "topic": "Portfolio Management",
     "match": [r"\bsell-?off\b", r"\brout\b", r"\bvolatil", r"\bvix\b", r"\bcrash\b", r"\bslumps?\b"],
     "explain": "In a broad sell-off most stocks fall together: a market-wide shock is a risk you can't diversify "
                "away by owning more shares.",
     "angle": "Only systematic risk (beta) earns a premium in CAPM: E(R) = Rf + β × [E(Rm) − Rf]."},
]
for _c in CONCEPTS:
    _c["rx"] = re.compile("|".join(_c["match"]), re.I)


def pick(stories, n=3):
    """stories: [(title, dek, story)] ranked best first -> [(concept, story)], distinct concepts,
    trying for distinct topic areas first."""
    chosen, used, topics, seen = [], set(), set(), set()
    for strict in (True, False):
        for title, dek, story in stories:
            if len(chosen) == n:
                return chosen
            if id(story) in seen:
                continue
            text = f"{title} {dek}"
            for c in CONCEPTS:
                if c["title"] in used or (strict and c["topic"] in topics):
                    continue
                if c["rx"].search(text):
                    chosen.append((c, story))
                    used.add(c["title"])
                    topics.add(c["topic"])
                    seen.add(id(story))
                    break
    return chosen
