"""Curated advertising/marketing compliance rules for social content, by
industry and region (US, UAE/GCC, India - the markets our users are in).

This file is the ONLY source of rules: the LLM classifies a business into an
INDUSTRIES key (agents/website_analysis_agent.py) and applicable_rules() looks
rules up deterministically - the model never writes or paraphrases law. Each
rule cites its source and carries a review status; every rule here starts as
"draft" and must be reviewed by legal counsel before being marked "reviewed".
Changing a rule here takes effect for every user at once - profiles store
only the industry, regions, and any rules the user marked not applicable.

Rule fields:
  id               stable identifier (stored in user profiles - never rename)
  framework        law/regulation/code name
  authority        who issues/enforces it
  regions          subset of REGIONS
  industries       INDUSTRIES keys, or ["*"] for every industry
  severity         "high" (legal exposure) / "medium" / "low" (best practice)
  summary          one line, shown to the user
  content_rules    what generated content must / must not do
  required_disclaimer  exact text to include when the rule is triggered, or None
  source_url       primary source
  last_reviewed    date the entry was last checked against the source
  review_status    "draft" (not yet legally reviewed) or "reviewed"

Guidance only - not legal advice.
"""

RULES_VERSION = "2026-09-28"
REGIONS = ("US", "UAE/GCC", "India")

# Fixed taxonomy the website analysis must pick from. Categories without
# industry-specific rules below still get every "*" rule for their regions.
INDUSTRIES = {
    "healthcare": "Healthcare providers (hospitals, clinics, doctors, dental, wellness clinics)",
    "pharma_medical_devices": "Pharmaceuticals & medical devices",
    "financial_services": "Banking, lending, payments & fintech",
    "investment_wealth": "Investment, wealth management, brokerage & mutual funds",
    "insurance": "Insurance",
    "crypto_digital_assets": "Crypto & virtual digital assets",
    "legal_services": "Legal services & law firms",
    "real_estate": "Real estate & property",
    "alcohol": "Alcoholic beverages",
    "gambling_gaming": "Gambling, betting & real-money gaming",
    "supplements_food": "Food, beverages & dietary supplements",
    "education": "Education & training",
    "kids_products": "Products or services for children",
    "employment_recruiting": "Recruiting, staffing & HR (job advertising)",
    "technology_saas": "Technology, software & SaaS",
    "retail_ecommerce": "Retail & e-commerce",
    "logistics_transport": "Logistics, freight & transportation",
    "professional_services": "Professional & business services",
    "hospitality_travel": "Hospitality, travel & food service",
    "manufacturing_industrial": "Manufacturing & industrial",
    "nonprofit": "Non-profit & social causes",
    "general": "Other / general business",
}

# schema.org @types a site declares about itself -> industry. A strong,
# site-provided signal used to confirm or correct the LLM's classification.
SCHEMA_TYPE_INDUSTRIES = {
    "MedicalOrganization": "healthcare",
    "Hospital": "healthcare",
    "MedicalClinic": "healthcare",
    "Physician": "healthcare",
    "Dentist": "healthcare",
    "DiagnosticLab": "healthcare",
    "Pharmacy": "pharma_medical_devices",
    "Drug": "pharma_medical_devices",
    "MedicalDevice": "pharma_medical_devices",
    "BankOrCreditUnion": "financial_services",
    "FinancialService": "financial_services",
    "LoanOrCredit": "financial_services",
    "BankAccount": "financial_services",
    "InvestmentFund": "investment_wealth",
    "InvestmentOrDeposit": "investment_wealth",
    "InsuranceAgency": "insurance",
    "LegalService": "legal_services",
    "Attorney": "legal_services",
    "RealEstateAgent": "real_estate",
    "RealEstateListing": "real_estate",
    "Winery": "alcohol",
    "Brewery": "alcohol",
    "Distillery": "alcohol",
    "Casino": "gambling_gaming",
    "EducationalOrganization": "education",
    "CollegeOrUniversity": "education",
    "School": "education",
    "Course": "education",
    "EmploymentAgency": "employment_recruiting",
    "JobPosting": "employment_recruiting",
    "SoftwareApplication": "technology_saas",
    "Store": "retail_ecommerce",
    "OnlineStore": "retail_ecommerce",
    "Restaurant": "hospitality_travel",
    "Hotel": "hospitality_travel",
    "LodgingBusiness": "hospitality_travel",
    "TravelAgency": "hospitality_travel",
    "NGO": "nonprofit",
}

_DRAFT = {"last_reviewed": RULES_VERSION, "review_status": "draft"}

