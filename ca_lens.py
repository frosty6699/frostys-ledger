"""The CA Lens: pairs the day's stories with the CA concept they illustrate, tagged by ICAI paper
(new scheme). Same matching as the CFA Lens: specific concepts first, general last."""
import re

from cfa_lens import pick as _pick

CONCEPTS = [
    {"title": "Transfer pricing & tax treaties", "topic": "CA Final · Direct Tax & International Taxation",
     "match": [r"transfer pricing", r"\bdtaa\b", r"tax treat(y|ies)", r"global minimum tax", r"\bpillar two\b"],
     "explain": "When group companies in different countries deal with each other, tax authorities check that the "
                "price is what independent parties would have agreed, so profits can't be shifted to low-tax places.",
     "angle": "Arm's length price: transactions between associated enterprises are priced as if between strangers. "
              "Treaties relieve double taxation through the exemption or the credit method."},
    {"title": "Customs & anti-dumping duty", "topic": "CA Final · Indirect Tax Laws",
     "match": [r"customs dut(y|ies)", r"import dut(y|ies)", r"anti-dumping", r"safeguard duty", r"\btariffs?\b"],
     "explain": "Goods coming into India pay basic customs duty plus IGST. Anti-dumping and safeguard duties are "
                "extra levies that protect local producers from unfairly cheap imports.",
     "angle": "IGST paid on imports is available as input tax credit; basic customs duty is not, so it becomes "
              "part of the cost of the goods."},
    {"title": "GST & input tax credit", "topic": "CA Inter · Taxation",
     "match": [r"\bgst\b", r"input tax credit", r"\bitc\b"],
     "explain": "GST is collected at every stage of the supply chain, but each business claims credit for the GST "
                "it paid on its inputs, so tax falls only on the value it adds.",
     "angle": "Credit needs a valid tax invoice, receipt of the goods or services, the tax actually paid to the "
              "government by the supplier, and the buyer's return filed."},
    {"title": "TDS: tax deducted at source", "topic": "CA Inter · Taxation",
     "match": [r"\btds\b", r"\btcs\b", r"tax deducted at source"],
     "explain": "The payer deducts tax when making certain payments (salary, rent, fees, property purchases from "
                "NRIs) and deposits it with the government; the payee claims it as credit against their tax bill.",
     "angle": "Miss the deduction and the payer faces interest and can lose the deduction for that expense, on top "
              "of being treated as an assessee in default."},
    {"title": "Frauds & the auditor's duty", "topic": "CA Final · Advanced Auditing & Ethics",
     "match": [r"\bfraud", r"embezzl", r"\bbriber", r"whistle-?blower", r"forensic audit", r"\bscam\b"],
     "explain": "Auditors don't guarantee that no fraud exists, but they must plan the audit so that material "
                "misstatement from fraud has a reasonable chance of being found.",
     "angle": "SA 240: reasonable, not absolute, assurance. Under Section 143(12) of the Companies Act, the auditor "
              "must report suspected fraud above ₹1 crore to the Central Government."},
    {"title": "The auditor's opinion", "topic": "CA Inter · Auditing & Ethics",
     "match": [r"\bauditors?\b", r"\baudit\b", r"\bnfra\b", r"qualified opinion", r"going concern"],
     "explain": "A statutory auditor tells shareholders whether the financial statements give a true and fair view. "
                "NFRA oversees the auditors of listed and large companies.",
     "angle": "SA 700/705: an unmodified opinion, or a modified one: qualified, adverse or a disclaimer of opinion."},
    {"title": "Related-party transactions", "topic": "CA Final · Financial Reporting",
     "match": [r"related[- ]party", r"\bpromoters?\b", r"\btrusts?\b.{0,40}\b(sons|board|stake)\b",
               r"group compan"],
     "explain": "Deals between a company and its promoters, directors or group firms can move value out of minority "
                "shareholders' hands, so the law makes them disclose and seek approval.",
     "angle": "Ind AS 24 requires the relationships and transactions to be disclosed. Section 188 of the Companies "
              "Act needs board approval, and shareholders' approval above the thresholds."},
    {"title": "Boards & independent directors", "topic": "CA Inter · Corporate & Other Laws",
     "match": [r"(?<!sebi )(?<!rbi )(?<!irdai )\bboard\b", r"\bchairman\b", r"\bchairperson\b", r"independent director", r"reappoint",
               r"\bagm\b", r"\bmanaging director\b"],
     "explain": "The board is accountable to shareholders for running the company. Independent directors are meant "
                "to protect minority shareholders when management and promoters disagree with them.",
     "angle": "Section 149 of the Companies Act: listed public companies need at least one-third independent "
              "directors on their board."},
    {"title": "Goodwill in business combinations", "topic": "CA Final · Financial Reporting",
     "match": [r"\bacqui(re|res|red|ring|sition|sitions)\b", r"\bmergers?\b", r"\btakeover\b", r"\bamalgamat"],
     "explain": "When one company buys another, it books the target's assets and liabilities at fair value. "
                "Whatever it paid above that becomes goodwill on its balance sheet.",
     "angle": "Ind AS 103: goodwill is not amortised but tested for impairment every year (Ind AS 36). A bargain "
              "purchase gain goes to OCI and capital reserve under the Indian carve-out."},
    {"title": "Control, subsidiaries & JVs", "topic": "CA Final · Financial Reporting",
     "match": [r"\bstake\b", r"\bsubsidiar", r"joint venture", r"\bjv\b", r"\bholding company\b"],
     "explain": "Buying a stake can make the target a subsidiary, a joint venture or just an investment, and each "
                "is accounted for very differently in the buyer's consolidated statements.",
     "angle": "Ind AS 110 control = power over the investee + exposure to variable returns + the ability to use "
              "that power. Joint ventures use the equity method (Ind AS 28)."},
    {"title": "Expected credit loss", "topic": "CA Final · Financial Reporting",
     "match": [r"\bnpas?\b", r"bad loans", r"asset quality", r"\bprovisions?\b", r"\bprovisioning\b",
               r"\bunderwriting\b", r"\bdefaults?\b"],
     "explain": "Under Ind AS 109 lenders set aside money for losses they expect, not just for loans that have "
                "already gone bad, so provisions rise as soon as risk rises.",
     "angle": "Three stages: 12-month ECL (stage 1); lifetime ECL once credit risk rises significantly (stage 2); "
              "lifetime ECL with interest on the net amount for credit-impaired loans (stage 3)."},
    {"title": "Leases on the balance sheet", "topic": "CA Final · Financial Reporting",
     "match": [r"\bleas(e|es|ing)\b", r"\baircraft\b", r"\bairlines?\b"],
     "explain": "Airlines, retailers and hotels lease much of what they use. Under Ind AS 116 almost every lease "
                "shows up as an asset and a matching liability.",
     "angle": "The lessee books a right-of-use asset and a lease liability. Rent becomes depreciation plus "
              "interest, which lifts EBITDA."},
    {"title": "Dividends & buybacks under the Act", "topic": "CA Inter · Corporate & Other Laws",
     "match": [r"\bbuy-?backs?\b", r"\bdividends?\b"],
     "explain": "Company law limits how much cash a company can hand back, to protect its creditors.",
     "angle": "Dividends only out of profits (Section 123). Buybacks capped at 25% of paid-up capital and free "
              "reserves, with debt no more than twice capital and free reserves afterwards (Section 68)."},
    {"title": "Share issue costs in an IPO", "topic": "CA Final · Financial Reporting",
     "match": [r"\bipos?\b", r"\bdrhp\b", r"\bqip\b", r"rights issue", r"\blisting\b"],
     "explain": "An IPO's costs — bankers, lawyers, listing fees — don't all hit the profit and loss account.",
     "angle": "Ind AS 32: costs of issuing new shares are deducted from equity; costs of listing existing shares "
              "are expensed. Shared costs are split between the two."},
    {"title": "Hedging currency risk", "topic": "CA Final · Advanced Financial Management",
     "match": [r"\brupee\b", r"\bforex\b", r"\bhedg", r"\bcurrenc(y|ies)\b", r"usd/inr"],
     "explain": "Exporters and importers use forward contracts to lock in today the exchange rate they'll get when "
                "the payment actually arrives.",
     "angle": "Forward rate by interest rate parity (direct quote): F = S × (1 + r_home) ÷ (1 + r_foreign). "
              "An exporter hedges by selling dollars forward."},
    {"title": "Financial leverage", "topic": "CA Inter · Financial Management",
     "match": [r"\bncds?\b", r"debt (fund)?rais", r"raise[sd]? .{0,25}\b(debt|loans?)\b", r"\bborrowings\b",
               r"(bond|debt) issue", r"\bleverage"],
     "explain": "Borrowing lets a company grow without issuing new shares, but interest has to be paid in good "
                "years and bad, which makes profits swing more.",
     "angle": "Degree of financial leverage = EBIT ÷ (EBIT − interest). Debt magnifies changes in EPS in both "
              "directions."},
    {"title": "Capital budgeting", "topic": "CA Inter · Financial Management",
     "match": [r"\bcapex\b", r"\bcapacity\b", r"\bplant\b", r"\bfactory\b", r"data cent(re|er)s?",
               r"\bexpansion\b", r"\binvest(s|ment)? .{0,30}(crore|billion|trillion)"],
     "explain": "A new plant or data centre is only worth building if the cash it throws off, discounted at the cost "
                "of capital, beats what it costs.",
     "angle": "Accept if NPV > 0. Payback period is simple but ignores the time value of money and cash flows after "
              "payback."},
    {"title": "Contribution & break-even", "topic": "CA Inter · Cost & Management Accounting",
     "match": [r"\bmargins?\b", r"input costs?", r"raw material", r"\bcost pressure", r"price (hike|cut)s?",
               r"costs? (rise|rises|surge|pressure)"],
     "explain": "When input costs rise faster than prices, each unit sold contributes less towards fixed costs, "
                "so profit falls faster than sales.",
     "angle": "Contribution = sales − variable costs. Break-even units = fixed costs ÷ contribution per unit; "
              "P/V ratio = contribution ÷ sales."},
    {"title": "Revenue recognition", "topic": "CA Final · Financial Reporting",
     "match": [r"\brevenue\b", r"\bresults\b", r"\bq[1-4]\b", r"order book", r"\borders?\b", r"\bcontracts?\b"],
     "explain": "Revenue is recognised when control of goods or services passes to the customer, not simply when "
                "cash arrives or a contract is signed.",
     "angle": "Ind AS 115's five steps: identify the contract → performance obligations → transaction price → "
              "allocate it → recognise as each obligation is satisfied."},
    {"title": "How income is taxed", "topic": "CA Inter · Taxation",
     "match": [r"income tax", r"\btax (cut|cuts|relief|slabs?)\b", r"new tax regime", r"\bbudget\b"],
     "explain": "Income is grouped under heads like salary, business and capital gains, added up, reduced by "
                "allowed deductions, and taxed at slab rates.",
     "angle": "Gross total income → deductions → total income → tax at slab rates → rebate, surcharge and cess. "
              "Under the new regime, most deductions are given up for lower rates."},
]
for _c in CONCEPTS:
    _c["rx"] = re.compile("|".join(_c["match"]), re.I)


def pick(stories, n=3, exclude=()):
    return _pick(stories, n, CONCEPTS, exclude)