RULES = [
    # ── All industries ──────────────────────────────────────────────────────
    {
        "id": "us-ftc-endorsements",
        "framework": "FTC Endorsement Guides (16 CFR Part 255)",
        "authority": "U.S. Federal Trade Commission",
        "regions": ["US"],
        "industries": ["*"],
        "severity": "high",
        "summary": "Paid, gifted or employee endorsements must clearly disclose the relationship.",
        "content_rules": [
            "Label sponsored, gifted or affiliate content clearly (e.g. #ad, 'Paid partnership') at the start, not buried in hashtags.",
            "Testimonials must reflect typical results, or clearly state what results people can generally expect.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.ftc.gov/business-guidance/resources/ftcs-endorsement-guides-what-people-are-asking",
        **_DRAFT,
    },
    {
        "id": "us-ftc-truthful-advertising",
        "framework": "FTC Act Section 5 & Trade Regulation Rule on Consumer Reviews and Testimonials (16 CFR Part 465)",
        "authority": "U.S. Federal Trade Commission",
        "regions": ["US"],
        "industries": ["*"],
        "severity": "high",
        "summary": "Claims must be truthful and substantiated; fake or incentivized-without-disclosure reviews are prohibited.",
        "content_rules": [
            "Only make objective claims (numbers, 'fastest', 'best') the company can substantiate.",
            "Never fabricate reviews, testimonials or statistics.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.ftc.gov/business-guidance/advertising-marketing",
        **_DRAFT,
    },
    {
        "id": "uae-media-law-advertising",
        "framework": "Federal Decree-Law No. 55 of 2023 on Media Regulation & UAE Media Council advertising standards",
        "authority": "UAE Media Council",
        "regions": ["UAE/GCC"],
        "industries": ["*"],
        "severity": "high",
        "summary": "Ads must respect religious and national values, not mislead, and paid promotion needs the required advertiser permit.",
        "content_rules": [
            "Do not use content that offends religious beliefs, national symbols, public morals or UAE leadership.",
            "Clearly disclose paid promotion; influencers/advertisers promoting in the UAE need a UAE Media Council advertiser permit.",
            "Do not make misleading claims about products, prices or offers.",
        ],
        "required_disclaimer": None,
        "source_url": "https://uaemc.gov.ae/en",
        **_DRAFT,
    },
    {
        "id": "ksa-mawthooq",
        "framework": "Mawthooq advertiser licence (Saudi Arabia)",
        "authority": "General Authority for Media Regulation (GMedia), Saudi Arabia",
        "regions": ["UAE/GCC"],
        "industries": ["*"],
        "severity": "medium",
        "summary": "If promoting to Saudi audiences via influencers, they need a Mawthooq licence.",
        "content_rules": [
            "When content targets Saudi Arabia and is published by an influencer, confirm the influencer holds a Mawthooq licence.",
        ],
        "required_disclaimer": None,
        "source_url": "https://gmedia.gov.sa",
        **_DRAFT,
    },
    {
        "id": "uae-pdpl",
        "framework": "Federal Decree-Law No. 45 of 2021 on Personal Data Protection (PDPL)",
        "authority": "UAE Data Office",
        "regions": ["UAE/GCC"],
        "industries": ["*"],
        "severity": "medium",
        "summary": "Lead-gen and contests that collect personal data need clear consent and purpose.",
        "content_rules": [
            "Posts that collect personal data (sign-ups, contests, lead forms) must link to a privacy notice and state the purpose.",
        ],
        "required_disclaimer": None,
        "source_url": "https://u.ae/en/about-the-uae/digital-uae/data/data-protection-laws",
        **_DRAFT,
    },
    {
        "id": "in-asci-code",
        "framework": "ASCI Code for Self-Regulation of Advertising Content & ASCI Influencer Guidelines",
        "authority": "Advertising Standards Council of India",
        "regions": ["India"],
        "industries": ["*"],
        "severity": "high",
        "summary": "Ads must be honest and not misleading; influencer/paid content must carry a disclosure label.",
        "content_rules": [
            "Label paid or gifted influencer content upfront with #ad, #collab, #sponsored or #partnership.",
            "Do not make claims that cannot be substantiated or that exploit consumers' lack of knowledge.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.ascionline.in/the-asci-code/",
        **_DRAFT,
    },
    {
        "id": "in-ccpa-misleading-ads",
        "framework": "Consumer Protection Act 2019 & CCPA Guidelines for Prevention of Misleading Advertisements and Endorsements, 2022",
        "authority": "Central Consumer Protection Authority (India)",
        "regions": ["India"],
        "industries": ["*"],
        "severity": "high",
        "summary": "Misleading ads, bait ads and undisclosed endorsements are penalized; 'free' claims must be genuinely free.",
        "content_rules": [
            "Do not use bait advertising or 'free' claims with hidden conditions.",
            "Endorsers must have actually used the product; disclose material connections.",
        ],
        "required_disclaimer": None,
        "source_url": "https://consumeraffairs.nic.in/",
        **_DRAFT,
    },
    {
        "id": "in-dpdp",
        "framework": "Digital Personal Data Protection Act, 2023",
        "authority": "Ministry of Electronics & IT (India)",
        "regions": ["India"],
        "industries": ["*"],
        "severity": "medium",
        "summary": "Collecting personal data through posts (forms, contests) needs notice and consent.",
        "content_rules": [
            "Posts that collect personal data must link to a notice explaining the purpose and obtain consent.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.meity.gov.in/data-protection-framework",
        **_DRAFT,
    },
    # ── Healthcare ──────────────────────────────────────────────────────────
    {
        "id": "us-hipaa-marketing",
        "framework": "HIPAA Privacy Rule - marketing & protected health information",
        "authority": "U.S. HHS Office for Civil Rights",
        "regions": ["US"],
        "industries": ["healthcare"],
        "severity": "high",
        "summary": "Never disclose patient information; patient stories or photos need a signed HIPAA authorization.",
        "content_rules": [
            "Never mention, confirm or imply that any identifiable person is a patient.",
            "Patient testimonials, names, photos or case details require written HIPAA authorization - do not generate them otherwise.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.hhs.gov/hipaa/for-professionals/privacy/guidance/marketing/index.html",
        **_DRAFT,
    },
    {
        "id": "us-ftc-health-claims",
        "framework": "FTC Health Products Compliance Guidance (2022)",
        "authority": "U.S. Federal Trade Commission",
        "regions": ["US"],
        "industries": ["healthcare", "pharma_medical_devices", "supplements_food"],
        "severity": "high",
        "summary": "Health benefit claims need competent and reliable scientific evidence.",
        "content_rules": [
            "Do not claim a product or treatment cures, prevents or treats a condition unless backed by rigorous evidence.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.ftc.gov/business-guidance/resources/health-products-compliance-guidance",
        **_DRAFT,
    },
    {
        "id": "health-no-guaranteed-outcomes",
        "framework": "General medical advertising principles",
        "authority": "Medical regulators & advertising codes (all regions)",
        "regions": ["US", "UAE/GCC", "India"],
        "industries": ["healthcare"],
        "severity": "medium",
        "summary": "No guaranteed outcomes, no fear-based messaging, no misleading before/after imagery.",
        "content_rules": [
            "Never promise or guarantee treatment outcomes.",
            "Avoid before/after images and fear-based messaging.",
            "Point readers to a qualified professional for personal medical advice.",
        ],
        "required_disclaimer": "This content is for general information only and is not a substitute for professional medical advice.",
        "source_url": "https://www.ftc.gov/business-guidance/resources/health-products-compliance-guidance",
        **_DRAFT,
    },
    {
        "id": "uae-health-ad-approval",
        "framework": "Health advertisement permits (MOHAP / DHA / DoH Abu Dhabi)",
        "authority": "UAE Ministry of Health & Prevention; Dubai Health Authority; Department of Health - Abu Dhabi",
        "regions": ["UAE/GCC"],
        "industries": ["healthcare", "pharma_medical_devices", "supplements_food"],
        "severity": "high",
        "summary": "Health-related ads in the UAE need prior approval from the relevant health authority.",
        "content_rules": [
            "Treat health, medical and health-product posts as requiring a MOHAP/DHA/DoH advertising permit before publishing.",
            "Do not make medical claims beyond what the approved permit covers.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.dha.gov.ae/en",
        **_DRAFT,
    },
    {
        "id": "in-drugs-magic-remedies",
        "framework": "Drugs and Magic Remedies (Objectionable Advertisements) Act, 1954",
        "authority": "Government of India",
        "regions": ["India"],
        "industries": ["healthcare", "pharma_medical_devices", "supplements_food"],
        "severity": "high",
        "summary": "Ads may not claim to cure or treat the diseases listed in the Act, or promote 'magic remedies'.",
        "content_rules": [
            "Do not claim a product or treatment cures or prevents diseases listed in the Act's schedule (e.g. diabetes, cancer, obesity).",
            "Do not advertise prescription drugs to the public.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.indiacode.nic.in/handle/123456789/1412",
        **_DRAFT,
    },
    # ── Pharma & medical devices ────────────────────────────────────────────
    {
        "id": "us-fda-rx-promotion",
        "framework": "FDA prescription drug advertising (21 CFR 202.1) & OPDP oversight",
        "authority": "U.S. Food and Drug Administration",
        "regions": ["US"],
        "industries": ["pharma_medical_devices"],
        "severity": "high",
        "summary": "Drug/device promotion must be fair-balanced (risks with benefits) and stay within approved labeling.",
        "content_rules": [
            "Present important risk information alongside any benefit claim (fair balance).",
            "Never promote off-label uses.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.fda.gov/drugs/office-prescription-drug-promotion",
        **_DRAFT,
    },
    # ── Investment & wealth ─────────────────────────────────────────────────
    {
        "id": "us-sec-marketing-rule",
        "framework": "SEC Marketing Rule (Rule 206(4)-1, Investment Advisers Act)",
        "authority": "U.S. Securities and Exchange Commission",
        "regions": ["US"],
        "industries": ["investment_wealth"],
        "severity": "high",
        "summary": "Testimonials, endorsements and performance claims carry strict disclosure and presentation rules.",
        "content_rules": [
            "Do not present investment performance without the required context and time periods.",
            "Testimonials/endorsements must disclose compensation and conflicts.",
            "Never promise or imply guaranteed returns.",
        ],
        "required_disclaimer": "Past performance is not a guarantee of future results. Investing involves risk, including possible loss of principal.",
        "source_url": "https://www.sec.gov/investment/marketing-faq",
        **_DRAFT,
    },
    {
        "id": "us-finra-2210",
        "framework": "FINRA Rule 2210 - Communications with the Public",
        "authority": "FINRA",
        "regions": ["US"],
        "industries": ["investment_wealth"],
        "severity": "high",
        "summary": "Broker-dealer communications must be fair, balanced, non-promissory, approved and retained.",
        "content_rules": [
            "Balance any benefit with the relevant risks; no exaggerated or promissory statements.",
            "Posts may require principal approval and must be retained as records.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/2210",
        **_DRAFT,
    },
    {
        "id": "in-sebi-amfi-advertising",
        "framework": "SEBI advertisement code (Mutual Funds, Investment Advisers, Research Analysts) & AMFI guidelines",
        "authority": "Securities and Exchange Board of India",
        "regions": ["India"],
        "industries": ["investment_wealth"],
        "severity": "high",
        "summary": "No assured returns; mutual fund ads carry the standard market-risk statement; show registration details.",
        "content_rules": [
            "Never promise assured or guaranteed returns.",
            "Show the SEBI registration number where the entity is a registered adviser/analyst.",
        ],
        "required_disclaimer": "Mutual Fund investments are subject to market risks, read all scheme related documents carefully.",
        "source_url": "https://www.sebi.gov.in/",
        **_DRAFT,
    },
    {
        "id": "uae-sca-financial-promotion",
        "framework": "Securities and Commodities Authority rules on financial promotion",
        "authority": "UAE Securities and Commodities Authority",
        "regions": ["UAE/GCC"],
        "industries": ["investment_wealth"],
        "severity": "high",
        "summary": "Only licensed entities may promote securities/investment products; no guaranteed returns.",
        "content_rules": [
            "Only promote investment products the company is licensed to offer in the UAE.",
            "Never promise guaranteed returns; state that investments carry risk.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.sca.gov.ae/en/home.aspx",
        **_DRAFT,
    },
    # ── Banking, lending & payments ─────────────────────────────────────────
    {
        "id": "us-consumer-credit-ads",
        "framework": "Truth in Lending Act (Regulation Z) advertising rules & CFPB UDAAP",
        "authority": "Consumer Financial Protection Bureau",
        "regions": ["US"],
        "industries": ["financial_services"],
        "severity": "high",
        "summary": "Credit ads that state rates or terms trigger APR and term disclosures; no unfair or deceptive practices.",
        "content_rules": [
            "If a post mentions a rate, payment amount or term ('trigger terms'), include the APR and key terms or omit the figures.",
            "FDIC-insured banks must follow FDIC official advertising statement rules.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.consumerfinance.gov/rules-policy/regulations/1026/24/",
        **_DRAFT,
    },
    {
        "id": "in-rbi-fair-practices",
        "framework": "RBI Fair Practices Code & Digital Lending Guidelines (2022)",
        "authority": "Reserve Bank of India",
        "regions": ["India"],
        "industries": ["financial_services"],
        "severity": "high",
        "summary": "Lending promotions must be transparent about costs and name the regulated lender.",
        "content_rules": [
            "State the all-in cost/APR when advertising loans; no hidden charges.",
            "Name the RBI-regulated entity behind any lending product.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.rbi.org.in/",
        **_DRAFT,
    },
    {
        "id": "uae-cbuae-consumer-protection",
        "framework": "CBUAE Consumer Protection Regulation & Standards",
        "authority": "Central Bank of the UAE",
        "regions": ["UAE/GCC"],
        "industries": ["financial_services", "insurance"],
        "severity": "high",
        "summary": "Financial product promotions must be clear, fair, not misleading, and disclose fees and rates.",
        "content_rules": [
            "Disclose applicable fees, rates and key terms; avoid 'free'/'zero' claims with conditions.",
            "Promote only products the company is licensed by CBUAE to offer.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.centralbank.ae/en/our-operations/consumer-protection/",
        **_DRAFT,
    },
    # ── Insurance ───────────────────────────────────────────────────────────
    {
        "id": "us-insurance-advertising",
        "framework": "State insurance advertising rules (NAIC model advertising regulations)",
        "authority": "State insurance departments / NAIC",
        "regions": ["US"],
        "industries": ["insurance"],
        "severity": "high",
        "summary": "Insurance ads must not overstate benefits or hide limitations and exclusions.",
        "content_rules": [
            "Do not describe coverage without mentioning material limitations or exclusions.",
            "Never use 'guaranteed' for benefits that are not guaranteed.",
        ],
        "required_disclaimer": None,
        "source_url": "https://content.naic.org/",
        **_DRAFT,
    },
    {
        "id": "in-irdai-advertising",
        "framework": "IRDAI regulations on insurance advertisements and disclosure",
        "authority": "Insurance Regulatory and Development Authority of India",
        "regions": ["India"],
        "industries": ["insurance"],
        "severity": "high",
        "summary": "Insurance ads must carry the insurer's registration details and the standard solicitation statement.",
        "content_rules": [
            "Include the insurer's IRDAI registration number and the product's UIN when a specific product is promoted.",
            "Do not misrepresent benefits or returns.",
        ],
        "required_disclaimer": "Insurance is the subject matter of solicitation. For more details on risk factors, terms and conditions, please read the sales brochure carefully before concluding a sale.",
        "source_url": "https://irdai.gov.in/",
        **_DRAFT,
    },
    # ── Crypto ──────────────────────────────────────────────────────────────
    {
        "id": "us-crypto-promotion",
        "framework": "SEC anti-touting (Securities Act Sec. 17(b)) & FTC deception rules for crypto promotion",
        "authority": "U.S. SEC / FTC",
        "regions": ["US"],
        "industries": ["crypto_digital_assets"],
        "severity": "high",
        "summary": "No guaranteed returns; paid promoters must disclose compensation.",
        "content_rules": [
            "Never promise returns or describe crypto as safe or risk-free.",
            "Disclose any compensation received for promoting a token or platform.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.sec.gov/oiea/investor-alerts-and-bulletins",
        **_DRAFT,
    },
    {
        "id": "in-asci-vda",
        "framework": "ASCI Guidelines for Advertising of Virtual Digital Assets (2022)",
        "authority": "Advertising Standards Council of India",
        "regions": ["India"],
        "industries": ["crypto_digital_assets"],
        "severity": "high",
        "summary": "VDA ads must carry the prescribed risk disclaimer and avoid 'safe investment' framing.",
        "content_rules": [
            "Include the prescribed disclaimer prominently.",
            "Do not call crypto products 'currency', 'securities' or 'safe'.",
        ],
        "required_disclaimer": "Crypto products and NFTs are unregulated and can be highly risky. There may be no regulatory recourse for any loss from such transactions.",
        "source_url": "https://www.ascionline.in/",
        **_DRAFT,
    },
    {
        "id": "uae-vara-marketing",
        "framework": "VARA Marketing, Advertising and Promotions Regulations (Dubai)",
        "authority": "Virtual Assets Regulatory Authority",
        "regions": ["UAE/GCC"],
        "industries": ["crypto_digital_assets"],
        "severity": "high",
        "summary": "Only VARA-licensed providers may market virtual assets in Dubai, with risk disclaimers.",
        "content_rules": [
            "Only promote virtual asset activities the company is licensed for.",
            "Include a clear risk warning; no guaranteed or 'risk-free' returns.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.vara.ae/",
        **_DRAFT,
    },
    # ── Legal services ──────────────────────────────────────────────────────
    {
        "id": "us-aba-attorney-advertising",
        "framework": "ABA Model Rules 7.1-7.3 and state bar advertising rules",
        "authority": "State bars (ABA model rules)",
        "regions": ["US"],
        "industries": ["legal_services"],
        "severity": "high",
        "summary": "No misleading claims or guaranteed results; many states require an 'Attorney Advertising' label.",
        "content_rules": [
            "Never guarantee case outcomes; disclaim that prior results do not guarantee similar outcomes when citing results.",
            "Label posts 'Attorney Advertising' where the state bar requires it.",
        ],
        "required_disclaimer": "Attorney Advertising. Prior results do not guarantee a similar outcome.",
        "source_url": "https://www.americanbar.org/groups/professional_responsibility/publications/model_rules_of_professional_conduct/",
        **_DRAFT,
    },
    {
        "id": "in-bci-rule-36",
        "framework": "Bar Council of India Rules, Rule 36",
        "authority": "Bar Council of India",
        "regions": ["India"],
        "industries": ["legal_services"],
        "severity": "high",
        "summary": "Advocates may not advertise or solicit work; only limited factual information is permitted.",
        "content_rules": [
            "Do not create promotional or solicitation content for advocates practising in India - limit posts to permitted factual information (name, contact, areas of practice).",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.barcouncilofindia.org/",
        **_DRAFT,
    },
    # ── Real estate ─────────────────────────────────────────────────────────
    {
        "id": "us-fair-housing",
        "framework": "Fair Housing Act, 42 U.S.C. 3604(c)",
        "authority": "U.S. Department of Housing and Urban Development",
        "regions": ["US"],
        "industries": ["real_estate"],
        "severity": "high",
        "summary": "Housing ads may not state or imply a preference based on protected characteristics.",
        "content_rules": [
            "Never describe ideal buyers/tenants by race, religion, sex, familial status, disability or national origin (e.g. 'perfect for young couples').",
            "Include the brokerage/licence disclosure required by the state.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.hud.gov/program_offices/fair_housing_equal_opp",
        **_DRAFT,
    },
    {
        "id": "in-rera-advertising",
        "framework": "Real Estate (Regulation and Development) Act, 2016",
        "authority": "State RERA authorities",
        "regions": ["India"],
        "industries": ["real_estate"],
        "severity": "high",
        "summary": "Only RERA-registered projects may be advertised, showing the registration number and RERA website.",
        "content_rules": [
            "Include the project's RERA registration number and the state RERA website in any project promotion.",
            "Do not advertise unregistered projects.",
        ],
        "required_disclaimer": None,
        "source_url": "https://mohua.gov.in/cms/TheRealEstateAct2016.php",
        **_DRAFT,
    },
    {
        "id": "uae-trakheesi-permit",
        "framework": "Dubai Land Department / RERA Trakheesi advertising permits",
        "authority": "Dubai Land Department",
        "regions": ["UAE/GCC"],
        "industries": ["real_estate"],
        "severity": "high",
        "summary": "Property ads in Dubai require a Trakheesi permit, with the permit number shown.",
        "content_rules": [
            "Include the Trakheesi advertising permit number on property listings and promotions.",
        ],
        "required_disclaimer": None,
        "source_url": "https://dubailand.gov.ae/en/",
        **_DRAFT,
    },
    # ── Alcohol ─────────────────────────────────────────────────────────────
    {
        "id": "us-alcohol-advertising",
        "framework": "TTB advertising regulations (27 CFR Parts 4, 5, 7) & industry codes",
        "authority": "Alcohol and Tobacco Tax and Trade Bureau",
        "regions": ["US"],
        "industries": ["alcohol"],
        "severity": "high",
        "summary": "Target adults 21+, include responsible-drinking messaging, no health or performance claims.",
        "content_rules": [
            "Only target audiences of legal drinking age (21+); never appeal to minors.",
            "No health benefit claims; no linking drinking to driving, athletic or sexual success.",
        ],
        "required_disclaimer": "Please enjoy responsibly. Must be 21+.",
        "source_url": "https://www.ttb.gov/advertising",
        **_DRAFT,
    },
    {
        "id": "in-alcohol-surrogate",
        "framework": "Cable Television Networks Rules (ad code) & ASCI guidelines on surrogate advertising",
        "authority": "Ministry of Information & Broadcasting / ASCI",
        "regions": ["India"],
        "industries": ["alcohol"],
        "severity": "high",
        "summary": "Direct and surrogate advertising of alcohol is prohibited.",
        "content_rules": [
            "Do not create content that promotes alcoholic beverages directly or through surrogate brand extensions.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.ascionline.in/",
        **_DRAFT,
    },
    {
        "id": "gcc-alcohol-restrictions",
        "framework": "GCC alcohol advertising restrictions",
        "authority": "National media and licensing authorities",
        "regions": ["UAE/GCC"],
        "industries": ["alcohol"],
        "severity": "high",
        "summary": "Alcohol promotion is heavily restricted in the UAE and prohibited in several GCC states.",
        "content_rules": [
            "Do not create public alcohol promotion for GCC audiences.",
        ],
        "required_disclaimer": None,
        "source_url": "https://uaemc.gov.ae/en",
        **_DRAFT,
    },
    # ── Gambling & gaming ───────────────────────────────────────────────────
    {
        "id": "us-gambling-advertising",
        "framework": "State gaming regulations & AGA Responsible Marketing Code",
        "authority": "State gaming regulators / American Gaming Association",
        "regions": ["US"],
        "industries": ["gambling_gaming"],
        "severity": "high",
        "summary": "Only where legal, 21+ audiences, with responsible-gambling messaging and no 'risk-free' claims.",
        "content_rules": [
            "Target 21+ audiences in states where the offering is legal only.",
            "Never describe betting as 'risk-free' or a way to earn income.",
        ],
        "required_disclaimer": "21+. Gambling problem? Call 1-800-GAMBLER.",
        "source_url": "https://www.americangaming.org/resources/responsible-marketing-code-for-sports-wagering/",
        **_DRAFT,
    },
    {
        "id": "in-betting-advertising",
        "framework": "MIB advisories on online betting advertisements & ASCI online gaming guidelines",
        "authority": "Ministry of Information & Broadcasting / ASCI",
        "regions": ["India"],
        "industries": ["gambling_gaming"],
        "severity": "high",
        "summary": "Advertising betting platforms is prohibited; real-money gaming ads need risk disclaimers.",
        "content_rules": [
            "Do not promote betting or gambling platforms.",
            "Real-money gaming content must not target minors or present gaming as an income source.",
        ],
        "required_disclaimer": "This game may be habit-forming or financially risky. Play responsibly.",
        "source_url": "https://mib.gov.in/",
        **_DRAFT,
    },
    {
        "id": "uae-gcgra",
        "framework": "UAE General Commercial Gaming Regulatory Authority (GCGRA)",
        "authority": "GCGRA",
        "regions": ["UAE/GCC"],
        "industries": ["gambling_gaming"],
        "severity": "high",
        "summary": "Gambling is prohibited except operators licensed by the GCGRA.",
        "content_rules": [
            "Only promote gaming activities licensed by the GCGRA; otherwise do not create gambling content.",
        ],
        "required_disclaimer": None,
        "source_url": "https://gcgra.gov.ae/",
        **_DRAFT,
    },
    # ── Food & supplements ──────────────────────────────────────────────────
    {
        "id": "us-dshea-structure-function",
        "framework": "FDA structure/function claims for dietary supplements (21 CFR 101.93)",
        "authority": "U.S. Food and Drug Administration",
        "regions": ["US"],
        "industries": ["supplements_food"],
        "severity": "high",
        "summary": "Supplement claims may describe support for body function, never disease treatment, and need the FDA disclaimer.",
        "content_rules": [
            "Use structure/function wording ('supports immune health'), never disease claims ('prevents flu').",
        ],
        "required_disclaimer": "These statements have not been evaluated by the Food and Drug Administration. This product is not intended to diagnose, treat, cure, or prevent any disease.",
        "source_url": "https://www.fda.gov/food/information-dietary-supplements-industry/structurefunction-claims",
        **_DRAFT,
    },
    {
        "id": "in-fssai-claims",
        "framework": "FSSAI (Advertising and Claims) Regulations, 2018",
        "authority": "Food Safety and Standards Authority of India",
        "regions": ["India"],
        "industries": ["supplements_food"],
        "severity": "high",
        "summary": "Food claims must be substantiated and may not claim to cure or prevent disease.",
        "content_rules": [
            "No claims that a food or supplement cures, treats or prevents disease.",
            "Health/nutrition claims must be scientifically substantiated.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.fssai.gov.in/",
        **_DRAFT,
    },
    # ── Education ───────────────────────────────────────────────────────────
    {
        "id": "us-ferpa",
        "framework": "Family Educational Rights and Privacy Act (FERPA)",
        "authority": "U.S. Department of Education",
        "regions": ["US"],
        "industries": ["education"],
        "severity": "medium",
        "summary": "Don't publish identifiable student records or information without consent.",
        "content_rules": [
            "Do not feature identifiable students, grades or records without documented consent.",
        ],
        "required_disclaimer": None,
        "source_url": "https://studentprivacy.ed.gov/",
        **_DRAFT,
    },
    {
        "id": "in-asci-education",
        "framework": "ASCI guidelines for advertising of educational institutions",
        "authority": "Advertising Standards Council of India",
        "regions": ["India"],
        "industries": ["education"],
        "severity": "medium",
        "summary": "No unsubstantiated placement, ranking or guaranteed-result claims.",
        "content_rules": [
            "Do not claim guaranteed jobs, admissions or results.",
            "Rankings and placement numbers must be verifiable and sourced.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.ascionline.in/",
        **_DRAFT,
    },
    # ── Children ────────────────────────────────────────────────────────────
    {
        "id": "us-coppa-caru",
        "framework": "COPPA & CARU Advertising Guidelines",
        "authority": "U.S. FTC / BBB National Programs (CARU)",
        "regions": ["US"],
        "industries": ["kids_products", "education"],
        "severity": "high",
        "summary": "No collecting data from children under 13 without parental consent; ads to children must not exploit them.",
        "content_rules": [
            "Do not invite children under 13 to share personal information.",
            "Do not use high-pressure tactics or blur ads and content for child audiences.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.ftc.gov/legal-library/browse/rules/childrens-online-privacy-protection-rule-coppa",
        **_DRAFT,
    },
    {
        "id": "in-asci-children",
        "framework": "ASCI Code - advertising to children",
        "authority": "Advertising Standards Council of India",
        "regions": ["India"],
        "industries": ["kids_products"],
        "severity": "medium",
        "summary": "Ads must not exploit children's credulity or encourage unsafe behaviour.",
        "content_rules": [
            "Do not suggest a child is inferior without the product, or show unsafe behaviour.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.ascionline.in/the-asci-code/",
        **_DRAFT,
    },
    # ── Employment ──────────────────────────────────────────────────────────
    {
        "id": "us-eeo-job-ads",
        "framework": "Title VII, ADEA & ADA - discriminatory job advertising",
        "authority": "U.S. Equal Employment Opportunity Commission",
        "regions": ["US"],
        "industries": ["employment_recruiting"],
        "severity": "high",
        "summary": "Job ads may not express preferences based on protected characteristics.",
        "content_rules": [
            "No age, sex, religion, national-origin or disability preferences (e.g. 'young', 'digital native', 'recent graduates only').",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.eeoc.gov/prohibited-employment-policiespractices",
        **_DRAFT,
    },
    {
        "id": "uae-labour-non-discrimination",
        "framework": "Federal Decree-Law No. 33 of 2021 on Regulation of Labour Relations (Art. 4)",
        "authority": "UAE Ministry of Human Resources & Emiratisation",
        "regions": ["UAE/GCC"],
        "industries": ["employment_recruiting"],
        "severity": "high",
        "summary": "Job ads may not discriminate on race, colour, sex, religion, national or social origin, or disability.",
        "content_rules": [
            "Do not state preferences for nationality, sex, religion or other protected grounds in job posts.",
        ],
        "required_disclaimer": None,
        "source_url": "https://www.mohre.gov.ae/en/laws-and-regulations",
        **_DRAFT,
    },
]

_RULES_BY_ID = {rule["id"]: rule for rule in RULES}


def normalize_industry(value: str | None) -> str:
    return value if value in INDUSTRIES else "general"


def normalize_regions(values) -> list[str]:
    return [r for r in REGIONS if r in (values or [])]


def industry_from_schema_types(schema_types) -> str | None:
    """First industry implied by the schema.org types a site declares."""
    for schema_type in schema_types or []:
        if schema_type in SCHEMA_TYPE_INDUSTRIES:
            return SCHEMA_TYPE_INDUSTRIES[schema_type]
    return None


def applicable_rules(industry: str | None, regions, excluded_ids=()) -> list[dict]:
    """Rules for this industry (plus every-industry "*" rules) in these
    regions, high severity first. Rules in excluded_ids (user marked not
    applicable) are returned with "excluded": True so the UI can show and
    re-enable them; callers enforcing rules must skip those."""
    industry = normalize_industry(industry)
    regions = set(normalize_regions(regions))
    excluded = set(excluded_ids or [])
    severity_order = {"high": 0, "medium": 1, "low": 2}

    selected = [
        {**rule, "excluded": rule["id"] in excluded}
        for rule in RULES
        if regions.intersection(rule["regions"]) and ("*" in rule["industries"] or industry in rule["industries"])
    ]
    return sorted(selected, key=lambda r: (severity_order.get(r["severity"], 3), "*" in r["industries"]))


def rule_by_id(rule_id: str) -> dict | None:
    return _RULES_BY_ID.get(rule_id)
